"""
KORTEX OS — Milestone F5 Test Suite
Connector Action Semantic Capability Bridge: descriptor validation, the generic dispatch adapter,
Kernel registration/discovery, risk classification, and security boundaries (tenant isolation,
authorization, credential resolution, secret non-leakage).
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ResourceAlreadyExistsError
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.connector.actions import (
    ConnectorActionBootstrapEngine,
    ConnectorActionDescriptor,
    ConnectorActionFailedError,
    ConnectorActionValidationError,
    _build_full_parameters_schema,
    make_action_handler,
    register_action_capabilities,
)
from kortex.engines.connector.drivers.dummy_driver import DummyConnectorDriver
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorProfileNotFoundError
from kortex.engines.connector.models import ActionResult, ConnectorActionType, ConnectorProfile
from kortex.engines.connector.reference_actions import (
    REFERENCE_ACTION_DESCRIPTORS,
    WEBHOOK_SEND_ACTION,
    WEBHOOK_STATUS_ACTION,
)
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x33" * 32
_TEST_SIGNING_KEY = b"\x44" * 32
_ROLE = "F5_CONNECTOR_ACTION_TEST_ROLE"
_NOPERM_ROLE = "F5_CONNECTOR_ACTION_NOPERM_ROLE"
_TENANT_A = "f5_tenant_alpha"
_TENANT_B = "f5_tenant_beta"

# ============================================================================
# A. Descriptor / schema tests (no Kernel needed)
# ============================================================================


def test_descriptor_requires_is_read_only_and_is_idempotent_explicitly() -> None:
    with pytest.raises(ValidationError):
        ConnectorActionDescriptor(  # type: ignore[call-arg]
            capability_name="kortex.connector.test.thing.read",
            description="x",
            connector_action_type=ConnectorActionType.FETCH,
        )


def test_descriptor_is_frozen() -> None:
    with pytest.raises(ValidationError):
        WEBHOOK_STATUS_ACTION.description = "changed"  # type: ignore[misc]


def test_descriptor_rejects_non_dict_schema() -> None:
    with pytest.raises(ValidationError):
        ConnectorActionDescriptor(
            capability_name="kortex.connector.test.thing.read",
            description="x",
            connector_action_type=ConnectorActionType.FETCH,
            parameters_schema="not-a-dict",  # type: ignore[arg-type]
            is_read_only=True,
            is_idempotent=True,
        )


def test_build_full_parameters_schema_prepends_profile_id() -> None:
    schema = _build_full_parameters_schema(WEBHOOK_STATUS_ACTION)
    assert schema["required"][0] == "profile_id"
    assert "profile_id" in schema["properties"]
    assert "url" in schema["properties"]
    # the descriptor's own schema is untouched -- profile_id was never part of it
    assert "profile_id" not in WEBHOOK_STATUS_ACTION.parameters_schema.get("properties", {})


def test_reference_descriptors_have_deterministic_unique_identity() -> None:
    names = [d.capability_name for d in REFERENCE_ACTION_DESCRIPTORS]
    assert names == [
        "kortex.connector.notification.webhook.status",
        "kortex.connector.notification.webhook.send",
    ]
    assert len(names) == len(set(names))


def test_reference_action_returns_schema_is_valid_and_non_empty() -> None:
    for descriptor in REFERENCE_ACTION_DESCRIPTORS:
        assert descriptor.returns_schema.get("type") == "object"
        assert "status_code" in descriptor.returns_schema.get("properties", {})


# ============================================================================
# B. Risk classification (F)
# ============================================================================


def test_status_action_is_read_only_and_idempotent() -> None:
    assert WEBHOOK_STATUS_ACTION.is_read_only is True
    assert WEBHOOK_STATUS_ACTION.is_idempotent is True
    assert WEBHOOK_STATUS_ACTION.connector_action_type == ConnectorActionType.FETCH


def test_send_action_is_mutation_and_not_idempotent() -> None:
    assert WEBHOOK_SEND_ACTION.is_read_only is False
    assert WEBHOOK_SEND_ACTION.is_idempotent is False
    assert WEBHOOK_SEND_ACTION.connector_action_type == ConnectorActionType.SEND


# ============================================================================
# C. Dispatch adapter tests (stub connector engine, no real Kernel) (D)
# ============================================================================


class _StubConnectorEngine:
    """Minimal stand-in proving `make_action_handler` never touches anything beyond
    `execute_action` -- no driver, no profile manager, no secret resolver reference at all."""

    name = "connector"

    def __init__(self, outcome: ActionResult | Exception) -> None:
        self._outcome = outcome
        self.received_requests: list[tuple[object, object]] = []

    async def execute_action(self, request: object, principal: object = None) -> ActionResult:
        self.received_requests.append((request, principal))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


@pytest.mark.asyncio
async def test_handler_missing_required_parameter_raises_validation_error() -> None:
    stub = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))
    handler = make_action_handler(WEBHOOK_STATUS_ACTION, stub)  # type: ignore[arg-type]
    with pytest.raises(ConnectorActionValidationError):
        await handler(profile_id="p1")  # missing required "url"
    assert stub.received_requests == []  # never reached ConnectorEngine at all


@pytest.mark.asyncio
async def test_handler_builds_action_request_correctly_and_returns_response_payload() -> None:
    stub = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={"status_code": 200}))
    handler = make_action_handler(WEBHOOK_STATUS_ACTION, stub)  # type: ignore[arg-type]
    result = await handler(profile_id="p1", url="/status/1")

    assert result == {"status_code": 200}
    request, principal = stub.received_requests[0]
    assert request.profile_id == "p1"  # type: ignore[attr-defined]
    assert request.action_type == ConnectorActionType.FETCH  # type: ignore[attr-defined]
    assert request.payload == {"url": "/status/1"}  # type: ignore[attr-defined]
    assert principal is None


@pytest.mark.asyncio
async def test_handler_raises_action_failed_on_non_success_result() -> None:
    stub = _StubConnectorEngine(ActionResult(request_id="x", status="FAILED", error_details={"error": "boom"}))
    handler = make_action_handler(WEBHOOK_SEND_ACTION, stub)  # type: ignore[arg-type]
    with pytest.raises(ConnectorActionFailedError, match="boom"):
        await handler(profile_id="p1", url="/x", body={"a": 1})


@pytest.mark.asyncio
async def test_handler_never_reads_a_caller_supplied_tenant_id() -> None:
    """D17: no caller-controlled `tenant_id` parameter exists on the handler at all -- an
    attacker-supplied value lands (inertly) in the connector payload, never in `ActionRequest.
    tenant_id`, which stays at its own model default until `ConnectorEngine.execute_action`'s own
    principal-authoritative override (exercised separately in the full-dispatch tests below)."""
    assert (
        "tenant_id"
        not in inspect.signature(
            make_action_handler(
                WEBHOOK_STATUS_ACTION,
                _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={})),
            )
        ).parameters
    )  # type: ignore[arg-type]

    stub = _StubConnectorEngine(ActionResult(request_id="x", status="SUCCESS", response_payload={}))
    handler = make_action_handler(WEBHOOK_STATUS_ACTION, stub)  # type: ignore[arg-type]
    await handler(profile_id="p1", url="/x", tenant_id="attacker-supplied-tenant")
    request, _ = stub.received_requests[0]
    assert request.tenant_id == "default"  # type: ignore[attr-defined]


# ============================================================================
# D. Registration tests against a bare Kernel (no boot needed) (B)
# ============================================================================


def test_duplicate_action_capability_registration_rejected() -> None:
    kernel = Kernel()
    connector_engine = ConnectorEngine()
    with pytest.raises(ResourceAlreadyExistsError):
        register_action_capabilities(kernel, connector_engine, [WEBHOOK_STATUS_ACTION, WEBHOOK_STATUS_ACTION])


def test_registration_is_deterministic_across_two_kernels() -> None:
    caps_1 = []
    caps_2 = []
    for caps in (caps_1, caps_2):
        kernel = Kernel()
        connector_engine = ConnectorEngine()
        register_action_capabilities(kernel, connector_engine, REFERENCE_ACTION_DESCRIPTORS)
        for name in ("kortex.connector.notification.webhook.status", "kortex.connector.notification.webhook.send"):
            d = kernel.get_capability(name)
            caps.append((d.name, d.is_read_only, d.is_idempotent, d.parameters_schema, d.returns_schema))
    assert caps_1 == caps_2


# ============================================================================
# E. Full dispatch / security tests (real Kernel, SecurityEngine, ConnectorEngine)
# ============================================================================


@pytest.fixture
async def kernel_env(tmp_path: Path) -> AsyncIterator[tuple[Kernel, ConnectorEngine]]:
    db_path = (tmp_path / f"kortex_f5_actions_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager
    data_store = RelationalDataStore(db_manager)

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_f5_actions_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    connector_engine = ConnectorEngine(data_store=data_store)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(connector_engine)
    kernel.register_engine(ConnectorActionBootstrapEngine(REFERENCE_ACTION_DESCRIPTORS))

    hasher = PasswordHasher()

    async def _seed_rbac(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:execute"))
        session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission="connector:read"))
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_a",
                principal_type="USER",
                credential_hash=hasher.hash("pass-a"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_B,
                principal_id="user_b",
                principal_type="USER",
                credential_hash=hasher.hash("pass-b"),
                roles=[_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )
        session.add(
            PrincipalRecord(
                id=str(uuid4()),
                tenant_id=_TENANT_A,
                principal_id="user_a_noperm",
                principal_type="USER",
                credential_hash=hasher.hash("pass-noperm"),
                roles=[_NOPERM_ROLE],
                attributes={"clearance_level": "RESTRICTED"},
            )
        )

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    await storage_engine.data.execute_in_transaction(_seed_rbac)

    connector_engine.register_driver(DummyConnectorDriver())

    await security_engine.put_secret("vault:f5-secret", _TENANT_A, "tenant-a-secret-value")
    await security_engine.put_secret("vault:f5-secret", _TENANT_B, "tenant-b-secret-value")
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-a",
            tenant_id=_TENANT_A,
            name="A",
            driver_id="connector-dummy",
            secret_handle="vault:f5-secret",
        )
    )
    await connector_engine.profile_manager.register_profile(
        ConnectorProfile(
            profile_id="prof-b",
            tenant_id=_TENANT_B,
            name="B",
            driver_id="connector-dummy",
            secret_handle="vault:f5-secret",
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


async def _invoke(kernel: Kernel, capability_name: str, token: object, tenant_id: str, **parameters: object):
    return await kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability_name,
            session_token=token,  # type: ignore[arg-type]
            parameters=parameters,
            context={"resource_tenant_id": tenant_id},
        )
    )


@pytest.mark.asyncio
async def test_action_capabilities_appear_in_list_and_search(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    kernel, _ = kernel_env
    names = {c.name for c in kernel.list_capabilities()}
    assert "kortex.connector.notification.webhook.status" in names
    assert "kortex.connector.notification.webhook.send" in names
    hits = {c.name for c in kernel.search_capabilities(resource_type="notification.webhook")}
    assert hits == {"kortex.connector.notification.webhook.status", "kortex.connector.notification.webhook.send"}
    keyword_hits = {c.name for c in kernel.search_capabilities(keyword="webhook")}
    assert hits == keyword_hits


@pytest.mark.asyncio
async def test_action_capability_metadata_is_complete(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    kernel, _ = kernel_env
    cap = kernel.get_capability("kortex.connector.notification.webhook.status")
    assert cap.is_read_only is True
    assert cap.is_idempotent is True
    assert cap.parameters_schema
    assert cap.returns_schema
    assert cap.required_permissions == ["connector:execute"]
    assert cap.requires_execution_context is True
    assert cap.owner_domain == "connector"
    assert cap.resource_type == "notification.webhook"
    assert cap.action == "status"


@pytest.mark.asyncio
async def test_dispatch_executes_through_connector_engine_with_resolved_secret(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    kernel, _ = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_a", "pass-a")
    result = await _invoke(
        kernel, "kortex.connector.notification.webhook.status", token_a, _TENANT_A, profile_id="prof-a", url="/status/1"
    )
    assert result["status"] == "executed"
    assert result["mock_driver_id"] == "connector-dummy"
    assert result["secret_authenticated"] is True


@pytest.mark.asyncio
async def test_secret_value_never_appears_in_capability_result(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    kernel, _ = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_a", "pass-a")
    result = await _invoke(
        kernel, "kortex.connector.notification.webhook.status", token_a, _TENANT_A, profile_id="prof-a", url="/status/1"
    )
    serialized = str(result)
    assert "tenant-a-secret-value" not in serialized
    assert "tenant-b-secret-value" not in serialized


@pytest.mark.asyncio
async def test_secret_value_never_appears_in_capability_or_tool_metadata(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    kernel, _ = kernel_env
    cap = kernel.get_capability("kortex.connector.notification.webhook.status")
    serialized = f"{cap.description} {cap.parameters_schema} {cap.returns_schema}"
    assert "tenant-a-secret-value" not in serialized
    assert "tenant-b-secret-value" not in serialized
    assert "vault:f5-secret" not in serialized


@pytest.mark.asyncio
async def test_cross_tenant_profile_access_denied(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    """A: tenant B cannot reach tenant A's connector profile by name, even holding the same
    permission -- `ConnectorProfileManager`'s existing enumeration-resistant tenant masking
    applies unchanged to an F5 action capability."""
    kernel, _ = kernel_env
    token_b = await _token(kernel, _TENANT_B, "user_b", "pass-b")
    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(
            kernel,
            "kortex.connector.notification.webhook.status",
            token_b,
            _TENANT_B,
            profile_id="prof-a",
            url="/status/1",
        )


@pytest.mark.asyncio
async def test_missing_permission_denied(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    """C/M: a principal without `connector:execute` cannot invoke a connector action capability."""
    kernel, _ = kernel_env
    token = await _token(kernel, _TENANT_A, "user_a_noperm", "pass-noperm")
    with pytest.raises(AuthorizationDeniedError):
        await _invoke(
            kernel,
            "kortex.connector.notification.webhook.status",
            token,
            _TENANT_A,
            profile_id="prof-a",
            url="/status/1",
        )


@pytest.mark.asyncio
async def test_action_parameters_cannot_override_profile_resolution(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    """K: extra/malicious action parameters can never redirect execution to a different profile,
    driver, or secret -- only the explicit `profile_id` parameter is ever used for resolution."""
    kernel, _ = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_a", "pass-a")
    result = await _invoke(
        kernel,
        "kortex.connector.notification.webhook.status",
        token_a,
        _TENANT_A,
        profile_id="prof-a",
        url="/status/1",
        secret_handle="prof-b",
        driver_id="something-else",
    )
    # the smuggled keys land, inertly, inside the echoed payload -- they never influenced which
    # profile/driver/secret actually executed the action.
    assert result["echo_payload"]["secret_handle"] == "prof-b"
    assert result["mock_driver_id"] == "connector-dummy"
    assert result["secret_authenticated"] is True


@pytest.mark.asyncio
async def test_missing_connector_profile_raises_not_found(kernel_env: tuple[Kernel, ConnectorEngine]) -> None:
    """L: a nonexistent profile_id fails cleanly, not silently."""
    kernel, _ = kernel_env
    token_a = await _token(kernel, _TENANT_A, "user_a", "pass-a")
    with pytest.raises(ConnectorProfileNotFoundError):
        await _invoke(
            kernel,
            "kortex.connector.notification.webhook.status",
            token_a,
            _TENANT_A,
            profile_id="does-not-exist",
            url="/x",
        )


@pytest.mark.asyncio
async def test_legacy_generic_connector_capability_still_registered(
    kernel_env: tuple[Kernel, ConnectorEngine],
) -> None:
    """I: the pre-existing M7.3 generic capability is completely unaffected by F5's additive
    per-action capabilities."""
    kernel, _ = kernel_env
    names = {c.name for c in kernel.list_capabilities()}
    assert "kortex.connector.action.execute" in names
    assert "kortex.connector.profile.get" in names
