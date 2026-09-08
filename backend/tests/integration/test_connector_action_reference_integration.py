"""
KORTEX OS — Milestone F5 Test Suite
Connector Action reference integration: the full profile -> secret -> driver -> action ->
capability -> result chain against the real `HttpRestConnectorDriver` (network mocked, production
execution path unmodified), Workflow Engine `capability_name` compatibility, and legacy
compatibility of the pre-existing M7.3 connector surface through the real production boot path.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.actions import ConnectorActionBootstrapEngine
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.drivers.http_driver import HttpRestConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.models import ConnectorProfile
from kortex.engines.connector.reference_actions import REFERENCE_ACTION_DESCRIPTORS
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.models import (
    WorkflowDefinition,
    WorkflowState,
    WorkflowStep,
)

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32
_ROLE = "F5_REFERENCE_INTEGRATION_ROLE"
_TENANT = "f5_reference_tenant"


class _MockStreamResponse:
    """Mirrors `test_http_connector_driver.py`'s own `MockStreamResponse` helper exactly -- the
    established, proven mock shape for `httpx.AsyncClient.stream`, reused rather than reinvented."""

    def __init__(self, chunks: list[bytes], status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.chunks = chunks
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()


# ============================================================================
# A. Full reference chain: profile -> secret -> HttpRestConnectorDriver -> action -> capability
# ============================================================================


@pytest.fixture
async def http_kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, object]]:
    db_path = (tmp_path / f"kortex_f5_ref_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_f5_ref_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    kernel.register_engine(ConnectorActionBootstrapEngine(REFERENCE_ACTION_DESCRIPTORS))

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_ref",
                principal_type="USER",
                credential_hash=hasher.hash("pass-ref"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)

    connector_engine.register_driver(HttpRestConnectorDriver())
    await security_engine.put_secret("vault:webhook-token", _TENANT, "real-webhook-bearer-token")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="webhook-profile",
            tenant_id=_TENANT,
            name="Reference Webhook",
            driver_id="connector-http-rest",
            secret_handle="vault:webhook-token",
            options={"base_url": "https://api.example.com"},
        )
    )

    try:
        yield kernel, connector_engine
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


async def _token(kernel: Kernel, tenant_id: str, principal_id: str, password: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    return await security_engine.authentication_manager.issue_token(principal)


@pytest.mark.asyncio
async def test_reference_read_action_full_chain(http_kernel_env: tuple[Kernel, object]) -> None:
    """profile -> secret -> HttpRestConnectorDriver -> FETCH action -> capability -> result."""
    kernel, _ = http_kernel_env
    token = await _token(kernel, _TENANT, "user_ref", "pass-ref")

    resp_bytes = json.dumps({"delivered": True}).encode("utf-8")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]),
        patch.object(httpx.AsyncClient, "stream") as mock_stream,
    ):
        mock_stream.return_value = _MockStreamResponse([resp_bytes], status_code=200)
        result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.notification.webhook.status",
                session_token=token,
                parameters={"profile_id": "webhook-profile", "url": "https://api.example.com/status/abc"},
                context={"resource_tenant_id": _TENANT},
            )
        )

    assert result["status_code"] == 200
    assert result["body"] == {"delivered": True}
    # the resolved secret was sent as a real Authorization header, never returned to the caller.
    call_kwargs = mock_stream.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer real-webhook-bearer-token"
    assert "real-webhook-bearer-token" not in str(result)


@pytest.mark.asyncio
async def test_reference_mutation_action_full_chain(http_kernel_env: tuple[Kernel, object]) -> None:
    """profile -> secret -> HttpRestConnectorDriver -> SEND action -> capability -> result."""
    kernel, _ = http_kernel_env
    token = await _token(kernel, _TENANT, "user_ref", "pass-ref")

    resp_bytes = json.dumps({"accepted": True}).encode("utf-8")
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]),
        patch.object(httpx.AsyncClient, "stream") as mock_stream,
    ):
        mock_stream.return_value = _MockStreamResponse([resp_bytes], status_code=201)
        result = await kernel.invoke_capability(
            CapabilityRequest(
                capability_name="kortex.connector.notification.webhook.send",
                session_token=token,
                parameters={
                    "profile_id": "webhook-profile",
                    "url": "https://api.example.com/notify",
                    "body": {"message": "hello"},
                },
                context={"resource_tenant_id": _TENANT},
            )
        )

    assert result["status_code"] == 201
    assert result["body"] == {"accepted": True}
    call_kwargs = mock_stream.call_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert json.loads(call_kwargs["content"]) == {"message": "hello"}


# ============================================================================
# B. Workflow Engine `capability_name` compatibility (D12) — zero executor changes
# ============================================================================


@pytest.fixture
async def workflow_kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, WorkflowEngine]]:
    db_path = (tmp_path / f"kortex_f5_wf_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_f5_wf_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine(data_store=data_store)
    workflow_engine = WorkflowEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    kernel.register_engine(ConnectorActionBootstrapEngine(REFERENCE_ACTION_DESCRIPTORS))
    kernel.register_engine(workflow_engine)

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="workflow:start"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT,
                principal_id="user_wf",
                principal_type="USER",
                credential_hash=hasher.hash("pass-wf"),
                roles=[_ROLE],
                attributes={"clearance_level": "INTERNAL"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed)

    connector_engine.register_driver(DummyConnectorDriver())
    await security_engine.put_secret("vault:wf-secret", _TENANT, "wf-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="wf-profile",
            tenant_id=_TENANT,
            name="WF",
            driver_id="connector-dummy",
            secret_handle="vault:wf-secret",
        )
    )

    try:
        yield kernel, workflow_engine
    finally:
        if kernel.state == KernelState.RUNNING:
            await kernel.shutdown()
        await db_manager.disconnect()


@pytest.mark.asyncio
async def test_workflow_step_invokes_connector_action_capability_with_zero_executor_changes(
    workflow_kernel_env: tuple[Kernel, WorkflowEngine],
) -> None:
    kernel, workflow_engine = workflow_kernel_env
    token_dict, _ = await _issue_session_token_dict(kernel, _TENANT, "user_wf", "pass-wf")

    definition = WorkflowDefinition(
        id="f5-reference-workflow",
        name="F5 Reference Workflow",
        tenant_id=_TENANT,
        steps=[
            WorkflowStep(
                id="s1",
                name="Check webhook status",
                capability_name="kortex.connector.notification.webhook.status",
                parameters={
                    "profile_id": "wf-profile",
                    "url": "https://api.example.com/status/1",
                    # Required so the Kernel's ABAC tenant check has a `resource_tenant_id` to
                    # verify against -- `StepEvaluator.execute_step` pops this reserved key out of
                    # `parameters` before dispatch (evaluator.py), an existing, unmodified contract
                    # every authenticated capability a workflow step invokes already relies on.
                    "_authz_context": {"resource_tenant_id": _TENANT},
                },
            )
        ],
    )
    await workflow_engine.register_definition_async(definition, tenant_id=_TENANT)

    instance = await workflow_engine.start_workflow(
        "f5-reference-workflow", tenant_id=_TENANT, session_token=token_dict
    )

    completed = None
    for _ in range(60):
        current = workflow_engine.get_instance(instance.id)
        if current.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            completed = current
            break
        await asyncio.sleep(0.05)

    assert completed is not None
    assert completed.state == WorkflowState.COMPLETED
    step_output = completed.context.step_outputs["s1"]
    assert step_output["status"] == "executed"
    assert step_output["mock_driver_id"] == "connector-dummy"


async def _issue_session_token_dict(kernel: Kernel, tenant_id: str, principal_id: str, password: str):
    security_engine: SecurityEngine = kernel.get_engine("security")
    principal = await security_engine.authentication_manager.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": password}
    )
    token = await security_engine.authentication_manager.issue_token(principal)
    token_dict = token.model_dump()
    if token_dict.get("signature") is not None:
        token_dict["signature"] = token_dict["signature"].hex()
    return token_dict, tenant_id


# ============================================================================
# C. Legacy compatibility through the real production boot path (I)
# ============================================================================


@pytest.mark.asyncio
async def test_production_boot_registers_both_legacy_and_f5_connector_capabilities(tmp_path: Path) -> None:
    """The real `build_and_boot_kernel()` production wiring registers F5's connector action
    capabilities and their generated AI tools additively, alongside the pre-existing M7.3 generic
    capability and its two hand-authored AI tools -- neither is affected by the other."""
    import os

    os.environ["KORTEX_STORAGE_DIR"] = str(tmp_path / "prod_boot_storage")
    from kortex.api.kernel_bootstrap import build_and_boot_kernel

    kernel = await build_and_boot_kernel()
    try:
        names = {c.name for c in kernel.list_capabilities()}
        assert "kortex.connector.action.execute" in names
        assert "kortex.connector.notification.webhook.status" in names
        assert "kortex.connector.notification.webhook.send" in names

        ai_engine = kernel.get_engine("ai")
        tool_names = {t.name for t in ai_engine.tool_registry.list_tools()}
        assert "connector_read_status" in tool_names
        assert "connector_send_action" in tool_names
        assert "kortex_connector_notification_webhook_status" in tool_names
        assert "kortex_connector_notification_webhook_send" in tool_names
    finally:
        await kernel.shutdown()
