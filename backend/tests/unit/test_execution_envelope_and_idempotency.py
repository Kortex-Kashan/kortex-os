"""
KORTEX Milestone M5.2 Unit Test Suite.

Tests Canonical Execution Envelope, Dispatcher-Level Idempotency, Concurrent Duplicate
Protection, Tenant Isolation, Secret-Scrubbed Persistence, Execution Audit Lineage,
and Transactional Event Outbox Foundation.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from argon2 import PasswordHasher
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.api.errors import map_exception
from kortex.api.main import _invoke
from kortex.api.schemas import IpcCapabilityRequest
from kortex.api.token_codec import encode_token
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityRequest
from kortex.core.exceptions import ConcurrentExecutionError, IdempotencyError
from kortex.core.idempotency import (
    IdempotencyState,
)
from kortex.core.kernel import Kernel
from kortex.core.outbox import EventOutboxStatus, OutboxStore
from kortex.engines.event.engine import EventEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import (
    PrincipalRecord,
    RolePermissionRecord,
    TokenPayload,
)
from kortex.engines.storage.engine import StorageEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.storage.stores.data_store import RelationalDataStore

_TEST_MASTER_KEY = b"\xaa" * 32
_TEST_SIGNING_KEY = b"\xbb" * 32


# =============================================================================
# Helper Utilities & Fixtures
# =============================================================================


class _CounterSpy:
    """Spy callable that records invocations and returns customizable results."""

    def __init__(self, return_value: Any = None, sleep_s: float = 0.0) -> None:
        self.call_count = 0
        self.invocations: list[dict[str, Any]] = []
        self.return_value = return_value or {"status": "ok"}
        self.sleep_s = sleep_s

    async def __call__(self, **kwargs: Any) -> Any:
        self.call_count += 1
        self.invocations.append(dict(kwargs))
        if self.sleep_s > 0:
            await asyncio.sleep(self.sleep_s)
        if isinstance(self.return_value, Exception):
            raise self.return_value
        return self.return_value


async def _create_test_db(tmp_path: Path, name: str) -> DatabaseEngineManager:
    """Create an isolated, clean file-based SQLite database for testing."""
    db_file = tmp_path / f"{name}_{uuid.uuid4().hex[:8]}.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    db = DatabaseEngineManager(connection_url=db_url)
    await db.connect()
    await db.create_all_tables()
    return db


async def _build_authenticated_kernel(
    tmp_path: Path,
    test_name: str,
) -> tuple[Kernel, StorageEngine, SecurityEngine]:
    """Build a clean Kernel with Storage, Security, and isolated DB before boot."""
    db_manager = await _create_test_db(tmp_path, test_name)
    kernel = Kernel()
    kernel._db_manager = db_manager

    storage_dir = tmp_path / f"storage_{test_name}_{uuid.uuid4().hex[:8]}"
    storage_engine = StorageEngine(base_directory=str(storage_dir))
    security_engine = SecurityEngine(
        master_key=_TEST_MASTER_KEY,
        signing_private_key=_TEST_SIGNING_KEY,
    )

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    return kernel, storage_engine, security_engine


async def _seed_user_with_permission(
    data_store: IDataStore,
    security_engine: SecurityEngine,
    tenant_id: str,
    principal_id: str,
    permission: str,
) -> TokenPayload:
    """Seed principal, role, and permission, then issue a valid TokenPayload."""
    role_name = f"role_{tenant_id}_{uuid.uuid4().hex[:6]}"

    async def _action(session: AsyncSession) -> None:
        p_record = PrincipalRecord(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            principal_id=principal_id,
            principal_type="USER",
            enabled=True,
            credential_hash=PasswordHasher().hash("test-password"),
            roles=[role_name],
            attributes={"clearance_level": "RESTRICTED"},
        )
        session.add(p_record)
        r_record = RolePermissionRecord(
            id=str(uuid.uuid4()),
            role=role_name,
            permission=permission,
        )
        session.add(r_record)

    await data_store.execute_in_transaction(_action)

    principal = await security_engine.authentication_manager.authenticate(
        {
            "principal_type": "USER",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "password": "test-password",
        }
    )
    return await security_engine.authentication_manager.issue_token(principal)


# =============================================================================
# Category A: Execution Envelope Defaults & Explicit Identity
# =============================================================================


def test_capability_request_envelope_defaults() -> None:
    """Verify auto-generation of request_id and correlation_id as distinct UUIDs."""
    req1 = CapabilityRequest(capability_name="test.noop")
    req2 = CapabilityRequest(capability_name="test.noop")

    assert req1.request_id is not None
    assert uuid.UUID(req1.request_id)
    assert req1.correlation_id is not None
    assert uuid.UUID(req1.correlation_id)
    assert req1.idempotency_key is None

    # Distinct per instance
    assert req1.request_id != req2.request_id
    assert req1.correlation_id != req2.correlation_id


def test_capability_request_envelope_explicit_values() -> None:
    """Verify explicitly provided envelope IDs and idempotency_key are preserved."""
    r_id = "req-12345"
    c_id = "corr-67890"
    i_key = "idemp-abcde"

    req = CapabilityRequest(
        request_id=r_id,
        correlation_id=c_id,
        idempotency_key=i_key,
        capability_name="test.order.create",
        parameters={"amount": 100},
    )

    assert req.request_id == r_id
    assert req.correlation_id == c_id
    assert req.idempotency_key == i_key
    assert req.capability_name == "test.order.create"
    assert req.parameters == {"amount": 100}


# =============================================================================
# Category B: Transport Wire Propagation (API -> CapabilityRequest)
# =============================================================================


@pytest.mark.asyncio
async def test_transport_propagation_to_capability_request(tmp_path: Path) -> None:
    """Verify api/main.py _invoke cleanly passes request_id, correlation_id, and idempotency_key."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "transport_prop")

    captured_requests: list[CapabilityRequest] = []

    async def _inspecting_handler(**kwargs: Any) -> dict[str, str]:
        return {"result": "ok"}

    kernel.register_capability(
        name="test.transport.propagate",
        description="Verify wire propagation",
        provider="test",
        handler=_inspecting_handler,
        requires_authentication=True,
        required_permissions=["test:propagate"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-wire", "user-wire", "test:propagate"
    )
    token_str = encode_token(token)

    # Intercept dispatch to inspect the created CapabilityRequest
    original_dispatch = kernel._dispatcher.dispatch

    async def _intercept(request: CapabilityRequest) -> Any:
        captured_requests.append(request)
        return await original_dispatch(request)

    kernel._dispatcher.dispatch = _intercept  # type: ignore[method-assign]

    ipc_req = IpcCapabilityRequest(
        request_id="wire-req-001",
        correlation_id="wire-corr-002",
        idempotency_key="wire-key-003",
        capability_name="test.transport.propagate",
        parameters={"foo": "bar"},
    )

    result_envelope, _, status_code = await _invoke(
        kernel=kernel,
        ipc_request=ipc_req,
        session_token_blob=token_str,
    )

    assert status_code == 200
    assert result_envelope.status == "SUCCESS"
    assert len(captured_requests) == 1

    dispatched = captured_requests[0]
    assert dispatched.request_id == "wire-req-001"
    assert dispatched.correlation_id == "wire-corr-002"
    assert dispatched.idempotency_key == "wire-key-003"
    assert dispatched.capability_name == "test.transport.propagate"
    assert dispatched.context["resource_tenant_id"] == "tenant-wire"

    await kernel.shutdown()


# =============================================================================
# Category C: Security Ordering (AuthN / AuthZ Before Idempotency Gate)
# =============================================================================


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected_before_idempotency_lookup(tmp_path: Path) -> None:
    """Unauthenticated request must fail with AuthenticationError without touching idempotency."""
    kernel, _, _ = await _build_authenticated_kernel(tmp_path, "sec_order_authn")

    spy = _CounterSpy({"data": "secret_result"})
    kernel.register_capability(
        name="test.secure.operation",
        description="Requires authentication",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["test:operate"],
    )
    await kernel.boot()

    # Dispatch with idempotency_key but no session_token
    request = CapabilityRequest(
        capability_name="test.secure.operation",
        idempotency_key="unauth-key-1",
        session_token=None,
        context={"resource_tenant_id": "tenant-sec"},
    )

    with pytest.raises(AuthenticationError):
        await kernel.invoke_capability(request)

    assert spy.call_count == 0

    # Verify no record was created in idempotency store
    idemp_store = kernel._dispatcher._resolve_idempotency_store()
    assert idemp_store is not None
    record = await idemp_store.get_record("tenant-sec", "unauth-key-1")
    assert record is None

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_unauthorized_request_cannot_retrieve_cached_idempotent_result(tmp_path: Path) -> None:
    """An unauthorized caller with valid token cannot read another tenant's or role's cached result."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "sec_order_authz")

    spy = _CounterSpy({"order_id": "ord-999"})
    kernel.register_capability(
        name="test.protected.action",
        description="Requires permission test:action",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["test:action"],
    )
    await kernel.boot()

    # 1. User A (Authorized) executes with key-123
    token_a = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-alpha", "user-a", "test:action"
    )

    req_a = CapabilityRequest(
        capability_name="test.protected.action",
        idempotency_key="shared-idemp-key",
        session_token=token_a,
        context={"resource_tenant_id": "tenant-alpha"},
    )
    res_a = await kernel.invoke_capability(req_a)
    assert res_a == {"order_id": "ord-999"}
    assert spy.call_count == 1

    # 2. User B (Lacks test:action) attempts to call using the same idempotency key under tenant-alpha
    token_b = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-alpha", "user-b", "unrelated:perm"
    )

    req_b = CapabilityRequest(
        capability_name="test.protected.action",
        idempotency_key="shared-idemp-key",
        session_token=token_b,
        context={"resource_tenant_id": "tenant-alpha"},
    )

    # Must be denied at authorization step — must NEVER return User A's cached result!
    with pytest.raises(AuthorizationDeniedError):
        await kernel.invoke_capability(req_b)

    # Handler was still not called again
    assert spy.call_count == 1

    await kernel.shutdown()


# =============================================================================
# Category D: Idempotency State Transitions & Duplicate Replay
# =============================================================================


@pytest.mark.asyncio
async def test_idempotent_first_execution_and_subsequent_replay(tmp_path: Path) -> None:
    """First call executes handler; second call with same key replays cached result without executing handler."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "idemp_replay")

    spy = _CounterSpy({"invoice_id": "inv-001", "total": 250})
    kernel.register_capability(
        name="test.billing.charge",
        description="Billing charge",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["billing:charge"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-1", "user-1", "billing:charge"
    )

    req1 = CapabilityRequest(
        capability_name="test.billing.charge",
        idempotency_key="charge-user-001",
        session_token=token,
        parameters={"amount": 250},
        context={"resource_tenant_id": "tenant-1"},
    )

    res1 = await kernel.invoke_capability(req1)
    assert res1 == {"invoice_id": "inv-001", "total": 250}
    assert spy.call_count == 1

    # Replay with identical key
    req2 = CapabilityRequest(
        capability_name="test.billing.charge",
        idempotency_key="charge-user-001",
        session_token=token,
        parameters={"amount": 250},
        context={"resource_tenant_id": "tenant-1"},
    )

    res2 = await kernel.invoke_capability(req2)
    # Identical deterministic response
    assert res2 == {"invoice_id": "inv-001", "total": 250}
    # Handler was NOT invoked a second time
    assert spy.call_count == 1

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_concurrent_processing_collision_rejected_with_conflict(tmp_path: Path) -> None:
    """When a request is already in PROCESSING state, concurrent duplicate raises ConcurrentExecutionError."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "concurrent_collision")

    # Handler sleeps for 0.3s to allow concurrent request to arrive
    spy = _CounterSpy({"result": "delayed_done"}, sleep_s=0.3)
    kernel.register_capability(
        name="test.slow.mutation",
        description="Slow mutation",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["slow:mutate"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-slow", "user-slow", "slow:mutate"
    )

    key = "concurrent-key-1"

    req1 = CapabilityRequest(
        capability_name="test.slow.mutation",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-slow"},
    )
    req2 = CapabilityRequest(
        capability_name="test.slow.mutation",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-slow"},
    )

    # Launch req1
    task1 = asyncio.create_task(kernel.invoke_capability(req1))
    await asyncio.sleep(0.05)  # ensure req1 claims PROCESSING

    # Launch req2 while req1 is still PROCESSING
    with pytest.raises(ConcurrentExecutionError) as exc_info:
        await kernel.invoke_capability(req2)

    assert "currently being processed" in str(exc_info.value)

    # Wait for req1 to finish successfully
    res1 = await task1
    assert res1 == {"result": "delayed_done"}

    # Handler was executed exactly once
    assert spy.call_count == 1

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_concurrent_fresh_key_claims_never_raise_unhandled_error(tmp_path: Path) -> None:
    """M5-A4 regression: N callers racing a genuinely NEW idempotency key at
    the same instant (a double-click, or a client retrying a request whose
    response it never saw) must resolve deterministically — exactly one
    CLAIMED execution, the rest a clean `ConcurrentExecutionError` — never an
    unhandled `InvalidRequestError`/500 from the loser's INSERT colliding
    with the winner's.

    Unlike `test_concurrent_processing_collision_rejected_with_conflict`
    above (which deliberately sequences req1 before req2 via `asyncio.sleep`
    so req1's row already exists — a real but different scenario), this
    launches every call with `asyncio.gather` and NO artificial ordering, so
    the actual concurrent-INSERT race is exercised, not simulated.
    """
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "concurrent_fresh_claim")

    spy = _CounterSpy({"result": "done"}, sleep_s=0.05)
    kernel.register_capability(
        name="test.race.fresh_claim",
        description="Raced by many concurrent callers on one brand-new key",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["race:claim"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-race", "user-race", "race:claim"
    )

    key = f"fresh-race-key-{uuid.uuid4().hex[:8]}"
    concurrency = 8

    async def _attempt() -> Any:
        request = CapabilityRequest(
            capability_name="test.race.fresh_claim",
            idempotency_key=key,
            session_token=token,
            context={"resource_tenant_id": "tenant-race"},
        )
        return await kernel.invoke_capability(request)

    results = await asyncio.gather(*(_attempt() for _ in range(concurrency)), return_exceptions=True)

    unexpected = [r for r in results if isinstance(r, BaseException) and not isinstance(r, ConcurrentExecutionError)]
    assert unexpected == [], f"Unhandled exception(s) from concurrent claim race: {unexpected!r}"

    successes = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, ConcurrentExecutionError)]

    # Every one of the 8 concurrent callers resolves to exactly one of two
    # legitimate outcomes — a `ConcurrentExecutionError` (arrived while the
    # winner was still mid-flight), or `{"result": "done"}` (arrived after
    # the winner already completed, and got the deterministic cached
    # replay — NOT a fresh execution; see the `spy.call_count` assertion
    # below, which is the actual "no double-execution" proof). Which of the
    # two a given caller gets depends on exact timing and is not itself
    # asserted; both are correct.
    assert len(successes) + len(conflicts) == concurrency
    assert len(successes) >= 1, "At least one concurrent caller must win the claim"
    assert all(r == {"result": "done"} for r in successes)

    # The actual "no double-execution" guarantee: regardless of how many
    # callers observed a success (fresh or replayed), the underlying side
    # effect ran exactly once.
    assert spy.call_count == 1

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_stale_processing_record_is_reclaimed_after_lease_expiry(tmp_path: Path) -> None:
    """M5-A4 regression: a PROCESSING record whose original caller vanished
    (crashed, was killed — no exception handler ever ran to call
    record_failed) must not block that idempotency key forever. Once older
    than `STALE_PROCESSING_LEASE_SECONDS`, a fresh caller may reclaim it and
    actually execute; a record that is merely still within its lease window
    must NOT be reclaimed out from under a genuinely still-running caller.
    """
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "stale_reclaim")

    spy = _CounterSpy({"result": "reclaimed_done"})
    kernel.register_capability(
        name="test.reclaim.stale",
        description="Reclaimed after its original caller vanished",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["reclaim:stale"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-reclaim", "user-reclaim", "reclaim:stale"
    )
    key = "stale-key-1"

    idemp_store = kernel._dispatcher._resolve_idempotency_store()
    assert idemp_store is not None

    # Simulate an abandoned claim: a PROCESSING row whose caller never came
    # back to complete or fail it, well outside the lease window.
    from kortex.core.idempotency import STALE_PROCESSING_LEASE_SECONDS, IdempotencyRecordModel

    ancient = datetime.now(UTC) - timedelta(seconds=STALE_PROCESSING_LEASE_SECONDS + 60)

    async def _seed_abandoned(session: AsyncSession) -> None:
        session.add(
            IdempotencyRecordModel(
                id=str(uuid.uuid4()),
                tenant_id="tenant-reclaim",
                idempotency_key=key,
                capability_name="test.reclaim.stale",
                request_id="abandoned-req",
                correlation_id="abandoned-corr",
                state=IdempotencyState.PROCESSING.value,
                created_at=ancient,
                updated_at=ancient,
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_abandoned)

    request = CapabilityRequest(
        capability_name="test.reclaim.stale",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-reclaim"},
    )
    result = await kernel.invoke_capability(request)
    assert result == {"result": "reclaimed_done"}
    assert spy.call_count == 1

    # A record still well inside its lease window must NOT be reclaimable —
    # this is a currently-running (or at least plausibly-running) claim, not
    # an abandoned one.
    fresh_key = "not-stale-key-1"
    recent = datetime.now(UTC) - timedelta(seconds=5)

    async def _seed_fresh_processing(session: AsyncSession) -> None:
        session.add(
            IdempotencyRecordModel(
                id=str(uuid.uuid4()),
                tenant_id="tenant-reclaim",
                idempotency_key=fresh_key,
                capability_name="test.reclaim.stale",
                request_id="in-flight-req",
                correlation_id="in-flight-corr",
                state=IdempotencyState.PROCESSING.value,
                created_at=recent,
                updated_at=recent,
            )
        )

    await storage_engine.data.execute_in_transaction(_seed_fresh_processing)

    fresh_request = CapabilityRequest(
        capability_name="test.reclaim.stale",
        idempotency_key=fresh_key,
        session_token=token,
        context={"resource_tenant_id": "tenant-reclaim"},
    )
    with pytest.raises(ConcurrentExecutionError):
        await kernel.invoke_capability(fresh_request)
    assert spy.call_count == 1  # unchanged: the in-lease record was not touched

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_client_timeout_cancellation_does_not_strand_processing_record(tmp_path: Path) -> None:
    """M5-A4 regression: when the dispatcher's own coroutine is cancelled
    mid-handler (the exact effect of `asyncio.wait_for(..., timeout=...)` in
    `api/main.py`'s request-timeout enforcement — `asyncio.CancelledError`,
    a `BaseException`, not an `Exception`), the idempotency record must
    still transition to FAILED, not remain stuck in PROCESSING forever.
    """
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "cancel_no_strand")

    slow_spy = _CounterSpy({"result": "too_late"}, sleep_s=5.0)
    kernel.register_capability(
        name="test.cancel.slow",
        description="Slower than the caller's patience",
        provider="test",
        handler=slow_spy,
        requires_authentication=True,
        required_permissions=["cancel:slow"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-cancel", "user-cancel", "cancel:slow"
    )
    key = "cancel-key-1"

    request = CapabilityRequest(
        capability_name="test.cancel.slow",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-cancel"},
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(kernel.invoke_capability(request), timeout=0.1)

    # Give the dispatcher's own except-block cleanup a moment to finish
    # running (it executes as part of unwinding the cancelled task).
    await asyncio.sleep(0.05)

    idemp_store = kernel._dispatcher._resolve_idempotency_store()
    assert idemp_store is not None
    record = await idemp_store.get_record("tenant-cancel", key)
    assert record is not None
    # FAILED, not stranded in PROCESSING: `record_failed` ran during the
    # dispatcher's cancellation cleanup, so a fresh retry can immediately
    # claim it (proven generically by `test_failed_execution_allows_atomic_retry_claim`)
    # rather than being permanently blocked behind a claim nobody will ever complete.
    assert record.state == IdempotencyState.FAILED.value

    await kernel.shutdown()


@pytest.mark.asyncio
async def test_failed_execution_allows_atomic_retry_claim(tmp_path: Path) -> None:
    """Failed execution records FAILED state; retry atomically transitions FAILED -> PROCESSING -> COMPLETED."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "retry_failed")

    failing_spy = _CounterSpy(return_value=RuntimeError("Transient gateway failure"))
    kernel.register_capability(
        name="test.flaky.service",
        description="Flaky service",
        provider="test",
        handler=failing_spy,
        requires_authentication=True,
        required_permissions=["flaky:call"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-flaky", "user-flaky", "flaky:call"
    )

    key = "retry-flaky-key-1"

    req1 = CapabilityRequest(
        capability_name="test.flaky.service",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-flaky"},
    )

    # 1. First attempt fails
    with pytest.raises(RuntimeError):
        await kernel.invoke_capability(req1)

    assert failing_spy.call_count == 1

    idemp_store = kernel._dispatcher._resolve_idempotency_store()
    assert idemp_store is not None
    rec = await idemp_store.get_record("tenant-flaky", key)
    assert rec is not None
    assert rec.state == IdempotencyState.FAILED.value
    assert "Transient gateway failure" in (rec.error_message or "")

    # 2. Fix the service for retry
    failing_spy.return_value = {"status": "recovered"}

    req2 = CapabilityRequest(
        capability_name="test.flaky.service",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-flaky"},
    )

    res2 = await kernel.invoke_capability(req2)
    assert res2 == {"status": "recovered"}
    assert failing_spy.call_count == 2

    # Verify final state is COMPLETED
    rec_after = await idemp_store.get_record("tenant-flaky", key)
    assert rec_after is not None
    assert rec_after.state == IdempotencyState.COMPLETED.value
    assert rec_after.error_message is None

    await kernel.shutdown()


# =============================================================================
# Category E: Strict Multi-Tenant Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_tenant_isolation_for_identical_idempotency_keys(tmp_path: Path) -> None:
    """The same idempotency key across different tenants must NOT collide or share state."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "tenant_isolation")

    spy = _CounterSpy()
    kernel.register_capability(
        name="test.tenant.mutation",
        description="Tenant mutation",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["tenant:mutate"],
    )
    await kernel.boot()

    token_a = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-A", "user-A", "tenant:mutate"
    )
    token_b = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-B", "user-B", "tenant:mutate"
    )

    key = "reused-order-key-100"

    # Tenant A invokes
    spy.return_value = {"owner": "tenant-A"}
    req_a = CapabilityRequest(
        capability_name="test.tenant.mutation",
        idempotency_key=key,
        session_token=token_a,
        context={"resource_tenant_id": "tenant-A"},
    )
    res_a = await kernel.invoke_capability(req_a)
    assert res_a == {"owner": "tenant-A"}
    assert spy.call_count == 1

    # Tenant B invokes with the EXACT same idempotency key
    spy.return_value = {"owner": "tenant-B"}
    req_b = CapabilityRequest(
        capability_name="test.tenant.mutation",
        idempotency_key=key,
        session_token=token_b,
        context={"resource_tenant_id": "tenant-B"},
    )
    res_b = await kernel.invoke_capability(req_b)
    # Must execute independently and NOT return Tenant A's result
    assert res_b == {"owner": "tenant-B"}
    assert spy.call_count == 2

    # Check both records exist in DB partitioned by tenant_id
    idemp_store = kernel._dispatcher._resolve_idempotency_store()
    assert idemp_store is not None
    rec_a = await idemp_store.get_record("tenant-A", key)
    rec_b = await idemp_store.get_record("tenant-B", key)

    assert rec_a is not None and rec_b is not None
    assert rec_a.tenant_id == "tenant-A"
    assert rec_b.tenant_id == "tenant-B"
    assert rec_a.id != rec_b.id

    await kernel.shutdown()


# =============================================================================
# Category F: Persistence Constraints & Secret Sanitization
# =============================================================================


@pytest.mark.asyncio
async def test_sensitive_tokens_scrubbed_from_persisted_idempotency_response(tmp_path: Path) -> None:
    """Credentials, tokens, and passwords must never be persisted into idempotency response_json."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "sanitize_secrets")

    sensitive_result = {
        "user_id": "usr-123",
        "session_token": "secret-jwt-token-val",
        "password": "SuperSecretPassword123!",
        "api_key": "kortex_live_sk_12345",
        "bearer_token": "bearer-token-abc",
        "nested": {
            "credentials": "top_secret_creds",
            "safe_field": "public_val",
        },
    }

    spy = _CounterSpy(return_value=sensitive_result)
    kernel.register_capability(
        name="test.secret.producer",
        description="Produces secrets",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["secret:read"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-sec", "user-sec", "secret:read"
    )

    key = "secret-sanitize-key"
    req = CapabilityRequest(
        capability_name="test.secret.producer",
        idempotency_key=key,
        session_token=token,
        context={"resource_tenant_id": "tenant-sec"},
    )

    await kernel.invoke_capability(req)

    idemp_store = kernel._dispatcher._resolve_idempotency_store()
    assert idemp_store is not None
    rec = await idemp_store.get_record("tenant-sec", key)
    assert rec is not None
    assert rec.response_json is not None

    persisted_json = rec.response_json
    assert "SuperSecretPassword123!" not in persisted_json
    assert "secret-jwt-token-val" not in persisted_json
    assert "kortex_live_sk_12345" not in persisted_json
    assert "top_secret_creds" not in persisted_json
    assert "public_val" in persisted_json

    await kernel.shutdown()


# =============================================================================
# Category G: Execution Audit Lineage & Replay Distinction
# =============================================================================


@pytest.mark.asyncio
async def test_execution_audit_lineage_and_replay_distinction(tmp_path: Path) -> None:
    """Execution logs kortex.kernel.dispatch.execute with lineage; replay does not record false execution."""
    kernel, storage_engine, security_engine = await _build_authenticated_kernel(tmp_path, "audit_lineage")

    spy = _CounterSpy({"data": "payload-1"})
    kernel.register_capability(
        name="test.audited.action",
        description="Audited action",
        provider="test",
        handler=spy,
        requires_authentication=True,
        required_permissions=["audit:test"],
    )
    await kernel.boot()

    token = await _seed_user_with_permission(
        storage_engine.data, security_engine, "tenant-aud", "user-aud", "audit:test"
    )

    req_id = "req-audit-1"
    corr_id = "corr-audit-2"
    idemp_key = "idemp-audit-3"

    req1 = CapabilityRequest(
        request_id=req_id,
        correlation_id=corr_id,
        idempotency_key=idemp_key,
        capability_name="test.audited.action",
        session_token=token,
        parameters={"param1": "val1", "password": "sensitive_password"},
        context={"resource_tenant_id": "tenant-aud"},
    )

    await kernel.invoke_capability(req1)

    # Query audit trail
    entries = await security_engine.audit_manager.get_audit_entries("tenant-aud")
    exec_entries = [e for e in entries if e.action == "kortex.kernel.dispatch.execute"]

    assert len(exec_entries) == 1
    audit = exec_entries[0]
    assert audit.resource_id == "test.audited.action"
    assert audit.actor_id == "user-aud"
    assert audit.tenant_id == "tenant-aud"
    assert audit.new_state_hash is not None

    ctx = audit.context
    assert ctx["request_id"] == req_id
    assert ctx["correlation_id"] == corr_id
    assert ctx["idempotency_key"] == idemp_key
    assert ctx["status"] == "SUCCESS"
    assert ctx["duration_ms"] >= 0
    # Parameter secrets scrubbed
    assert ctx["parameters"]["param1"] == "val1"
    assert ctx["parameters"]["password"] is None

    # Now replay with the same key
    req2 = CapabilityRequest(
        request_id="req-replay-99",
        correlation_id="corr-replay-99",
        idempotency_key=idemp_key,
        capability_name="test.audited.action",
        session_token=token,
        context={"resource_tenant_id": "tenant-aud"},
    )
    await kernel.invoke_capability(req2)

    # Check audit entries again — COMPLETED replay must NOT add another execution audit
    entries_after = await security_engine.audit_manager.get_audit_entries("tenant-aud")
    exec_entries_after = [e for e in entries_after if e.action == "kortex.kernel.dispatch.execute"]
    assert len(exec_entries_after) == 1  # Still exactly 1, no duplicate execution fabricated!

    await kernel.shutdown()


# =============================================================================
# Category H: Minimal Transactional Outbox
# =============================================================================


@pytest.mark.asyncio
async def test_transactional_outbox_staging_and_dispatch(tmp_path: Path) -> None:
    """Verify outbox stages events in DB, supports atomic rollback, and dispatches to EventEngine."""
    db_manager = await _create_test_db(tmp_path, "outbox_test")
    data_store = RelationalDataStore(db_manager)
    outbox = OutboxStore(data_store)
    event_engine = EventEngine()

    received_events: list[dict[str, Any]] = []

    async def _on_order_created(event: Any) -> None:
        received_events.append(event.payload)

    event_engine.subscribe("order.created", _on_order_created)

    # 1. Stage event in outbox
    staged = await outbox.stage_event(
        tenant_id="tenant-outbox",
        topic="order.created",
        payload={"order_id": "ord-1", "amount": 99.9, "session_token": "secret-tok"},
    )

    assert staged.status == EventOutboxStatus.PENDING.value
    assert staged.tenant_id == "tenant-outbox"
    assert staged.topic == "order.created"

    # Pending query
    pending = await outbox.get_pending_events(tenant_id="tenant-outbox")
    assert len(pending) == 1
    assert pending[0].id == staged.id

    # 2. Sweep & dispatch
    dispatched_count = await outbox.dispatch_pending(event_engine)
    assert dispatched_count == 1

    # Verify event published to EventEngine
    assert len(received_events) == 1
    assert received_events[0]["order_id"] == "ord-1"
    # Verify secret scrubbed from payload
    assert received_events[0]["session_token"] is None

    # Verify status changed to SENT
    pending_after = await outbox.get_pending_events(tenant_id="tenant-outbox")
    assert len(pending_after) == 0

    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_transactional_outbox_atomic_rollback(tmp_path: Path) -> None:
    """When an enclosing transaction fails, staged outbox events roll back with it."""
    db_manager = await _create_test_db(tmp_path, "outbox_rollback")
    data_store = RelationalDataStore(db_manager)
    outbox = OutboxStore(data_store)

    async def _failing_transaction(session: AsyncSession) -> None:
        outbox.stage_event_in_session(
            session=session,
            tenant_id="tenant-rb",
            topic="state.changed",
            payload={"action": "mutate"},
        )
        raise RuntimeError("Simulated transaction failure")

    with pytest.raises(RuntimeError):
        await data_store.execute_in_transaction(_failing_transaction)

    # Verify outbox event was rolled back
    pending = await outbox.get_pending_events(tenant_id="tenant-rb")
    assert len(pending) == 0

    await db_manager.disconnect()


# =============================================================================
# Category I: API Error Mapping (409 Conflict)
# =============================================================================


def test_api_error_mapping_for_concurrent_and_idempotency_errors() -> None:
    """Verify map_exception translates ConcurrentExecutionError to HTTP 409 Conflict."""
    exc1 = ConcurrentExecutionError("Concurrent duplicate operation in progress")
    mapping1 = map_exception(exc1)
    assert mapping1.http_status == 409
    assert mapping1.category == "EXECUTION_FAILED"

    exc2 = IdempotencyError("Idempotency conflict")
    mapping2 = map_exception(exc2)
    assert mapping2.http_status == 409
    assert mapping2.category == "EXECUTION_FAILED"


# =============================================================================
# Category J: Concurrency & Database-Level Isolation Invariants
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_retries_of_failed_record_race_condition(tmp_path: Path) -> None:
    """When two concurrent callers simultaneously retry a FAILED record, exactly one wins the claim."""
    from kortex.core.idempotency import ClaimResult, IdempotencyRecordModel, IdempotencyStore

    db_manager = await _create_test_db(tmp_path, "concurrent_retries")
    data_store = RelationalDataStore(db_manager)
    store = IdempotencyStore(data_store)

    tenant_id = "tenant-race"
    key = "racing-retry-key"

    # 1. Seed a FAILED record directly
    async def _seed_failed(session: AsyncSession) -> None:
        rec = IdempotencyRecordModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            idempotency_key=key,
            capability_name="test.retry.target",
            request_id="initial-req",
            correlation_id="initial-corr",
            state=IdempotencyState.FAILED.value,
            error_message="Initial failure",
        )
        session.add(rec)

    await data_store.execute_in_transaction(_seed_failed)

    # 2. Race two simultaneous claim attempts
    res1, res2 = await asyncio.gather(
        store.claim_or_get_execution(tenant_id, key, "test.retry.target", "req-1", "corr-1"),
        store.claim_or_get_execution(tenant_id, key, "test.retry.target", "req-2", "corr-2"),
    )

    claims = [res1[0], res2[0]]
    # Exactly one caller wins the atomic claim: FAILED -> PROCESSING
    assert ClaimResult.CLAIMED in claims
    assert ClaimResult.PROCESSING in claims

    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_database_unique_constraint_enforced_on_tenant_id_and_idempotency_key(tmp_path: Path) -> None:
    """Direct insertion of duplicate (tenant_id, idempotency_key) must raise IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    from kortex.core.idempotency import IdempotencyRecordModel

    db_manager = await _create_test_db(tmp_path, "uq_constraint")
    data_store = RelationalDataStore(db_manager)

    tenant_id = "tenant-uq"
    key = "duplicate-key-constraint"

    async def _insert_first(session: AsyncSession) -> None:
        rec1 = IdempotencyRecordModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            idempotency_key=key,
            capability_name="test.uq",
            request_id="req-1",
            correlation_id="corr-1",
            state=IdempotencyState.PROCESSING.value,
        )
        session.add(rec1)

    await data_store.execute_in_transaction(_insert_first)

    # Second insert with identical (tenant_id, idempotency_key) inside same or separate transaction
    async def _insert_second(session: AsyncSession) -> None:
        rec2 = IdempotencyRecordModel(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            idempotency_key=key,
            capability_name="test.uq",
            request_id="req-2",
            correlation_id="corr-2",
            state=IdempotencyState.PROCESSING.value,
        )
        session.add(rec2)
        await session.flush()

    with pytest.raises(IntegrityError):
        await data_store.execute_in_transaction(_insert_second)

    await db_manager.disconnect()


@pytest.mark.asyncio
async def test_transactional_outbox_tenant_filtered_query(tmp_path: Path) -> None:
    """OutboxStore.get_pending_events strictly partitions pending events by tenant."""
    db_manager = await _create_test_db(tmp_path, "outbox_filter")
    data_store = RelationalDataStore(db_manager)
    outbox = OutboxStore(data_store)

    await outbox.stage_event(tenant_id="tenant-A", topic="event.a1", payload={"n": 1})
    await outbox.stage_event(tenant_id="tenant-A", topic="event.a2", payload={"n": 2})
    await outbox.stage_event(tenant_id="tenant-B", topic="event.b1", payload={"n": 3})

    # Tenant-specific queries
    events_a = await outbox.get_pending_events(tenant_id="tenant-A")
    assert len(events_a) == 2
    assert all(e.tenant_id == "tenant-A" for e in events_a)

    events_b = await outbox.get_pending_events(tenant_id="tenant-B")
    assert len(events_b) == 1
    assert events_b[0].tenant_id == "tenant-B"

    # All tenants query
    all_events = await outbox.get_pending_events(tenant_id=None)
    assert len(all_events) == 3

    await db_manager.disconnect()
