"""Adversarial tests for the Trusted-Python governed capability IPC bridge.

The property under test is identity derivation. A sandboxed action can name a
capability and pass parameters; it cannot name a tenant, a principal, a
workflow, an execution, or a session token. Everything authoritative comes from
the grant the presented execution token resolves to, and the nested call then
re-enters the real `CapabilityDispatcher`, which re-verifies the session token
and builds the `CapabilityExecutionContext` itself.

Every test drives the **real** `CapabilityBridge` against a **real** booted
Kernel with a real `SecurityEngine`, real RBAC, and the real dispatcher. The
only thing that varies is what an attacker-controlled sandbox sends.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel, KernelState
from kortex.engines.python_exec.engine import PythonExecutionEngine
from kortex.engines.python_exec.exceptions import ExecutionTokenError
from kortex.engines.python_exec.ipc import CapabilityBridge, ExecutionTokenStore
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.models import PrincipalRecord, RolePermissionRecord
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\x31" * 32
_TEST_SIGNING_KEY = b"\x32" * 32

_ROLE = "PYTHON_BRIDGE_TEST_ROLE"
_TENANT_A = "tenant-bridge-a"
_TENANT_B = "tenant-bridge-b"
_USER_A = "user-bridge-a"
_USER_B = "user-bridge-b"
_PASSWORD = "bridge-test-pass"

# The one capability a trusted action under test is permitted to call.
_PERMITTED = "kortex.python.action.list"
# Registered, reachable by the dispatcher, but absent from the allowlist.
_NOT_PERMITTED = "kortex.python.action.get"
# The capability an escalation attempt would most want.
_SECRET_CAPABILITY = "kortex.security.secret.get"


@dataclass
class _Env:
    kernel: Kernel
    security_engine: SecurityEngine
    store: ExecutionTokenStore
    bridge: CapabilityBridge


@pytest_asyncio.fixture
async def env(tmp_path: Path) -> AsyncIterator[_Env]:
    db_path = (tmp_path / f"bridge_{uuid4().hex[:8]}.db").as_posix()
    db_manager = DatabaseEngineManager(connection_url=f"sqlite+aiosqlite:///{db_path}")
    await db_manager.connect()
    await db_manager.create_all_tables()

    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_engine = StorageEngine(base_directory=str(tmp_path / f"storage_{uuid4().hex[:8]}"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    python_engine = PythonExecutionEngine(
        data_store=RelationalDataStore(db_manager), execution_root=tmp_path / "pyexec"
    )
    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    kernel.register_engine(python_engine)

    await kernel.boot()
    assert kernel.state == KernelState.RUNNING

    hasher = PasswordHasher()

    async def _seed(session: AsyncSession) -> None:
        for permission in ("python:read", "python:execute", "python:write"):
            session.add(RolePermissionRecord(id=str(uuid4()), role=_ROLE, permission=permission))
        for tenant_id, principal_id in ((_TENANT_A, _USER_A), (_TENANT_B, _USER_B)):
            session.add(
                PrincipalRecord(
                    id=str(uuid4()),
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    principal_type="USER",
                    enabled=True,
                    credential_hash=hasher.hash(_PASSWORD),
                    roles=[_ROLE],
                    attributes={"clearance_level": "RESTRICTED"},
                )
            )

    await storage_engine.data.execute_in_transaction(_seed)

    store = ExecutionTokenStore()
    try:
        yield _Env(
            kernel=kernel,
            security_engine=security_engine,
            store=store,
            bridge=CapabilityBridge(kernel, store),
        )
    finally:
        if kernel.state == KernelState.RUNNING:
            with contextlib.suppress(Exception):
                await kernel.shutdown()
        await db_manager.disconnect()


async def _principal_and_token(env: _Env, tenant_id: str, principal_id: str) -> tuple[Any, Any]:
    principal = await env.security_engine.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": _PASSWORD,
        }
    )
    token = await env.security_engine.authentication_manager.issue_token(principal)
    return principal, token


async def _grant(
    env: _Env,
    *,
    tenant_id: str = _TENANT_A,
    principal_id: str = _USER_A,
    capabilities: frozenset[str] = frozenset({_PERMITTED}),
    ttl_seconds: float = 60.0,
    workflow_id: str = "wf-1",
    execution_id: str = "exec-1",
) -> tuple[str, Any]:
    principal, session_token = await _principal_and_token(env, tenant_id, principal_id)
    issued = env.store.issue(
        tenant_id=tenant_id,
        action_id="bridge-action",
        version=1,
        allowed_capabilities=capabilities,
        ttl_seconds=ttl_seconds,
        principal=principal,
        session_token=session_token,
        workflow_id=workflow_id,
        execution_id=execution_id,
    )
    return issued.token, issued.grant


def _request(token: str, capability: str, parameters: dict[str, Any] | None = None) -> bytes:
    return json.dumps({"token": token, "capability": capability, "parameters": parameters or {}}).encode("utf-8")


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_token_and_permitted_capability_succeeds(env: _Env) -> None:
    token, _ = await _grant(env)
    response = await env.bridge.handle(_request(token, _PERMITTED))

    assert response["ok"] is True, response
    assert isinstance(response["result"], list)


@pytest.mark.asyncio
async def test_the_call_runs_as_the_grants_tenant_not_a_claimed_one(env: _Env) -> None:
    """A sandbox cannot redirect its own call into another tenant.

    Tenant A's action publishes an action, then calls `action.list` while
    *claiming* to be tenant B in every field it controls. The result must still
    be tenant A's data, because tenant identity is taken from the grant and the
    dispatcher, never from the request.
    """
    token, _ = await _grant(env, capabilities=frozenset({_PERMITTED, "kortex.python.action.publish"}))

    await env.bridge.handle(
        _request(
            token,
            "kortex.python.action.publish",
            {"action_id": "tenant-a-only", "name": "A", "source_code": "def main(p):\n    return 1\n"},
        )
    )

    response = await env.bridge.handle(
        _request(
            token,
            _PERMITTED,
            # Every identity field a hostile action could try to inject.
            {"tenant_id": _TENANT_B, "principal_id": _USER_B, "workflow_id": "wf-other"},
        )
    )
    assert response["ok"] is True, response
    listed = {entry["action_id"] for entry in response["result"]}
    assert listed == {"tenant-a-only"}
    assert all(entry["tenant_id"] == _TENANT_A for entry in response["result"])


# ---------------------------------------------------------------------------
# Token adversarial cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replayed_token_fails_after_the_execution_terminates(env: _Env) -> None:
    """A token is invalid the moment its execution ends.

    This is the mechanism behind "single-use": the Gateway revokes the token
    unconditionally in its teardown `finally`, so a token captured during an
    execution is worthless afterwards.
    """
    token, _ = await _grant(env)
    first = await env.bridge.handle(_request(token, _PERMITTED))
    assert first["ok"] is True

    # Exactly what the Gateway does when the execution terminates.
    env.store.revoke(token)

    replayed = await env.bridge.handle(_request(token, _PERMITTED))
    assert replayed["ok"] is False
    assert "not valid" in replayed["error"]


@pytest.mark.asyncio
async def test_expired_token_fails(env: _Env) -> None:
    token, _ = await _grant(env, ttl_seconds=0.05)
    time.sleep(0.1)

    response = await env.bridge.handle(_request(token, _PERMITTED))
    assert response["ok"] is False
    assert "not valid" in response["error"]


@pytest.mark.asyncio
async def test_forged_and_absent_tokens_fail(env: _Env) -> None:
    for candidate in ("", "not-a-real-token", "a" * 43, "../../etc/passwd"):
        response = await env.bridge.handle(_request(candidate, _PERMITTED))
        assert response["ok"] is False, candidate


@pytest.mark.asyncio
async def test_token_refusals_are_indistinguishable_from_each_other(env: _Env) -> None:
    """An unknown token and an expired token must report identically.

    A distinguishable error is an oracle: it would let a sandbox confirm that a
    guessed token value once existed.
    """
    expired_token, _ = await _grant(env, ttl_seconds=0.05)
    time.sleep(0.1)

    unknown = await env.bridge.handle(_request("definitely-not-issued", _PERMITTED))
    expired = await env.bridge.handle(_request(expired_token, _PERMITTED))
    assert unknown["error"] == expired["error"]


@pytest.mark.asyncio
async def test_tenant_a_cannot_use_tenant_b_token(env: _Env) -> None:
    """A token stolen across a tenant boundary still acts only for its owner."""
    token_b, grant_b = await _grant(env, tenant_id=_TENANT_B, principal_id=_USER_B)
    assert grant_b.tenant_id == _TENANT_B

    # Tenant A's sandbox presents tenant B's token and asks for A's data.
    response = await env.bridge.handle(_request(token_b, _PERMITTED, {"tenant_id": _TENANT_A}))
    assert response["ok"] is True
    # The call ran as tenant B -- the token's owner -- not as the claimed A.
    assert all(entry["tenant_id"] == _TENANT_B for entry in response["result"])


@pytest.mark.asyncio
async def test_the_plaintext_token_is_never_stored(env: _Env) -> None:
    """Only a SHA-256 digest is kept, so a store disclosure yields no tokens."""
    token, _ = await _grant(env)
    serialized = repr(env.store.__dict__)
    assert token not in serialized


@pytest.mark.asyncio
async def test_revocation_is_idempotent_and_safe(env: _Env) -> None:
    token, _ = await _grant(env)
    env.store.revoke(token)
    env.store.revoke(token)
    env.store.revoke("never-issued")
    assert env.store.active_count() == 0


@pytest.mark.asyncio
async def test_issued_tokens_are_unpredictable_and_unique(env: _Env) -> None:
    tokens = set()
    for _ in range(25):
        token, _ = await _grant(env)
        tokens.add(token)
    assert len(tokens) == 25
    assert all(len(token) >= 40 for token in tokens)


@pytest.mark.asyncio
async def test_direct_redeem_raises_for_an_invalid_token(env: _Env) -> None:
    with pytest.raises(ExecutionTokenError):
        env.store.redeem("not-issued")


# ---------------------------------------------------------------------------
# Capability authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_outside_the_allowlist_is_refused_before_dispatch(env: _Env) -> None:
    """Refused by the bridge, not by the dispatcher.

    `kortex.python.action.get` is registered and the grant's principal holds
    `python:read`, so this call *would* succeed if it reached the dispatcher.
    It must not: the action never declared it.
    """
    token, _ = await _grant(env, capabilities=frozenset({_PERMITTED}))
    response = await env.bridge.handle(_request(token, _NOT_PERMITTED, {"action_id": "anything"}))

    assert response["ok"] is False
    assert "not permitted for this Python Action" in response["error"]


@pytest.mark.asyncio
async def test_secret_retrieval_is_not_reachable_from_a_sandbox(env: _Env) -> None:
    """The capability an escalation would most want is refused."""
    token, _ = await _grant(env)
    response = await env.bridge.handle(_request(token, _SECRET_CAPABILITY, {"handle": "vault:anything"}))
    assert response["ok"] is False
    assert "not permitted" in response["error"]


@pytest.mark.asyncio
async def test_allowlist_matching_is_exact(env: _Env) -> None:
    token, _ = await _grant(env, capabilities=frozenset({_PERMITTED}))
    for candidate in (
        _PERMITTED.upper(),
        _PERMITTED + " ",
        " " + _PERMITTED,
        _PERMITTED + ".extra",
        "kortex.python.action",
        "kortex.python.*",
    ):
        response = await env.bridge.handle(_request(token, candidate))
        assert response["ok"] is False, candidate


@pytest.mark.asyncio
async def test_a_grant_with_no_session_authority_cannot_dispatch(env: _Env) -> None:
    """Fail-closed: no session token in the grant means no capability access."""
    issued = env.store.issue(
        tenant_id=_TENANT_A,
        action_id="bridge-action",
        version=1,
        allowed_capabilities=frozenset({_PERMITTED}),
        ttl_seconds=60.0,
        principal=None,
        session_token=None,
    )
    response = await env.bridge.handle(_request(issued.token, _PERMITTED))
    assert response["ok"] is False
    assert "no session authority" in response["error"]


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_requests_are_refused_without_raising(env: _Env) -> None:
    """The transport must always get an answer, never an exception."""
    malformed = [
        b"",
        b"not json at all",
        b"{",
        b'"a bare string"',
        b"[1, 2, 3]",
        b"null",
        json.dumps({"capability": _PERMITTED}).encode(),  # no token
        json.dumps({"token": "x"}).encode(),  # no capability
        json.dumps({"token": 42, "capability": _PERMITTED, "parameters": {}}).encode(),
        json.dumps({"token": "x", "capability": ["list"], "parameters": {}}).encode(),
        json.dumps({"token": "x", "capability": _PERMITTED, "parameters": "not-a-dict"}).encode(),
        b"\xff\xfe\x00invalid utf-8",
    ]
    for raw in malformed:
        response = await env.bridge.handle(raw)
        assert response["ok"] is False, raw
        assert isinstance(response["error"], str)


@pytest.mark.asyncio
async def test_a_failing_capability_is_reported_not_raised(env: _Env) -> None:
    """A downstream failure becomes a refusal document, not a bridge crash."""
    token, _ = await _grant(env, capabilities=frozenset({"kortex.python.action.get"}))
    response = await env.bridge.handle(_request(token, "kortex.python.action.get", {"action_id": "missing"}))

    assert response["ok"] is False
    assert "PythonActionNotFoundError" in response["error"]


# ---------------------------------------------------------------------------
# Token must never be logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_token_value_ever_reaches_a_log_record(env: _Env, caplog: pytest.LogCaptureFixture) -> None:
    """Across success, refusal, and malformed paths.

    Captured at DEBUG on the root logger so this covers every module involved
    -- the bridge, the token store, and the dispatcher underneath it.
    """
    token, grant = await _grant(env, capabilities=frozenset({_PERMITTED}))

    with caplog.at_level(logging.DEBUG):
        await env.bridge.handle(_request(token, _PERMITTED))
        await env.bridge.handle(_request(token, _SECRET_CAPABILITY))
        await env.bridge.handle(_request(token, "kortex.python.action.get", {"action_id": "nope"}))
        await env.bridge.handle(b"{malformed")
        env.store.revoke(token)
        await env.bridge.handle(_request(token, _PERMITTED))

    captured = "\n".join(record.getMessage() for record in caplog.records)
    assert token not in captured, "the execution token appeared in a log record"
    # The non-secret correlation handle is what operators get instead.
    assert grant.token_id not in ("", None)


@pytest.mark.asyncio
async def test_refusal_messages_do_not_echo_the_token(env: _Env) -> None:
    token, _ = await _grant(env)
    responses = [
        await env.bridge.handle(_request(token, _SECRET_CAPABILITY)),
        await env.bridge.handle(_request(token, "kortex.python.action.get", {"action_id": "nope"})),
    ]
    for response in responses:
        assert token not in json.dumps(response)


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_bridge_requests_do_not_cross_identities(env: _Env) -> None:
    """Two executions running at once keep their own tenants.

    The bridge holds no per-request state on itself, so this pins that a
    future refactor introducing some cannot silently leak identity between
    concurrent executions.
    """
    token_a, _ = await _grant(env, tenant_id=_TENANT_A, principal_id=_USER_A)
    token_b, _ = await _grant(env, tenant_id=_TENANT_B, principal_id=_USER_B)

    responses = await asyncio.gather(
        *[env.bridge.handle(_request(token_a if index % 2 == 0 else token_b, _PERMITTED)) for index in range(20)]
    )
    for index, response in enumerate(responses):
        assert response["ok"] is True
        expected = _TENANT_A if index % 2 == 0 else _TENANT_B
        assert all(entry["tenant_id"] == expected for entry in response["result"])
