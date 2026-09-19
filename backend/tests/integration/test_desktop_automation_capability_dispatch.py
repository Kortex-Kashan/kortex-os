"""End-to-end vertical slice for the `kortex.desktop.*` capabilities.

Every test enters through the **real** Kernel capability-dispatch boundary --
real `SecurityEngine` authentication, real RBAC/ABAC, real
`kernel.invoke_capability` -- mirroring the methodology
`test_python_execution_vertical_slice.py` established for the Python
execution boundary. Nothing about `CapabilityDispatcher`/`SecurityEngine` is
stubbed or bypassed.

What *is* faked is the Desktop Agent's other end of the wire: a background
task plays its role by draining `AgentSession.outbound` and resolving the
matching pending command directly (the same primitive
`DesktopAgentGatewayServicer._resolve_pending_command` a real gRPC `Connect`
call would drive) -- exercising the real transport queue/future machinery
without needing a live mTLS socket or provisioned PKI material, which the
actual FlaUI/UIA execution (`apps/desktop-agent`) is responsible for and
which the dedicated `test_desktop_command_transport.py` already proves over
a real gRPC channel.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.agent_gateway.engine import AgentSession, DesktopAgentGatewayServicer
from kortex.engines.agent_gateway.exceptions import DesktopAgentUnavailableError
from kortex.engines.agent_gateway.protos import agent_pb2
from kortex.engines.desktop_automation.engine import (
    CLICK_CAPABILITY,
    LAUNCH_CAPABILITY,
    READ_TEXT_CAPABILITY,
    TYPE_CAPABILITY,
    DesktopAutomationEngine,
)
from kortex.engines.desktop_automation.exceptions import (
    DesktopApplicationNotAllowedError,
    DesktopElementAmbiguousError,
    DesktopElementNotFoundError,
    DesktopInvalidSelectorError,
)
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x51" * 32
_TEST_SIGNING_KEY = b"\x52" * 32

_FULL_ROLE = "DESKTOP_SLICE_FULL_ROLE"
_NO_PERMISSIONS_ROLE = "DESKTOP_SLICE_NO_PERMISSIONS_ROLE"
_TENANT_A = "tenant-desktop-a"
_TENANT_B = "tenant-desktop-b"
_USER_FULL = "user-desktop-full"
_USER_NONE = "user-desktop-none"
_USER_TENANT_B = "user-desktop-b"
_PASSWORD = "desktop-slice-test-pass"
_AGENT_A_PRINCIPAL = "agent-desktop-a"


@dataclass
class _Env:
    kernel: Kernel
    security_engine: SecurityEngine
    desktop_engine: DesktopAutomationEngine
    received_commands: list[Any] = field(default_factory=list)
    agent_loop_tasks: list[asyncio.Task[None]] = field(default_factory=list)


async def _fake_agent_loop(session: AgentSession, received: list[Any], *, error_code: str = "") -> None:
    """Answer every command pushed to `session` with a canned result.

    `error_code` set to a non-empty string simulates the agent refusing the
    operation (e.g. `APPLICATION_NOT_ALLOWED`); left empty, every command
    succeeds with placeholder identifiers.
    """
    while True:
        message = await session.outbound.get()
        received.append(message)
        kind = message.WhichOneof("payload")
        command = getattr(message, kind)
        if error_code:
            result = agent_pb2.DesktopCommandResult(command_id=command.command_id, success=False, error_code=error_code)
        elif kind == "desktop_launch":
            result = agent_pb2.DesktopCommandResult(
                command_id=command.command_id, success=True, window_handle="win-fake-1", process_id=9001
            )
        elif kind == "desktop_read_text":
            result = agent_pb2.DesktopCommandResult(
                command_id=command.command_id, success=True, text="fake-read-value", truncated=False
            )
        else:
            result = agent_pb2.DesktopCommandResult(command_id=command.command_id, success=True)
        DesktopAgentGatewayServicer._resolve_pending_command(session, result)


@pytest_asyncio.fixture
async def env(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[_Env]:
    tmp_path = tmp_path_factory.mktemp("desktop-slice")
    db_path = (tmp_path / "slice.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    desktop_engine = DesktopAutomationEngine(data_store=RelationalDataStore(db_manager), port=0)
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(desktop_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING
    # No PKI material was provisioned; the gateway degrades to "registered,
    # not listening" exactly as `DesktopAutomationEngine.start()` documents.
    assert desktop_engine._gateway_running is False

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("desktop:launch", "desktop:click", "desktop:type", "desktop:read"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_FULL_ROLE, permission=permission))
        session.add(  # deliberately grants nothing
            RolePermissionRecord(id=str(uuid4()), role=_NO_PERMISSIONS_ROLE, permission="unrelated:permission")
        )
        for tenant_id, principal_id, role in (
            (_TENANT_A, _USER_FULL, _FULL_ROLE),
            (_TENANT_A, _USER_NONE, _NO_PERMISSIONS_ROLE),
            (_TENANT_B, _USER_TENANT_B, _FULL_ROLE),
        ):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type="USER",
                    enabled=True,
                    credential_hash=hasher.hash(_PASSWORD),
                    roles=[role],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await storage_engine.data.execute_in_transaction(_seed)

    environment = _Env(kernel=kernel, security_engine=security_engine, desktop_engine=desktop_engine)
    try:
        yield environment
    finally:
        for task in environment.agent_loop_tasks:
            task.cancel()
        for task in environment.agent_loop_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if kernel.state == KernelState.RUNNING:
            with contextlib.suppress(Exception):
                await kernel.shutdown()
        await db_manager.disconnect()


async def _token(env: _Env, tenant_id: str, principal_id: str) -> Any:
    principal = await env.security_engine.authenticate(
        {"principal_type": "USER", "tenant_id": tenant_id, "principal_id": principal_id, "password": _PASSWORD}
    )
    return await env.security_engine.authentication_manager.issue_token(principal)


async def _invoke(env: _Env, capability: str, token: Any, *, context_tenant: str = _TENANT_A, **parameters: Any) -> Any:
    return await env.kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=dict(parameters),
            context={"resource_tenant_id": context_tenant},
        )
    )


def _register_fake_agent(env: _Env, tenant_id: str = _TENANT_A, *, error_code: str = "") -> AgentSession:
    session = AgentSession(
        session_id=str(uuid4()),
        principal_id=_AGENT_A_PRINCIPAL,
        tenant_id=tenant_id,
        machine_installation_id="m1",
        # This suite exercises the capability/RBAC layer, not the transport's
        # own identity-confirmation handshake (covered by
        # `test_desktop_command_transport.py`/`test_agent_gateway_mtls.py`),
        # so the fake session is registered as already having passed it.
        identity_confirmed=True,
    )
    env.desktop_engine.gateway.sessions[session.session_id] = session
    task = asyncio.ensure_future(_fake_agent_loop(session, env.received_commands, error_code=error_code))
    env.agent_loop_tasks.append(task)
    return session


# -- Authorized success (per capability) ---------------------------------------


async def test_launch_dispatch_authorized_succeeds(env: _Env) -> None:
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(env, LAUNCH_CAPABILITY, token, application_id="notepad")
    assert result == {"window_handle": "win-fake-1", "process_id": 9001}


async def test_click_dispatch_authorized_succeeds(env: _Env) -> None:
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(
        env, CLICK_CAPABILITY, token, window_handle="win-fake-1", selector={"automation_id": "OkButton"}
    )
    assert result == {"success": True}


async def test_type_dispatch_authorized_succeeds(env: _Env) -> None:
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(
        env,
        TYPE_CAPABILITY,
        token,
        window_handle="win-fake-1",
        selector={"automation_id": "InputBox"},
        ui_input_text="hello world",
    )
    assert result == {"success": True}
    sent = env.received_commands[-1].desktop_type
    assert sent.text == "hello world"


async def test_read_text_dispatch_authorized_succeeds(env: _Env) -> None:
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_FULL)
    result = await _invoke(
        env, READ_TEXT_CAPABILITY, token, window_handle="win-fake-1", selector={"name": "Status"}, max_length=128
    )
    assert result == {"text": "fake-read-value", "truncated": False}
    sent = env.received_commands[-1].desktop_read_text
    assert sent.max_length == 128


# -- Unauthorized (RBAC) denial -------------------------------------------------


@pytest.mark.parametrize(
    ("capability", "parameters"),
    [
        (LAUNCH_CAPABILITY, {"application_id": "notepad"}),
        (CLICK_CAPABILITY, {"window_handle": "w1", "selector": {"automation_id": "x"}}),
        (TYPE_CAPABILITY, {"window_handle": "w1", "selector": {"automation_id": "x"}, "ui_input_text": "hi"}),
        (READ_TEXT_CAPABILITY, {"window_handle": "w1", "selector": {"automation_id": "x"}}),
    ],
)
async def test_unauthorized_principal_denied(env: _Env, capability: str, parameters: dict[str, Any]) -> None:
    """A principal holding none of the `desktop:*` permissions is denied
    before any command ever reaches the (fake) agent."""
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_NONE)
    with pytest.raises(AuthorizationDeniedError):
        await _invoke(env, capability, token, **parameters)
    assert env.received_commands == []


# -- Fail-closed agent selection -------------------------------------------------


async def test_malformed_request_missing_required_parameter_fails_closed(env: _Env) -> None:
    """A request missing a capability's required parameters (no
    `window_handle`, no `selector`) never reaches the agent — it fails
    inside handler invocation, before any command is built or sent."""
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(TypeError):
        await _invoke(env, CLICK_CAPABILITY, token)
    assert env.received_commands == []


async def test_no_agent_connected_fails_closed(env: _Env) -> None:
    """No desktop agent session exists for the tenant at all."""
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(DesktopAgentUnavailableError):
        await _invoke(env, LAUNCH_CAPABILITY, token, application_id="notepad")


async def test_cross_tenant_agent_is_never_reachable(env: _Env) -> None:
    """An agent enrolled for tenant A is never selected for tenant B's
    request, even though it is the only session connected."""
    _register_fake_agent(env, tenant_id=_TENANT_A)
    token = await _token(env, _TENANT_B, _USER_TENANT_B)
    with pytest.raises(DesktopAgentUnavailableError):
        await _invoke(env, LAUNCH_CAPABILITY, token, application_id="notepad", context_tenant=_TENANT_B)
    assert env.received_commands == []


# -- Fail-closed UI targeting ----------------------------------------------------


async def test_empty_selector_rejected_before_any_command_is_sent(env: _Env) -> None:
    _register_fake_agent(env)
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(DesktopInvalidSelectorError):
        await _invoke(env, CLICK_CAPABILITY, token, window_handle="w1", selector={})
    assert env.received_commands == []


async def test_ambiguous_ui_target_fails_closed(env: _Env) -> None:
    _register_fake_agent(env, error_code="ELEMENT_AMBIGUOUS")
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(DesktopElementAmbiguousError):
        await _invoke(env, CLICK_CAPABILITY, token, window_handle="w1", selector={"name": "Submit"})


async def test_missing_ui_target_fails_closed(env: _Env) -> None:
    _register_fake_agent(env, error_code="ELEMENT_NOT_FOUND")
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(DesktopElementNotFoundError):
        await _invoke(env, CLICK_CAPABILITY, token, window_handle="w1", selector={"name": "Ghost"})


async def test_application_not_on_allow_list_is_blocked(env: _Env) -> None:
    """Arbitrary-executable execution is blocked: the agent's own allow-list
    rejection surfaces as a specific, decoded exception, not a generic one."""
    _register_fake_agent(env, error_code="APPLICATION_NOT_ALLOWED")
    token = await _token(env, _TENANT_A, _USER_FULL)
    with pytest.raises(DesktopApplicationNotAllowedError):
        await _invoke(env, LAUNCH_CAPABILITY, token, application_id="cmd")


# -- Sensitive value handling -----------------------------------------------------


def test_ui_input_text_is_redacted_from_persisted_audit_context() -> None:
    """`kortex.desktop.type`'s typed text is never written to an audit
    record or cached idempotency response in the clear."""
    cleaned = sanitize_for_persistence({"ui_input_text": "hunter2", "window_handle": "w1"})
    assert cleaned["ui_input_text"] is None
    assert cleaned["window_handle"] == "w1"
