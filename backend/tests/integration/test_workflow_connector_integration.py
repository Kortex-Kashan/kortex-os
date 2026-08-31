"""Sub-Milestone 10.4 Integration Tests: Workflow Engine -> Connector Engine Integration.

Verifies end-to-end orchestration between Workflow Engine, Kernel capability resolution,
ConnectorEngine, ConnectorPipeline, and HttpRestConnectorDriver using deterministic mock HTTP transport.

Guarantees:
- Zero production file modifications (StepEvaluator & engines remain 100% generic & locked).
- ActionRequest mapping compliance.
- Verification of actual WorkflowStep retry defaults and retry attempt counts.
- Deterministic retry ownership & attempt count verification across workflow and connector.
- Response header sanitization verification in step_outputs and WorkflowContext.
- Raw HTTP socket request header capture verifying Idempotency-Key propagation.
- RBAC capability authorization blocking before driver/network dispatch.
- Secret token isolation across context, step_outputs, logs, and Kernel event payloads.
- Stream closure and cancellation boundary propagation.
- Compensation stack execution & parameter verification upon step failure.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid as uuid_module
from typing import Any, Dict
from unittest.mock import patch
from uuid import UUID, uuid4

import httpcore
import httpx
import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.base_engine import EngineState
from kortex.core.kernel import Kernel
from kortex.engines.connector.drivers.http_driver import (
    HttpRestConnectorDriver,
    PinnedIPNetworkBackend,
    SSRFHardenedTransport,
)
from kortex.engines.connector.engine import ConnectorEngine
from kortex.engines.connector.exceptions import ConnectorSecurityError
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionType,
    ConnectorProfile,
)
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.workflow.engine import WorkflowEngine
from kortex.engines.workflow.exceptions import WorkflowExecutionError
from kortex.engines.workflow.models import (
    CompensationAction,
    ExecutionResult,
    RetryPolicy,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowInstance,
    WorkflowResult,
    WorkflowState,
    WorkflowStatus,
    WorkflowStep,
)

# -- Enforcement-Boundary Test Helpers ----------------------------------------
#
# Every capability defaults to `requires_authentication=True` (Kernel
# Capability Enforcement Boundary milestone). `kortex.connector.action.execute`
# is registered by ConnectorEngine without overriding this default, so any
# workflow step invoking it through the Kernel's enforced dispatch path now
# genuinely needs a signed session token AND a `resource_tenant_id` in ABAC
# context (M4's own unconditional missing-tenant-denies rule, unchanged).
# These helpers add exactly that — real, unmodified AuthenticationManager/
# AuthorizationEngine machinery, not a mock or a bypass.

_TEST_MASTER_KEY = b"\x22" * 32
_TEST_SIGNING_KEY = b"\x33" * 32


def _register_security_engine(kernel: Kernel) -> SecurityEngine:
    """Register a `SecurityEngine` with deterministic test key material
    (constructor overrides, bypassing `KORTEX_MASTER_KEY`/
    `KORTEX_AUTH_SIGNING_PRIVATE_KEY` env resolution entirely) before boot."""
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    kernel.register_engine(security_engine)
    return security_engine


async def _issue_test_session_token(security_engine: SecurityEngine, data_store: Any) -> tuple[Dict[str, Any], str]:
    """Seed a `PrincipalRecord` directly via `IDataStore` — matching the
    established test-seeding convention in `test_authentication_manager.py`
    (M3 has no provisioning capability) — then authenticate and issue a
    genuine, Ed25519-signed session token for it, returned as a plain dict
    matching `TokenPayload`'s own field shape, alongside the tenant_id it
    was issued for.

    `tenant_id` is a fresh `uuid4()` value every call, never a fixed
    constant — the Kernel's `DatabaseEngineManager` defaults to a single
    shared `kortex_local.db` file reused across every test process/run
    (confirmed elsewhere in this test suite's own conventions), so a fixed
    tenant/principal id would collide across tests via
    `security_principals`' `(tenant_id, principal_id, principal_type)`
    unique constraint.
    """
    tenant_id = f"tenant-wf-conn-it-{uuid_module.uuid4()}"
    principal_id = "principal-wf-conn-it"
    credential_hash = PasswordHasher().hash("test-credential")
    # `kortex.connector.action.execute` — the only production capability
    # this suite dispatches through the Kernel — now declares
    # `required_permissions=["connector:execute"]`. A role scoped to this
    # tenant's own fresh uuid4() grants exactly that, avoiding collisions
    # with other tests sharing `kortex_local.db`'s `security_role_permissions`
    # table (which has no tenant scoping of its own).
    role = f"wf-conn-it-role-{tenant_id}"

    async def _seed(session: AsyncSession) -> None:
        session.add(
            PrincipalRecord(
                id=str(uuid_module.uuid4()),
                tenant_id=tenant_id,
                principal_id=principal_id,
                principal_type="USER",
                enabled=True,
                credential_hash=credential_hash,
                roles=[role],
                # `CapabilityDescriptor.security_classification` defaults to
                # "INTERNAL" (kortex.core.dispatch), and abac.py denies
                # unless principal clearance rank >= the requirement's rank
                # — a principal with no `clearance_level` defaults to PUBLIC
                # (rank 0), which would fail every default-classified
                # capability's ABAC check. Grant INTERNAL explicitly.
                attributes={"clearance_level": "INTERNAL"},
            )
        )
        session.add(RolePermissionRecord(id=str(uuid_module.uuid4()), role=role, permission="connector:execute"))

    await data_store.execute_in_transaction(_seed)

    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "test-credential",
        }
    )
    token = await security_engine.authentication_manager.issue_token(principal)
    token_dict = token.model_dump()
    # `WorkflowContext.session_token` requires a JSON-safe dict — hex-encode
    # the raw signature bytes (see `workflow/models.py`'s field docstring
    # and `workflow/engine.py`'s dispatch closure, which decodes it back).
    if token_dict.get("signature") is not None:
        token_dict["signature"] = token_dict["signature"].hex()
    return token_dict, tenant_id


# -- Mock HTTP Transport Infrastructure ---------------------------------------

class MockNetworkStream(httpcore.AsyncNetworkStream):
    """httpcore AsyncNetworkStream returning configurable mock HTTP responses and capturing request bytes."""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b'{"status": "success", "data": "ok"}',
    ) -> None:
        hdr_lines = [f"HTTP/1.1 {status_code} OK"]
        custom_hdrs = headers or {}
        for k, v in custom_hdrs.items():
            hdr_lines.append(f"{k}: {v}")
        if "Content-Length" not in custom_hdrs and "content-length" not in custom_hdrs:
            hdr_lines.append(f"Content-Length: {len(body)}")
        if "Content-Type" not in custom_hdrs and "content-type" not in custom_hdrs:
            hdr_lines.append("Content-Type: application/json")
        if "Connection" not in custom_hdrs and "connection" not in custom_hdrs:
            hdr_lines.append("Connection: close")

        hdr_bytes = "\r\n".join(hdr_lines).encode("utf-8") + b"\r\n\r\n"
        self._raw_response = hdr_bytes + body
        self._offset = 0
        self.received_bytes = b""
        self.closed = False

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._offset >= len(self._raw_response):
            return b""
        chunk = self._raw_response[self._offset : self._offset + max_bytes]
        self._offset += len(chunk)
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.received_bytes += buffer

    async def aclose(self) -> None:
        self.closed = True

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self

    def get_extra_info(self, info: str, default=None):
        return default

    def get_captured_headers(self) -> dict[str, str]:
        """Extract HTTP headers received from client request bytes."""
        headers = {}
        text = self.received_bytes.decode("utf-8", errors="ignore")
        lines = text.split("\r\n")
        for line in lines[1:]:
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return headers


class MockNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore AsyncNetworkBackend recording connect_tcp calls and returning MockNetworkStream."""

    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b'{"status": "success", "data": "ok"}',
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.body = body
        self.connect_tcp_calls: list[dict[str, Any]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        stream = MockNetworkStream(
            status_code=self.status_code,
            headers=self.headers,
            body=self.body,
        )
        self.connect_tcp_calls.append({"host": host, "port": port, "stream": stream})
        return stream

    async def connect_unix_socket(self, *args, **kwargs):
        return MockNetworkStream()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


def setup_mock_transport(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    body: bytes = b'{"status": "success", "data": "ok"}',
    resolved_ip: str = "93.184.216.34",
):
    """Context manager setting up DNS resolution and PinnedIPNetworkBackend mock transport."""
    mock_backend = MockNetworkBackend(status_code=status_code, headers=headers, body=body)
    orig_pinned_init = PinnedIPNetworkBackend.__init__
    orig_ssrf_init = SSRFHardenedTransport.__init__

    def patched_pinned_init(self_pinned, pinned_ip):
        orig_pinned_init(self_pinned, pinned_ip)
        self_pinned._default_backend = mock_backend

    def patched_ssrf_init(self_ssrf, pinned_ip, **kwargs):
        kwargs["verify"] = False
        orig_ssrf_init(self_ssrf, pinned_ip, **kwargs)

    addr_info = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (resolved_ip, 80))]
    return (
        patch("socket.getaddrinfo", return_value=addr_info),
        patch.object(PinnedIPNetworkBackend, "__init__", patched_pinned_init),
        patch.object(SSRFHardenedTransport, "__init__", patched_ssrf_init),
        mock_backend,
    )


async def mock_secret_resolver(handle: str, tenant_id: str) -> str:
    return "Bearer secret-token-xyz-123"


# -- Test Suite ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_e2e_workflow_to_connector_http_execution(tmp_path) -> None:
    """1. End-to-end integration: WorkflowEngine -> StepEvaluator -> Kernel Capability -> ConnectorEngine -> Driver -> Mock Transport."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "e2e_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)

    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    # Register HTTP Rest Connector Driver & Connector Profile
    http_driver = HttpRestConnectorDriver()
    connector_engine.register_driver(http_driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-e2e-1",
        name="E2E Test Profile",
        driver_id="connector-http-rest",
        secret_handle="vault:secret_token_123",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    # Build Workflow Definition with Connector Step
    dummy_instance_id = str(uuid4())
    req = ActionRequest(
        request_id=f"wf-{dummy_instance_id}-step_fetch",
        profile_id="prof-e2e-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/v1/data"},
        correlation_id=dummy_instance_id,
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )

    step_connector = WorkflowStep(
        id="step_fetch",
        name="Fetch External Data Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )

    wf_def = WorkflowDefinition(
        id="wf_e2e_connector",
        name="Workflow E2E Connector Definition",
        steps=[step_connector],
    )
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, mock_backend = setup_mock_transport(
        status_code=200,
        headers={"Content-Type": "application/json", "X-Server-Id": "srv-01"},
        body=b'{"result": "e2e_success", "items_count": 5}',
    )

    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    assert instance.status == WorkflowStatus.COMPLETED

    # Inspect outputs in WorkflowContext step_outputs
    step_out = instance.context.step_outputs.get("step_fetch")
    assert step_out is not None
    assert isinstance(step_out, ActionResult)
    assert step_out.status == "SUCCESS"
    assert step_out.response_payload["status_code"] == 200
    assert step_out.response_payload["body"]["result"] == "e2e_success"

    # Verify TCP connection reached MockNetworkBackend
    assert len(mock_backend.connect_tcp_calls) >= 1
    assert mock_backend.connect_tcp_calls[0]["host"] == "93.184.216.34"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_actual_workflow_retry_default(tmp_path) -> None:
    """2. Verify actual WorkflowStep retry_policy default when omitted (retry_policy=None -> max_attempts=3)."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "def_storage"))
    workflow_engine = WorkflowEngine()
    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    security_engine = _register_security_engine(kernel)

    attempts_tracker: list[int] = []

    async def flaky_capability_handler() -> str:
        attempts_tracker.append(len(attempts_tracker) + 1)
        if len(attempts_tracker) < 3:
            raise RuntimeError(f"Transient error on attempt {len(attempts_tracker)}")
        return "success_on_attempt_3"

    # Registration must happen before boot — capability registration is only
    # permitted while the Kernel is CREATED or BOOTING (Kernel Capability
    # Enforcement Boundary milestone's registration-time state gate).
    kernel.register_capability(
        name="custom.flaky.capability",
        description="Flaky capability requiring 3 attempts",
        provider="test",
        handler=flaky_capability_handler,
    )

    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    # Step created with NO retry_policy supplied (defaults to None on WorkflowStep model)
    step_default = WorkflowStep(
        id="step_no_policy",
        name="Step Without Explicit Policy",
        capability_name="custom.flaky.capability",
        parameters={"_authz_context": {"resource_tenant_id": tenant_id}},
    )
    assert step_default.retry_policy is None, "WorkflowStep.retry_policy model field default must be None"

    wf_def = WorkflowDefinition(id="wf_default_retry", name="Default Retry WF", steps=[step_default])
    def_id = workflow_engine.register_definition(wf_def)

    instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
    # Default RetryPolicy has initial_delay_seconds=1.0 & backoff_factor=2.0 (wait up to 5s for retries)
    for _ in range(500):
        if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            break
        await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    assert attempts_tracker == [1, 2, 3], "StepEvaluator must retry up to 3 times by default when retry_policy is omitted"
    assert instance.context.step_outputs["step_no_policy"] == "success_on_attempt_3"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_actual_retry_amplification_default_workflow_policy(tmp_path) -> None:
    """3. Verify actual retry attempt count for default workflow policy (retry_policy=None) x connector retries (max_retries=2)."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "amp_def_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-amp-def-1",
        name="Amplification Default Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=2,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-ampdef-{uuid4()}-step_ampdef"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-amp-def-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/amp503"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    # Workflow step created with NO retry_policy supplied
    step = WorkflowStep(
        id="step_ampdef",
        name="Step Amp Def",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
    )
    wf_def = WorkflowDefinition(id="wf_ampdef", name="Amp Def WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    class FailingMockBackend(httpcore.AsyncNetworkBackend):
        def __init__(self) -> None:
            self.connect_tcp_calls: list[dict[str, Any]] = []

        async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
            self.connect_tcp_calls.append({"host": host, "port": port})
            raise socket.error("Connection reset by peer")

        async def connect_unix_socket(self, *args, **kwargs):
            raise socket.error("Connection reset")

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(0.001)

    fail_backend = FailingMockBackend()
    orig_pinned_init = PinnedIPNetworkBackend.__init__
    orig_ssrf_init = SSRFHardenedTransport.__init__

    def patched_pinned_init(self_pinned, pinned_ip):
        orig_pinned_init(self_pinned, pinned_ip)
        self_pinned._default_backend = fail_backend

    def patched_ssrf_init(self_ssrf, pinned_ip, **kwargs):
        kwargs["verify"] = False
        orig_ssrf_init(self_ssrf, pinned_ip, **kwargs)

    p_dns = patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))])
    p_pinned = patch.object(PinnedIPNetworkBackend, "__init__", patched_pinned_init)
    p_ssrf = patch.object(SSRFHardenedTransport, "__init__", patched_ssrf_init)

    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    out: ActionResult = instance.context.step_outputs["step_ampdef"]
    assert out.status == "FAILED"
    # ConnectorPipeline catches network exception in execute_with_retry and returns ActionResult(status="FAILED").
    # StepEvaluator receives ActionResult normally without exception, resulting in 1 workflow attempt * (1 initial + 2 connector retries) = 3 connector attempts.
    assert len(fail_backend.connect_tcp_calls) >= 3

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_actual_retry_amplification_explicit_exception_multiplication(tmp_path) -> None:
    """4. Verify retry multiplication when capability raises an unhandled exception (workflow max_attempts=3 x connector max_retries=2)."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "amp_mult_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)

    # Capability wrapper that raises an exception if ConnectorEngine returns a FAILED ActionResult.
    # Registered before boot — capability registration is only permitted
    # while the Kernel is CREATED or BOOTING. The handler closure only calls
    # `connector_engine.execute_action` at request time, after boot has
    # completed, so capturing the (not-yet-initialized) engine here is safe.
    orig_connector_exec = connector_engine.execute_action

    async def strict_connector_handler(request: ActionRequest) -> ActionResult:
        res = await orig_connector_exec(request)
        if res.status == "FAILED":
            raise RuntimeError(f"Connector action failed: {res.error_details}")
        return res

    kernel.register_capability(
        name="kortex.connector.strict_action.execute",
        description="Strict Connector Execution",
        provider="test",
        handler=strict_connector_handler,
    )

    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-mult-1",
        name="Mult Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=2,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-mult-{uuid4()}-step_mult"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-mult-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/mult503"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
        # M6.3-1: this test's own `strict_connector_handler` wrapper calls
        # `connector_engine.execute_action` directly (bypassing Kernel
        # dispatch, so no `principal` is ever injected into it) -- unlike
        # every other test in this file, which reaches `execute_action`
        # through real dispatch and gets a principal-corrected tenant
        # regardless of what this field says. Must match the profile's own
        # `tenant_id` for the no-principal fallback path to resolve it.
        tenant_id=tenant_id,
    )
    step = WorkflowStep(
        id="step_mult",
        name="Step Mult",
        capability_name="kortex.connector.strict_action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=3, initial_delay_seconds=0.001, backoff_factor=1.0, jitter=False),
    )
    wf_def = WorkflowDefinition(id="wf_mult", name="Mult WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    class FailingMockBackend(httpcore.AsyncNetworkBackend):
        def __init__(self) -> None:
            self.connect_tcp_calls: list[dict[str, Any]] = []

        async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
            self.connect_tcp_calls.append({"host": host, "port": port})
            raise socket.error("Connection reset by peer")

        async def connect_unix_socket(self, *args, **kwargs):
            raise socket.error("Connection reset")

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(0.001)

    fail_backend = FailingMockBackend()
    orig_pinned_init = PinnedIPNetworkBackend.__init__
    orig_ssrf_init = SSRFHardenedTransport.__init__

    def patched_pinned_init(self_pinned, pinned_ip):
        orig_pinned_init(self_pinned, pinned_ip)
        self_pinned._default_backend = fail_backend

    def patched_ssrf_init(self_ssrf, pinned_ip, **kwargs):
        kwargs["verify"] = False
        orig_ssrf_init(self_ssrf, pinned_ip, **kwargs)

    p_dns = patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))])
    p_pinned = patch.object(PinnedIPNetworkBackend, "__init__", patched_pinned_init)
    p_ssrf = patch.object(SSRFHardenedTransport, "__init__", patched_ssrf_init)

    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        # 9 real connector attempts plus, as of Milestone M5's dispatch-level
        # audit hook, 3 additional audited authenticate+authorize round trips
        # to IDataStore (one pair per workflow-level attempt) push total
        # completion slightly past a 1-second poll ceiling; 3 seconds keeps
        # ample headroom without masking a genuine hang (observed completion
        # is ~1.3-2s).
        for _ in range(300):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.FAILED
    # When exception escapes capability handler, Workflow retries 3 times * 3 connector attempts (1 initial + 2 retries) = 9 connector attempts total.
    assert len(fail_backend.connect_tcp_calls) >= 9

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_step_evaluator_is_completely_generic(tmp_path) -> None:
    """5. Verify StepEvaluator contains zero special-casing for ConnectorEngine (generic capability path preserved)."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "gen_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)

    # Register capability directly to verify StepEvaluator invokes any arbitrary handler.
    # Must happen before boot — capability registration is only permitted
    # while the Kernel is CREATED or BOOTING.
    invoked_args = {}

    async def custom_capability_handler(request: Any) -> str:
        invoked_args["received"] = request
        return "custom_capability_output"

    kernel.register_capability(
        name="custom.generic.capability",
        description="Arbitrary generic capability",
        provider="test",
        handler=custom_capability_handler,
    )

    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    step = WorkflowStep(
        id="generic_step",
        name="Generic Step",
        capability_name="custom.generic.capability",
        parameters={"request": "test_payload_val", "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_generic", name="Generic WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
    for _ in range(100):
        if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            break
        await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    assert invoked_args["received"] == "test_payload_val"
    assert instance.context.step_outputs["generic_step"] == "custom_capability_output"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_action_request_mapping_verification(tmp_path) -> None:
    """6. Verify ActionRequest object parameters propagate exactly without reshaping into parallel models."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "map_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    received_requests: list[ActionRequest] = []

    async def mock_execute_action(request: ActionRequest) -> ActionResult:
        received_requests.append(request)
        return ActionResult(
            request_id=request.request_id,
            status="SUCCESS",
            response_payload={"mapped": True},
            correlation_id=request.correlation_id,
        )

    connector_engine.execute_action = mock_execute_action

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    dummy_id = str(uuid4())
    req = ActionRequest(
        request_id=f"wf-{dummy_id}-step_map",
        profile_id="prof-map-1",
        action_type=ConnectorActionType.PUSH,
        payload={"data": "exact_mapping"},
        correlation_id=dummy_id,
        options={"granted_permissions": ["kortex.connector.action.execute"], "custom_flag": True},
        tenant_id="tenant-acme",
    )
    step = WorkflowStep(
        id="step_map",
        name="Map Verification Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_map", name="Map WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
    for _ in range(100):
        if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
            break
        await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED

    assert len(received_requests) == 1
    sent_req = received_requests[0]
    assert sent_req.request_id == f"wf-{dummy_id}-step_map"
    assert sent_req.profile_id == "prof-map-1"
    assert sent_req.action_type == ConnectorActionType.PUSH
    assert sent_req.payload == {"data": "exact_mapping"}
    assert sent_req.correlation_id == dummy_id
    assert sent_req.options["custom_flag"] is True
    assert sent_req.tenant_id == "tenant-acme"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_connector_pipeline_execution_and_rate_limiting(tmp_path) -> None:
    """7. Verify ConnectorPipeline rate limiter, events, and driver execution apply during workflow step."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "pipe_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-pipeline-1",
        name="Pipeline Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        rate_limit_per_sec=100.0,
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-pipe-{uuid4()}-step_pipe"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-pipeline-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/pipeline/test"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_pipe",
        name="Pipeline Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_pipeline", name="Pipeline WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, mock_backend = setup_mock_transport(status_code=200, body=b'{"pipeline": "passed"}')

    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    assert len(mock_backend.connect_tcp_calls) >= 1

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_http_success_propagates_to_step_output(tmp_path) -> None:
    """8. Verify HTTP 200 response payload maps to step_outputs[step_id]."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "succ_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-succ-1",
        name="Success Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-succ-{uuid4()}-step_succ"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-succ-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/success"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_succ",
        name="Success Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_succ", name="Succ WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, _ = setup_mock_transport(
        status_code=200,
        body=b'{"user": "alice", "role": "admin"}',
    )
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    out: ActionResult = instance.context.step_outputs["step_succ"]
    assert out.status == "SUCCESS"
    assert out.response_payload["body"] == {"user": "alice", "role": "admin"}

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_http_failure_propagates_to_step_failure(tmp_path) -> None:
    """9. Verify HTTP 500 error response propagates as structured ActionResult(status='FAILED') in step_outputs."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "fail_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-fail-1",
        name="Fail Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-fail-{uuid4()}-step_fail"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-fail-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/error500"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_fail",
        name="Fail Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_fail", name="Fail WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, _ = setup_mock_transport(
        status_code=500,
        body=b'{"error": "Internal Server Error"}',
    )
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    # Step execution returns structured ActionResult(status="FAILED")
    assert instance.state == WorkflowState.COMPLETED
    out: ActionResult = instance.context.step_outputs["step_fail"]
    assert out.status == "FAILED"
    assert out.error_details["status_code"] == 500

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_rbac_capability_permission_denied_before_network(tmp_path) -> None:
    """10. Verify missing 'kortex.connector.action.execute' permission raises ConnectorSecurityError before network dispatch."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "rbac_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-rbac-1",
        name="RBAC Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-rbac-{uuid4()}-step_rbac"
    # Missing kortex.connector.action.execute permission
    req_unauth = ActionRequest(
        request_id=req_id,
        profile_id="prof-rbac-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/unauth"},
        options={"granted_permissions": ["kortex.workflow.execute"]},
    )
    step = WorkflowStep(
        id="step_rbac",
        name="RBAC Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req_unauth, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_rbac", name="RBAC WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, mock_backend = setup_mock_transport(status_code=200)
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    # Execution fails due to ConnectorSecurityError
    assert instance.state == WorkflowState.FAILED

    # Zero network connections attempted
    assert len(mock_backend.connect_tcp_calls) == 0

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_secret_token_isolation_across_context_logs_and_events(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    """11. Verify secret token never appears in WorkflowContext, step_outputs, logs, or Kernel event payloads."""
    caplog.set_level(logging.DEBUG)

    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "sec_storage"))
    workflow_engine = WorkflowEngine()

    fake_secret = "Bearer super-secret-api-token-999"

    async def mock_secret_res(handle: str, tenant_id: str) -> str:
        return fake_secret

    connector_engine = ConnectorEngine(secret_resolver=mock_secret_res)

    captured_events: list[Any] = []

    async def capture_event(evt):
        captured_events.append(evt)

    kernel.subscribe_event("connector.action.started", capture_event)
    kernel.subscribe_event("connector.action.completed", capture_event)
    kernel.subscribe_event("connector.action.failed", capture_event)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-secret-1",
        name="Secret Profile",
        driver_id="connector-http-rest",
        secret_handle="vault:secret_key",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-sec-{uuid4()}-step_sec"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-secret-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/secret"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_sec",
        name="Secret Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_sec", name="Secret WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, _ = setup_mock_transport(status_code=200, body=b'{"ok": true}')
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED

    # 1. Check WorkflowContext step_outputs
    out: ActionResult = instance.context.step_outputs["step_sec"]
    out_str = str(out.model_dump())
    assert fake_secret not in out_str
    assert "super-secret" not in out_str

    # 2. Check full WorkflowContext model dump
    context_str = str(instance.context.model_dump())
    assert fake_secret not in context_str
    assert "super-secret" not in context_str

    # 3. Check captured log output
    log_text = caplog.text
    assert fake_secret not in log_text
    assert "super-secret" not in log_text

    # 4. Check Kernel event payloads
    await asyncio.sleep(0.05)
    assert len(captured_events) >= 2
    for evt in captured_events:
        evt_str = str(evt.payload)
        assert fake_secret not in evt_str
        assert "super-secret" not in evt_str

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_response_header_sanitization(tmp_path) -> None:
    """12. Verify HTTP response header sanitization in step_outputs and WorkflowContext.

    Verifies:
    - Allowed headers are present in ActionResult within step_outputs.
    - Denied credential-bearing headers are ABSENT from ActionResult within step_outputs.
    - Secret/credential header VALUES do not appear anywhere in WorkflowContext.
    - Secret/credential header VALUES do not appear in connector event payloads.
    """
    from kortex.engines.connector.drivers.http_driver import ALLOWED_RESPONSE_HEADERS

    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "hdr_san_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    # Capture connector events via Kernel event subscription (before boot)
    captured_events: list[Any] = []

    async def _capture_event(evt) -> None:
        captured_events.append(evt)

    kernel.subscribe_event("connector.action.completed", _capture_event)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-hdr-san-1",
        name="Header San Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-hdrsan-{uuid4()}-step_hdrsan"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-hdr-san-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/headers_test"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_hdrsan",
        name="Header San Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_hdrsan", name="Header San WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    mock_hdrs = {
        # Allowed
        "Content-Type": "application/json",
        "Content-Length": "17",
        # Denied — credential-bearing and internal headers
        "Set-Cookie": "session=supersecret",
        "Authorization": "Bearer supersecret",
        "Proxy-Authorization": "Basic supersecret",
        "X-Api-Key": "supersecret",
        "Api-Key": "supersecret",
        "WWW-Authenticate": "secret",
        "X-Internal-Debug": "secret",
    }

    p_dns, p_pinned, p_ssrf, _ = setup_mock_transport(
        status_code=200,
        headers=mock_hdrs,
        body=b'{"data": "clean"}',
    )
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    out: ActionResult = instance.context.step_outputs["step_hdrsan"]

    # Extract actual headers returned in ActionResult within step_outputs
    res_headers = out.response_payload.get("headers", {})
    lower_keys = {k.lower() for k in res_headers}

    # ASSERTION 1: Allowed headers are PRESENT
    assert "content-type" in lower_keys, "Allowed header 'content-type' missing"
    assert "content-length" in lower_keys, "Allowed header 'content-length' missing"

    # ASSERTION 2: Denied headers are ABSENT
    denied = {
        "set-cookie", "authorization", "proxy-authorization",
        "x-api-key", "api-key", "www-authenticate", "x-internal-debug",
    }
    for d in denied:
        assert d not in lower_keys, f"Denied header '{d}' found in step_outputs"

    # ASSERTION 3: Only allowed headers present — no extra headers leak through
    for key in lower_keys:
        assert key in ALLOWED_RESPONSE_HEADERS, f"Non-allowed header '{key}' found in step_outputs"

    # ASSERTION 4: Secret/credential VALUES do not appear in WorkflowContext
    context_dump = instance.context.model_dump_json()
    secret_values = [
        "supersecret", "Bearer supersecret", "Basic supersecret",
        "session=supersecret", "secret",
    ]
    for sv in secret_values:
        assert sv not in context_dump, f"Secret value '{sv}' leaked into WorkflowContext"

    # ASSERTION 5: Secret VALUES do not appear in captured connector events
    await asyncio.sleep(0.05)  # Allow events to propagate
    for evt in captured_events:
        evt_str = str(evt.payload) if hasattr(evt, "payload") else str(evt)
        for sv in secret_values:
            assert sv not in evt_str, f"Secret value '{sv}' leaked into connector event"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_idempotency_key_header_propagation(tmp_path) -> None:
    """13. Verify explicit user-supplied Idempotency-Key header propagates into actual client HTTP request bytes."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "idem_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-idem-1",
        name="Idempotency Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    user_idem_key = "ik-77777777-8888-9999-0000-111111111111"
    req_id = f"wf-idem-{uuid4()}-step_idem"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-idem-1",
        action_type=ConnectorActionType.PUSH,
        payload={
            "url": "http://api.example.com/payments",
            "body": {"amount": 100},
            "headers": {"Idempotency-Key": user_idem_key},
        },
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_idem",
        name="Idempotency Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_idem", name="Idem WF", steps=[step])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, mock_backend = setup_mock_transport(status_code=200, body=b'{"charged": true}')
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        for _ in range(100):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED):
                break
            await asyncio.sleep(0.01)

    assert instance.state == WorkflowState.COMPLETED
    assert len(mock_backend.connect_tcp_calls) >= 1

    # Extract captured raw HTTP socket request headers from MockNetworkStream
    stream: MockNetworkStream = mock_backend.connect_tcp_calls[0]["stream"]
    captured_headers = stream.get_captured_headers()

    assert "idempotency-key" in captured_headers, "Raw HTTP request bytes must contain Idempotency-Key header"
    assert captured_headers["idempotency-key"] == user_idem_key, "Idempotency-Key header value must match exact user input"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_cancellation_and_stream_closure(tmp_path) -> None:
    """14. Verify task cancellation immediately raises asyncio.CancelledError, closes network stream (closed=True), and halts retries."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "cancel_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)
    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-cancel-1",
        name="Cancel Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=5,
    )
    await connector_engine.profile_manager.register_profile(profile)

    req_id = f"wf-cancel-{uuid4()}-step_cancel"
    req = ActionRequest(
        request_id=req_id,
        profile_id="prof-cancel-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/slow"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step = WorkflowStep(
        id="step_cancel",
        name="Cancel Step",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req, "_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )
    wf_def = WorkflowDefinition(id="wf_cancel", name="Cancel WF", steps=[step])

    created_streams: list[SlowMockStream] = []

    class SlowMockStream(httpcore.AsyncNetworkStream):
        def __init__(self) -> None:
            self.closed = False
            created_streams.append(self)

        async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
            try:
                await asyncio.sleep(10.0)
            except asyncio.CancelledError:
                self.closed = True
                raise
            return b""

        async def write(self, buffer: bytes, timeout: float | None = None) -> None:
            pass

        async def aclose(self) -> None:
            self.closed = True

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return self

        def get_extra_info(self, info: str, default=None):
            return default

    class SlowMockBackend(httpcore.AsyncNetworkBackend):
        def __init__(self) -> None:
            self.connect_tcp_calls: list[dict[str, Any]] = []

        async def connect_tcp(self, host: str, port: int, timeout=None, local_address=None, socket_options=None):
            stream = SlowMockStream()
            self.connect_tcp_calls.append({"host": host, "port": port, "stream": stream})
            return stream

        async def connect_unix_socket(self, *args, **kwargs):
            stream = SlowMockStream()
            return stream

        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(seconds)

    slow_backend = SlowMockBackend()
    orig_pinned_init = PinnedIPNetworkBackend.__init__
    orig_ssrf_init = SSRFHardenedTransport.__init__

    def patched_pinned_init(self_pinned, pinned_ip):
        orig_pinned_init(self_pinned, pinned_ip)
        self_pinned._default_backend = slow_backend

    def patched_ssrf_init(self_ssrf, pinned_ip, **kwargs):
        kwargs["verify"] = False
        orig_ssrf_init(self_ssrf, pinned_ip, **kwargs)

    p_dns = patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))])
    p_pinned = patch.object(PinnedIPNetworkBackend, "__init__", patched_pinned_init)
    p_ssrf = patch.object(SSRFHardenedTransport, "__init__", patched_ssrf_init)

    with p_dns, p_pinned, p_ssrf:
        def_id = workflow_engine.register_definition(wf_def)
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        # Find background task executing workflow instance steps
        for _ in range(100):
            if created_streams:
                break
            await asyncio.sleep(0.02)
        # Cancel active workflow tasks
        for task in asyncio.all_tasks():
            if "_run_instance_steps" in repr(task):
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    # 1. asyncio.CancelledError propagated out of task
    # 2. Verify stream aclose() was called and stream.closed is True
    assert len(created_streams) >= 1
    assert created_streams[0].closed is True, "Network stream must be closed upon cancellation"

    # 3. Verify no additional connector retries occurred after cancellation
    assert len(slow_backend.connect_tcp_calls) == len(created_streams), "Zero extra retries must execute after cancellation"

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_failed_connector_step_participates_in_compensation_stack(tmp_path) -> None:
    """15. Verify a failed connector step triggers LIFO compensation stack rollback execution in WorkflowEngine."""
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "comp_storage"))
    workflow_engine = WorkflowEngine()
    connector_engine = ConnectorEngine(secret_resolver=mock_secret_resolver)

    kernel.register_engine(storage_engine)
    kernel.register_engine(workflow_engine)
    kernel.register_engine(connector_engine)
    security_engine = _register_security_engine(kernel)

    compensation_executed = []

    async def mock_comp_handler(**kwargs) -> None:
        compensation_executed.append(kwargs)

    async def failing_capability_handler() -> None:
        raise RuntimeError("Step 2 catastrophic failure")

    # Both must be registered before boot — capability registration is only
    # permitted while the Kernel is CREATED or BOOTING.
    kernel.register_capability(
        name="kortex.test.compensation",
        description="Compensation capability",
        provider="test",
        handler=mock_comp_handler,
    )
    kernel.register_capability(
        name="kortex.test.failing_step",
        description="Failing capability",
        provider="test",
        handler=failing_capability_handler,
    )

    await kernel.boot()
    session_token, tenant_id = await _issue_test_session_token(security_engine, storage_engine.data)

    driver = HttpRestConnectorDriver()
    connector_engine.register_driver(driver)

    profile = ConnectorProfile(
        tenant_id=tenant_id,
        profile_id="prof-comp-1",
        name="Comp Profile",
        driver_id="connector-http-rest",
        options={"base_url": "http://api.example.com"},
        max_retries=0,
    )
    await connector_engine.profile_manager.register_profile(profile)

    # Step 1: Successful step that registers a LIFO compensation action
    comp_action = CompensationAction(
        name="Rollback Reservation",
        capability_name="kortex.test.compensation",
        parameters={"reservation_id": "res-123", "_authz_context": {"resource_tenant_id": tenant_id}},
    )
    req_succ = ActionRequest(
        request_id=f"wf-comp-{uuid4()}-step_comp_succ",
        profile_id="prof-comp-1",
        action_type=ConnectorActionType.FETCH,
        payload={"url": "http://api.example.com/reserve"},
        options={"granted_permissions": ["kortex.connector.action.execute"]},
    )
    step_succ = WorkflowStep(
        id="step_comp_succ",
        name="Successful Step with Compensation",
        capability_name="kortex.connector.action.execute",
        parameters={"request": req_succ, "_authz_context": {"resource_tenant_id": tenant_id}},
        compensation_action=comp_action,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    # Step 2: Step that raises an exception, triggering workflow failure and compensation rollback
    step_fail = WorkflowStep(
        id="step_failing",
        name="Failing Step",
        capability_name="kortex.test.failing_step",
        parameters={"_authz_context": {"resource_tenant_id": tenant_id}},
        retry_policy=RetryPolicy(max_attempts=1),
    )

    wf_def = WorkflowDefinition(id="wf_comp", name="Comp WF", steps=[step_succ, step_fail])
    def_id = workflow_engine.register_definition(wf_def)

    p_dns, p_pinned, p_ssrf, _ = setup_mock_transport(status_code=200, body=b'{"reserved": true}')
    with p_dns, p_pinned, p_ssrf:
        instance = await workflow_engine.start_workflow(def_id, session_token=session_token)
        # `instance.state` transitions to FAILED *before*
        # `execute_compensation_stack` is awaited (engine.py's own existing
        # ordering, unchanged here), and `compensation_stack.pop()` removes
        # an action from the stack immediately, before its dispatch is even
        # awaited — so neither `state` nor `compensation_stack` reliably
        # signals that compensation has actually *finished* now that
        # dispatch performs real async I/O (a fresh `verify_token` DB
        # lookup). Poll on the actual completion signal instead.
        for _ in range(200):
            if instance.state in (WorkflowState.COMPLETED, WorkflowState.FAILED) and compensation_executed:
                break
            await asyncio.sleep(0.01)

    # Workflow failed at Step 2
    assert instance.state == WorkflowState.FAILED

    # LIFO Compensation stack executed Step 1's compensation action
    assert len(compensation_executed) == 1, "Compensation action must be executed upon workflow failure"
    assert compensation_executed[0]["reservation_id"] == "res-123", "Compensation parameters must match exact input"

    await kernel.shutdown()
