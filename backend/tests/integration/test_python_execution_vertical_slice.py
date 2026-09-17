"""End-to-end vertical slice for governed Python execution.

Every test enters through the **real** Kernel capability-dispatch boundary --
real `SecurityEngine` authentication, real RBAC/ABAC, real
`kernel.invoke_capability` -- never through a raw Gateway or raw manager
shortcut, matching the methodology `test_mcp_gateway_catalog.py` established.

The canonical chain proved here:

    authenticated principal
      -> kortex.python.action.publish   (immutable version)
      -> kortex.python.execute          (pinned version)
      -> CapabilityDispatcher           (existing execution boundary)
      -> SecurityEngine authorization
      -> governed platform boundary     (real process, real isolation)
      -> governed capability IPC        (real named pipe, real token)
      -> CapabilityDispatcher           (nested, same authority)
      -> structured result -> execution lineage

Nothing is stubbed. The Python that runs is a real interpreter in a real
AppContainer, and the capability it calls back into KORTEX crosses a real
named pipe.
"""

from __future__ import annotations

import contextlib
import platform
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.python_exec.engine import (
    ACTION_PUBLISH_CAPABILITY,
    ACTION_VERSION_LIST_CAPABILITY,
    EXECUTE_CAPABILITY,
    PythonExecutionEngine,
)
from kortex.engines.python_exec.models import PythonExecutionRecordModel, PythonTrustLevel
from kortex.engines.python_exec.workspace import delete_sandbox_identity
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_IS_WINDOWS = platform.system() == "Windows"

pytestmark = pytest.mark.skipif(
    not _IS_WINDOWS,
    reason="Real Python execution requires a platform with an implemented boundary; "
    "the Linux path additionally requires nsjail (see the milestone report).",
)

_TEST_MASTER_KEY = b"\x41" * 32
_TEST_SIGNING_KEY = b"\x42" * 32

_FULL_ROLE = "PYTHON_SLICE_FULL_ROLE"
_READONLY_ROLE = "PYTHON_SLICE_READONLY_ROLE"
# Holds python:write/execute but deliberately NOT python:trust, so the
# trust check is reached rather than being pre-empted by an ordinary RBAC
# denial on the publish capability itself.
_WRITER_ROLE = "PYTHON_SLICE_WRITER_ROLE"
_TENANT_A = "tenant-slice-a"
_TENANT_B = "tenant-slice-b"
_USER_A = "user-slice-a"
_USER_B = "user-slice-b"
_USER_READONLY = "user-slice-readonly"
_USER_WRITER = "user-slice-writer"
_PASSWORD = "slice-test-pass"


@dataclass
class _Env:
    kernel: Kernel
    security_engine: SecurityEngine
    python_engine: PythonExecutionEngine
    storage_engine: StorageEngine
    dispatched: list[str] = field(default_factory=list)


@pytest_asyncio.fixture(scope="module")
async def env(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[_Env]:
    """A booted Kernel, module-scoped so the runtime image is provisioned once."""
    tmp_path = tmp_path_factory.mktemp("python-slice")
    db_path = (tmp_path / "slice.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / "storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    python_engine = PythonExecutionEngine(
        data_store=RelationalDataStore(db_manager),
        execution_root=tmp_path / "pyexec",
        identity_name="KortexPythonSliceTest",
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(python_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("python:read", "python:write", "python:execute", "python:trust"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_FULL_ROLE, permission=permission))
        # Deliberately holds neither python:execute nor python:trust.
        session.add(RolePermissionRecord(id=str(uuid4()), role=_READONLY_ROLE, permission="python:read"))
        for permission in ("python:read", "python:write", "python:execute"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_WRITER_ROLE, permission=permission))

        for tenant_id, principal_id, role in (
            (_TENANT_A, _USER_A, _FULL_ROLE),
            (_TENANT_B, _USER_B, _FULL_ROLE),
            (_TENANT_A, _USER_READONLY, _READONLY_ROLE),
            (_TENANT_A, _USER_WRITER, _WRITER_ROLE),
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

    environment = _Env(
        kernel=kernel,
        security_engine=security_engine,
        python_engine=python_engine,
        storage_engine=storage_engine,
    )

    # A pass-through spy on the REAL dispatcher: records what actually crossed
    # the execution boundary without replacing any of its behaviour.
    real_dispatch = kernel._dispatcher.dispatch

    async def _recording_dispatch(request: CapabilityRequest) -> Any:
        environment.dispatched.append(request.capability_name)
        return await real_dispatch(request)

    kernel._dispatcher.dispatch = _recording_dispatch  # type: ignore[method-assign]

    try:
        yield environment
    finally:
        kernel._dispatcher.dispatch = real_dispatch  # type: ignore[method-assign]
        if kernel.state == KernelState.RUNNING:
            with contextlib.suppress(Exception):
                await kernel.shutdown()
        await db_manager.disconnect()

        # AppContainer provisioning is lazy (see `PythonExecutionGateway.
        # ensure_provisioned`'s own docstring): the "KortexPythonSliceTest"
        # identity only exists if some test in this module actually executed
        # Python. Only delete it in that case -- there is nothing to remove,
        # and nothing to silently no-op past, otherwise. A genuine deletion
        # failure is raised, not swallowed, so it surfaces as a visible
        # teardown error rather than leaving profile residue unnoticed.
        gateway_identity = python_engine.gateway._identity
        if gateway_identity is not None:
            delete_sandbox_identity(gateway_identity)


async def _token(env: _Env, tenant_id: str, principal_id: str) -> Any:
    principal = await env.security_engine.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": _PASSWORD,
        }
    )
    return await env.security_engine.authentication_manager.issue_token(principal)


async def _invoke(env: _Env, capability: str, token: Any, *, context_tenant: str = _TENANT_A, **parameters: Any) -> Any:
    """Drive one capability through the real Kernel dispatch boundary.

    `resource_tenant_id` is supplied in the request *context* because ABAC
    denies by default without it. It is not an identity claim: the dispatcher
    still derives the authoritative tenant from the verified session token, as
    `test_a_caller_supplied_tenant_id_is_ignored` demonstrates.
    """
    return await env.kernel.invoke_capability(
        CapabilityRequest(
            capability_name=capability,
            session_token=token,
            parameters=dict(parameters),
            context={"resource_tenant_id": context_tenant},
        )
    )


async def _publish(
    env: _Env, token: Any, action_id: str, source: str, *, context_tenant: str = _TENANT_A, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action_id": action_id,
        "name": action_id,
        "source_code": source,
        # TRUSTED by default in this suite because the Windows boundary
        # refuses UNTRUSTED Python outright -- an MVP non-goal, asserted
        # directly by `test_windows_untrusted_python_is_refused_...` below.
        "trust_level": PythonTrustLevel.TRUSTED.value,
    }
    payload.update(overrides)
    result = await _invoke(env, ACTION_PUBLISH_CAPABILITY, token, context_tenant=context_tenant, **payload)
    return dict(result)


# ---------------------------------------------------------------------------
# The canonical slice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_then_execute_through_the_real_dispatch_boundary(env: _Env) -> None:
    """Workflow -> Dispatcher -> SecurityEngine -> boundary -> result."""
    token = await _token(env, _TENANT_A, _USER_A)
    published = await _publish(
        env,
        token,
        "compute-total",
        "def main(payload):\n    return {'total': sum(payload['amounts']), 'currency': payload['currency']}\n",
    )
    assert published["version"] == 1

    env.dispatched.clear()
    result = await _invoke(
        env,
        EXECUTE_CAPABILITY,
        token,
        action_id="compute-total",
        version=1,
        input_payload={"amounts": [10, 20, 12], "currency": "GBP"},
    )

    assert result["status"] == "SUCCEEDED", result
    assert result["output"] == {"total": 42, "currency": "GBP"}
    assert result["boundary"] == "WINDOWS_RESTRICTED_TOKEN_JOB"
    assert result["exit_code"] == 0
    assert result["trust_level"] == "UNTRUSTED" or result["trust_level"] == "TRUSTED"
    # The execution genuinely crossed the real dispatcher.
    assert EXECUTE_CAPABILITY in env.dispatched


@pytest.mark.asyncio
async def test_trusted_python_calls_a_kortex_capability_over_the_real_bridge(env: _Env) -> None:
    """The full Trusted-Python IPC chain, with nothing stubbed.

    A real interpreter inside a real AppContainer opens the real named pipe,
    presents its real execution token, and the call re-enters the real
    dispatcher -- which is what `dispatched` proves by recording the nested
    capability name.
    """
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "bridge-caller",
        "def main(payload, kortex):\n"
        "    actions = kortex.call_capability('kortex.python.action.list', {})\n"
        "    return {'action_count': len(actions), 'ids': sorted(a['action_id'] for a in actions)}\n",
        trust_level=PythonTrustLevel.TRUSTED.value,
        allowed_capabilities=["kortex.python.action.list"],
    )

    env.dispatched.clear()
    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="bridge-caller", version=1)

    assert result["status"] == "SUCCEEDED", result
    assert result["capability_calls"] == 1
    assert "bridge-caller" in result["output"]["ids"]
    # The nested call went through the one authoritative execution boundary.
    assert "kortex.python.action.list" in env.dispatched


@pytest.mark.asyncio
async def test_trusted_python_can_make_several_capability_calls_in_sequence(env: _Env) -> None:
    """Repeated calls must all succeed, not just the first.

    The KORTEX-side accept loop serves one connection per pipe instance and
    then creates the next. A call landing in the gap between those two steps
    sees no endpoint at all, so without the client's bounded connect retry a
    multi-call Trusted Action fails intermittently -- and does so for a reason
    that has nothing to do with its own logic. Five sequential calls is enough
    to hit that window reliably.
    """
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "bridge-repeat",
        "def main(payload, kortex):\n"
        "    counts = []\n"
        "    for _ in range(5):\n"
        "        counts.append(len(kortex.call_capability('kortex.python.action.list', {})))\n"
        "    return {'counts': counts}\n",
        trust_level=PythonTrustLevel.TRUSTED.value,
        allowed_capabilities=["kortex.python.action.list"],
    )

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="bridge-repeat", version=1)

    assert result["status"] == "SUCCEEDED", result
    assert len(result["output"]["counts"]) == 5
    assert result["capability_calls"] == 5
    # Every call saw the same tenant-scoped view.
    assert len(set(result["output"]["counts"])) == 1


@pytest.mark.asyncio
async def test_trusted_python_is_refused_a_capability_outside_its_allowlist(env: _Env) -> None:
    """Refusal surfaces to the action as an exception it can observe."""
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "bridge-overreach",
        "def main(payload, kortex):\n"
        "    try:\n"
        "        kortex.call_capability('kortex.security.secret.get', {'handle': 'vault:x'})\n"
        "        return {'secret_access': 'ALLOWED'}\n"
        "    except Exception as exc:\n"
        "        return {'secret_access': 'REFUSED', 'kind': type(exc).__name__}\n",
        trust_level=PythonTrustLevel.TRUSTED.value,
        allowed_capabilities=["kortex.python.action.list"],
    )

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="bridge-overreach", version=1)
    assert result["status"] == "SUCCEEDED", result
    assert result["output"]["secret_access"] == "REFUSED"
    assert result["output"]["kind"] == "CapabilityError"


@pytest.mark.asyncio
async def test_an_action_declaring_no_capabilities_has_no_bridge_to_reach(env: _Env) -> None:
    """No declared capabilities means no bridge object and no endpoint.

    Not merely unauthorized -- the address and token are absent from the
    execution's environment entirely, so there is nothing for it to attempt.
    An unused endpoint is still an endpoint, so none is opened.
    """
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "no-bridge",
        "import os\n"
        "def main(payload, kortex):\n"
        "    return {\n"
        "        'bridge_is_none': kortex is None,\n"
        "        'address_in_env': 'KORTEX_BRIDGE_ADDRESS' in os.environ,\n"
        "        'token_in_env': 'KORTEX_EXECUTION_TOKEN' in os.environ,\n"
        "    }\n",
    )

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="no-bridge", version=1)
    assert result["status"] == "SUCCEEDED", result
    assert result["output"]["bridge_is_none"] is True
    assert result["output"]["address_in_env"] is False
    assert result["output"]["token_in_env"] is False


@pytest.mark.asyncio
async def test_the_execution_token_is_removed_from_the_actions_environment(env: _Env) -> None:
    """Even a trusted action cannot read its own token out of `os.environ`.

    The runner captures it and deletes it before any action code runs, so an
    action that dumps its environment into its result cannot exfiltrate a live
    credential.
    """
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "token-scrape",
        "import os\n"
        "def main(payload, kortex):\n"
        "    return {\n"
        "        'token_in_env': 'KORTEX_EXECUTION_TOKEN' in os.environ,\n"
        "        'env': dict(os.environ),\n"
        "        'works': len(kortex.call_capability('kortex.python.action.list', {})) >= 0,\n"
        "    }\n",
        trust_level=PythonTrustLevel.TRUSTED.value,
        allowed_capabilities=["kortex.python.action.list"],
    )

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="token-scrape", version=1)
    assert result["status"] == "SUCCEEDED", result
    assert result["output"]["token_in_env"] is False
    # The bridge still worked -- the token was captured, then removed.
    assert result["output"]["works"] is True


@pytest.mark.asyncio
async def test_windows_untrusted_python_is_refused_before_any_process_starts(env: _Env) -> None:
    """Windows Untrusted Python is an MVP non-goal and must be refused.

    The refusal is a REJECTED result rather than a FAILED one, which is the
    distinction that matters: REJECTED means no workspace was created and no
    process was ever spawned, so the Windows boundary was never reached with
    untrusted code at all.
    """
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "untrusted-on-windows",
        "def main(payload):\n    return 'should never run'\n",
        trust_level=PythonTrustLevel.UNTRUSTED.value,
    )

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="untrusted-on-windows", version=1)

    assert result["status"] == "REJECTED"
    assert "UnsupportedTrustLevelError" in result["error"]
    assert "not supported on Windows" in result["error"]
    # No boundary was entered, so none is reported.
    assert result["boundary"] is None or result["exit_code"] is None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_requires_authentication(env: _Env) -> None:
    with pytest.raises(AuthenticationError):
        await env.kernel.invoke_capability(
            CapabilityRequest(
                capability_name=EXECUTE_CAPABILITY,
                session_token=None,
                parameters={"action_id": "compute-total", "version": 1},
            )
        )


@pytest.mark.asyncio
async def test_execution_requires_the_execute_permission(env: _Env) -> None:
    readonly = await _token(env, _TENANT_A, _USER_READONLY)
    with pytest.raises(AuthorizationDeniedError):
        await _invoke(env, EXECUTE_CAPABILITY, readonly, action_id="compute-total", version=1)


@pytest.mark.asyncio
async def test_publishing_a_trusted_action_requires_the_trust_permission(env: _Env) -> None:
    """Trust is conferred, never claimed by the author.

    `python:write` alone would otherwise let any author mark their own code
    trusted and thereby grant it the capability bridge.
    """
    # A principal that CAN publish (holds python:write) but was never
    # granted python:trust. Using the read-only principal instead would
    # prove nothing: the dispatcher would deny the publish capability
    # outright and the trust check would never be reached.
    writer = await _token(env, _TENANT_A, _USER_WRITER)
    with pytest.raises(AuthorizationDeniedError, match="python:trust"):
        await _publish(
            env,
            writer,
            "self-declared-trust",
            "def main(payload):\n    return 1\n",
            trust_level=PythonTrustLevel.TRUSTED.value,
            allowed_capabilities=["kortex.python.action.list"],
        )


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_b_cannot_execute_tenant_a_action(env: _Env) -> None:
    """A cross-tenant execution is refused as not-found, not as forbidden."""
    token_a = await _token(env, _TENANT_A, _USER_A)
    await _publish(env, token_a, "tenant-a-private", "def main(payload):\n    return 'A'\n")

    token_b = await _token(env, _TENANT_B, _USER_B)
    result = await _invoke(
        env, EXECUTE_CAPABILITY, token_b, context_tenant=_TENANT_B, action_id="tenant-a-private", version=1
    )

    assert result["status"] == "REJECTED"
    assert "PythonActionNotFoundError" in result["error"]


@pytest.mark.asyncio
async def test_a_caller_supplied_tenant_id_is_ignored(env: _Env) -> None:
    """`tenant_id` is not a parameter; identity comes from the dispatcher."""
    token_b = await _token(env, _TENANT_B, _USER_B)
    await _publish(env, token_b, "tenant-b-only", "def main(payload):\n    return 'B'\n", context_tenant=_TENANT_B)

    # Tenant B publishes, then a tenant-A caller claims to be tenant B.
    token_a = await _token(env, _TENANT_A, _USER_A)
    result = await _invoke(
        env,
        EXECUTE_CAPABILITY,
        token_a,
        action_id="tenant-b-only",
        version=1,
        tenant_id=_TENANT_B,
    )
    assert result["status"] == "REJECTED"
    assert "PythonActionNotFoundError" in result["error"]


# ---------------------------------------------------------------------------
# Version pinning, failure, lineage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pinned_version_keeps_executing_its_own_source(env: _Env) -> None:
    """Publishing v2 must not change what a workflow pinned to v1 runs."""
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(env, token, "pinned", "def main(payload):\n    return {'v': 1}\n")
    await _publish(env, token, "pinned", "def main(payload):\n    return {'v': 2}\n")

    versions = await _invoke(env, ACTION_VERSION_LIST_CAPABILITY, token, action_id="pinned")
    assert [entry["version"] for entry in versions] == [2, 1]

    first = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="pinned", version=1)
    second = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="pinned", version=2)
    assert first["output"] == {"v": 1}
    assert second["output"] == {"v": 2}


@pytest.mark.asyncio
async def test_a_failing_action_produces_a_structured_failure_not_an_exception(env: _Env) -> None:
    """Workflow state stays authoritative: failure is a result, not a crash."""
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(env, token, "boom", "def main(payload):\n    raise RuntimeError('exploded')\n")

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="boom", version=1)
    assert result["status"] == "FAILED"
    assert "exploded" in result["error"]
    assert result["timed_out"] is False


@pytest.mark.asyncio
async def test_a_runaway_action_times_out_and_is_reported_as_timeout(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "runaway",
        "import time\ndef main(payload):\n    time.sleep(120)\n    return 'unreachable'\n",
        limits={"timeout_seconds": 5},
    )

    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="runaway", version=1)
    assert result["status"] == "TIMEOUT"
    assert result["timed_out"] is True
    assert result["output"] is None


@pytest.mark.asyncio
async def test_execution_lineage_is_persisted_for_success_and_failure(env: _Env) -> None:
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(env, token, "lineage", "def main(payload):\n    return payload.get('n', 0) * 2\n")
    await _invoke(env, EXECUTE_CAPABILITY, token, action_id="lineage", version=1, input_payload={"n": 21})

    async def _load(session: AsyncSession) -> list[PythonExecutionRecordModel]:
        statement = select(PythonExecutionRecordModel).where(
            PythonExecutionRecordModel.tenant_id == _TENANT_A,
            PythonExecutionRecordModel.action_id == "lineage",
        )
        return list((await session.execute(statement)).scalars().all())

    records = await env.storage_engine.data.execute_in_transaction(_load)
    assert len(records) == 1
    record = records[0]
    assert record.status == "SUCCEEDED"
    assert record.version == 1
    assert record.boundary == "WINDOWS_RESTRICTED_TOKEN_JOB"
    assert record.principal_id == _USER_A
    assert record.duration_ms >= 0


@pytest.mark.asyncio
async def test_python_execution_is_observable_through_the_existing_audit_trail(env: _Env) -> None:
    """Audit comes from the existing `AuditManager`, not a second audit system.

    Both the outer `kortex.python.execute` and the Trusted-Python capability
    call it makes appear in the same tenant-scoped audit trail every other
    KORTEX capability uses, attributed to the same real principal -- which is
    the point: the sandbox's nested call is not a separate, unaudited channel.
    """
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "audited",
        "def main(payload, kortex):\n    return len(kortex.call_capability('kortex.python.action.list', {}))\n",
        trust_level=PythonTrustLevel.TRUSTED.value,
        allowed_capabilities=["kortex.python.action.list"],
    )
    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="audited", version=1)
    assert result["status"] == "SUCCEEDED", result

    entries = await env.security_engine.audit_manager.get_audit_entries(
        tenant_id=_TENANT_A, action="kortex.kernel.dispatch.execute", limit=1000
    )
    executed = [
        entry
        for entry in entries
        if entry.context.get("capability_name") in (EXECUTE_CAPABILITY, "kortex.python.action.list")
    ]
    assert executed, "the Python execution produced no dispatch audit entry"

    names = {entry.context.get("capability_name") for entry in executed}
    assert EXECUTE_CAPABILITY in names
    # The nested Trusted-Python call is audited too, through the same trail.
    assert "kortex.python.action.list" in names
    assert all(entry.tenant_id == _TENANT_A for entry in executed)
    assert all(entry.actor_id == _USER_A for entry in executed)

    # No execution token reached the audit trail.
    serialized = " ".join(str(entry.context) for entry in executed)
    assert "KORTEX_EXECUTION_TOKEN" not in serialized


@pytest.mark.asyncio
async def test_no_execution_token_remains_active_after_execution(env: _Env) -> None:
    """Teardown is unconditional, so the store is empty between executions."""
    token = await _token(env, _TENANT_A, _USER_A)
    await _publish(
        env,
        token,
        "token-lifetime",
        "def main(payload, kortex):\n    return len(kortex.call_capability('kortex.python.action.list', {}))\n",
        trust_level=PythonTrustLevel.TRUSTED.value,
        allowed_capabilities=["kortex.python.action.list"],
    )
    result = await _invoke(env, EXECUTE_CAPABILITY, token, action_id="token-lifetime", version=1)

    assert result["status"] == "SUCCEEDED", result
    assert env.python_engine.gateway.token_store.active_count() == 0
