"""Adversarial tests for the Security Engine M4 `AuthorizationEngine` (hybrid RBAC + ABAC).

Named `test_rbac_abac.py` per `security_engine_implementation_spec.md` S3's
own folder-structure naming ("test_rbac_abac.py # Unit tests for RBAC and
ABAC evaluations").

Covers: static role-to-permission matrix evaluation (grant/deny, unions
across multiple roles, unprovisioned-role fail-closed, vacuous allow for an
empty requirement), the S16/S18 `ICacheStore` read-through permission-matrix
cache (hit/miss/populate/failure-fallback/malformed-value/key-namespacing),
fail-closed tenant_id and security_classification ABAC rules (missing
resource_tenant_id denies — a ratified M4 decision, not a frozen-text
quote — malformed input never crashes and never grants a bypass), the
combined `authorize()` orchestration (RBAC-deny short-circuits ABAC,
both-must-allow, deterministic, no exception path produces an allow),
`authorize_strict()`'s raise-on-deny contract, and storage-failure
normalization — proving the ratified M4 architecture decisions, not merely
exercising code paths.

Tenant/role/permission identifiers are derived from `tmp_path.name` (unique
per test) for role-permission grants that must not collide with a prior
test's persisted `RolePermissionRecord` rows, mirroring the exact discipline
`test_authentication_manager.py` established for the same reason (`Kernel()`
defaults to a single shared, non-test-scoped SQLite file).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NoReturn

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.kernel import Kernel
from kortex.engines.security.abac import ABACEvaluator
from kortex.engines.security.authorization import AuthorizationEngine
from kortex.engines.security.exceptions import AuthorizationDeniedError, SecurityEngineError
from kortex.engines.security.models import (
    AccessDecision,
    ClassificationLevel,
    PermissionRequirement,
    RolePermissionRecord,
    SecurityPrincipal,
)
from kortex.engines.security.rbac import RBACEvaluator
from kortex.engines.storage.engine import StorageEngine

_PRINCIPAL_TYPE = "USER"
_CLASSIFICATION_ORDER = [
    ClassificationLevel.PUBLIC,
    ClassificationLevel.INTERNAL,
    ClassificationLevel.CONFIDENTIAL,
    ClassificationLevel.RESTRICTED,
]


def _role(tmp_path: Path, suffix: str) -> str:
    return f"role-{tmp_path.name}-{suffix}-{uuid.uuid4().hex[:8]}"


async def _make_authorization_engine(tmp_path: Path) -> tuple[Kernel, StorageEngine, AuthorizationEngine]:
    kernel = Kernel()
    storage_engine = StorageEngine(base_directory=str(tmp_path / "authorization_engine_test"))
    kernel.register_engine(storage_engine)
    await storage_engine.initialize(kernel)
    await storage_engine.start()
    await kernel.db.connect()
    await kernel.db.create_all_tables()
    engine = AuthorizationEngine(data_store=storage_engine.data)
    return kernel, storage_engine, engine


async def _grant(data_store: Any, role: str, permission: str) -> None:
    """Insert a `RolePermissionRecord` directly via `IDataStore` — M4 has no
    provisioning capability, mirroring `test_authentication_manager.py`'s
    `_seed_principal` pattern exactly."""

    async def _action(session: AsyncSession) -> None:
        session.add(RolePermissionRecord(id=str(uuid.uuid4()), role=role, permission=permission))
        await session.flush()

    await data_store.execute_in_transaction(_action)


def _principal(
    tenant_id: str = "tenant-a", roles: list[str] | None = None, attributes: dict[str, Any] | None = None
) -> SecurityPrincipal:
    return SecurityPrincipal(
        principal_id="principal-1",
        principal_type=_PRINCIPAL_TYPE,
        tenant_id=tenant_id,
        roles=roles or [],
        attributes=attributes or {},
    )


class _FailingDataStore:
    """Fake IDataStore whose transactions always fail — proves storage
    failures normalize to `SecurityEngineError` rather than a silent
    allow/deny."""

    async def get_session(self) -> Any:  # pragma: no cover - not exercised
        raise NotImplementedError

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> NoReturn:
        raise RuntimeError("simulated storage outage")


class _CountingDataStore:
    """Wraps a real `IDataStore`, counting `execute_in_transaction` calls —
    used to prove a cache hit avoids a second authoritative query."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count = 0

    async def get_session(self) -> Any:  # pragma: no cover - not exercised
        return await self._inner.get_session()

    async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        self.call_count += 1
        return await self._inner.execute_in_transaction(action)


class _RecordingCacheStore:
    """Real (dict-backed) `ICacheStore`, recording every `get`/`set` call for assertions."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self.get_calls: list[str] = []
        self.set_calls: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        self.get_calls.append(key)
        return self._data.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        self.set_calls.append((key, value, ttl_seconds))
        self._data[key] = value
        return True

    async def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None

    async def clear(self) -> bool:
        self._data.clear()
        return True


class _RaisingGetCacheStore(_RecordingCacheStore):
    """A cache whose `get()` always raises — proves a read failure falls back to IDataStore."""

    async def get(self, key: str) -> Any:
        self.get_calls.append(key)
        raise RuntimeError("simulated cache read failure")


class _RaisingSetCacheStore(_RecordingCacheStore):
    """A cache whose `set()` always raises — proves a write failure never changes the result."""

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> bool:
        self.set_calls.append((key, value, ttl_seconds))
        raise RuntimeError("simulated cache write failure")


# -- RBAC -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_rbac_allows_when_role_grants_permission(tmp_path: Path) -> None:
    role = _role(tmp_path, "editor")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "document.write")

    requirement = PermissionRequirement(capability_name="document.write", required_permissions=["document.write"])
    decision = await engine.evaluate_rbac(_principal(roles=[role]), requirement)

    assert isinstance(decision, AccessDecision)
    assert decision.is_allowed is True
    assert decision.decision_code == "RBAC_ALLOWED"


@pytest.mark.asyncio
async def test_evaluate_rbac_denies_when_role_lacks_permission(tmp_path: Path) -> None:
    role = _role(tmp_path, "viewer")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "document.read")

    requirement = PermissionRequirement(capability_name="document.write", required_permissions=["document.write"])
    decision = await engine.evaluate_rbac(_principal(roles=[role]), requirement)

    assert decision.is_allowed is False
    assert decision.decision_code == "RBAC_PERMISSION_DENIED"
    assert "document.write" in decision.reason


@pytest.mark.asyncio
async def test_evaluate_rbac_denies_when_principal_has_no_roles(tmp_path: Path) -> None:
    _kernel, _storage, engine = await _make_authorization_engine(tmp_path)

    decision = await engine.evaluate_rbac(
        _principal(roles=[]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )

    assert decision.is_allowed is False
    assert decision.decision_code == "RBAC_NO_ROLES"


@pytest.mark.asyncio
async def test_evaluate_rbac_denies_for_unprovisioned_role(tmp_path: Path) -> None:
    """A role the principal claims to have, but that has zero rows in
    RolePermissionRecord, contributes zero permissions — fail closed, not an
    error."""
    unprovisioned_role = _role(tmp_path, "never-granted-anything")
    _kernel, _storage, engine = await _make_authorization_engine(tmp_path)

    decision = await engine.evaluate_rbac(
        _principal(roles=[unprovisioned_role]),
        PermissionRequirement(capability_name="x", required_permissions=["x.read"]),
    )

    assert decision.is_allowed is False
    assert decision.decision_code == "RBAC_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_evaluate_rbac_allows_vacuously_when_no_permissions_required(tmp_path: Path) -> None:
    _kernel, _storage, engine = await _make_authorization_engine(tmp_path)

    decision = await engine.evaluate_rbac(
        _principal(roles=[]), PermissionRequirement(capability_name="x", required_permissions=[])
    )

    assert decision.is_allowed is True
    assert decision.decision_code == "RBAC_NO_PERMISSIONS_REQUIRED"


@pytest.mark.asyncio
async def test_evaluate_rbac_unions_permissions_across_multiple_roles(tmp_path: Path) -> None:
    role_a, role_b = _role(tmp_path, "a"), _role(tmp_path, "b")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role_a, "perm.one")
    await _grant(storage.data, role_b, "perm.two")

    decision = await engine.evaluate_rbac(
        _principal(roles=[role_a, role_b]),
        PermissionRequirement(capability_name="x", required_permissions=["perm.one", "perm.two"]),
    )

    assert decision.is_allowed is True


@pytest.mark.asyncio
async def test_evaluate_rbac_partial_union_still_denies_missing_permission(tmp_path: Path) -> None:
    role_a, role_b = _role(tmp_path, "c"), _role(tmp_path, "d")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role_a, "perm.one")
    # role_b intentionally never granted "perm.two"

    decision = await engine.evaluate_rbac(
        _principal(roles=[role_a, role_b]),
        PermissionRequirement(capability_name="x", required_permissions=["perm.one", "perm.two"]),
    )

    assert decision.is_allowed is False
    assert "perm.two" in decision.reason
    assert "perm.one" not in decision.reason.split("permission(s):")[1]  # only the missing one is named


@pytest.mark.asyncio
async def test_rbac_evaluator_storage_failure_raises_security_engine_error() -> None:
    evaluator = RBACEvaluator(data_store=_FailingDataStore())

    with pytest.raises(SecurityEngineError):
        await evaluator.evaluate(
            _principal(roles=["some-role"]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
        )


@pytest.mark.asyncio
async def test_rbac_evaluator_propagates_security_engine_error_raised_directly_by_storage_action() -> None:
    """`_run_in_transaction`'s `except SecurityEngineError: raise` passthrough
    — a `SecurityEngineError` raised by the transaction body itself must
    propagate unchanged, never re-wrapped. Mirrors the identical test for
    `AuthenticationManager` in M3."""

    class _RaisingSecurityErrorDataStore:
        async def get_session(self) -> Any:  # pragma: no cover - not exercised
            raise NotImplementedError

        async def execute_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> NoReturn:
            raise SecurityEngineError("marker-error-from-transaction-body")

    evaluator = RBACEvaluator(data_store=_RaisingSecurityErrorDataStore())

    with pytest.raises(SecurityEngineError, match="marker-error-from-transaction-body"):
        await evaluator.evaluate(
            _principal(roles=["some-role"]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
        )


# -- RBAC permission-matrix cache (S16/S18) ----------------------------------------


@pytest.mark.asyncio
async def test_rbac_cache_miss_queries_data_store_and_populates_cache(tmp_path: Path) -> None:
    role = _role(tmp_path, "cache-miss")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    cache = _RecordingCacheStore()
    evaluator = RBACEvaluator(data_store=storage.data, cache_store=cache)

    decision = await evaluator.evaluate(
        _principal(roles=[role]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )

    assert decision.is_allowed is True
    assert len(cache.set_calls) == 1
    key, value, ttl = cache.set_calls[0]
    assert key == f"security:rbac:role:{role}"
    assert value == ["x.read"]
    assert ttl == 300


@pytest.mark.asyncio
async def test_rbac_cache_hit_avoids_second_data_store_query(tmp_path: Path) -> None:
    role = _role(tmp_path, "cache-hit")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    counting_store = _CountingDataStore(storage.data)
    cache = _RecordingCacheStore()
    evaluator = RBACEvaluator(data_store=counting_store, cache_store=cache)
    principal = _principal(roles=[role])
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    first = await evaluator.evaluate(principal, requirement)
    assert first.is_allowed is True
    assert counting_store.call_count == 1

    second = await evaluator.evaluate(principal, requirement)
    assert second.is_allowed is True
    assert counting_store.call_count == 1  # no new authoritative query on the cache-hit path


@pytest.mark.asyncio
async def test_rbac_cache_store_none_preserves_authoritative_behavior(tmp_path: Path) -> None:
    """Regression guard: omitting `cache_store` entirely must behave exactly
    as it did before caching existed."""
    role = _role(tmp_path, "no-cache")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    evaluator = RBACEvaluator(data_store=storage.data, cache_store=None)

    decision = await evaluator.evaluate(
        _principal(roles=[role]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )
    assert decision.is_allowed is True


@pytest.mark.asyncio
async def test_rbac_cache_read_exception_falls_back_to_data_store(tmp_path: Path) -> None:
    role = _role(tmp_path, "cache-read-fail")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    evaluator = RBACEvaluator(data_store=storage.data, cache_store=_RaisingGetCacheStore())

    decision = await evaluator.evaluate(
        _principal(roles=[role]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )
    assert decision.is_allowed is True  # authoritative result is unaffected by the cache read failure


@pytest.mark.asyncio
async def test_rbac_cache_write_exception_does_not_change_result(tmp_path: Path) -> None:
    role = _role(tmp_path, "cache-write-fail")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    evaluator = RBACEvaluator(data_store=storage.data, cache_store=_RaisingSetCacheStore())

    decision = await evaluator.evaluate(
        _principal(roles=[role]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )
    assert decision.is_allowed is True  # a cache write failure never changes the authoritative result


@pytest.mark.asyncio
async def test_rbac_cache_malformed_value_is_never_trusted_and_data_store_is_reconsulted(tmp_path: Path) -> None:
    role = _role(tmp_path, "cache-malformed")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    cache = _RecordingCacheStore()
    await cache.set(f"security:rbac:role:{role}", "not-a-list", ttl_seconds=300)  # corrupted/malformed cache entry
    evaluator = RBACEvaluator(data_store=storage.data, cache_store=cache)

    decision = await evaluator.evaluate(
        _principal(roles=[role]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )
    assert decision.is_allowed is True  # never trusted the malformed cached value; re-fetched authoritatively


@pytest.mark.asyncio
async def test_rbac_cache_key_is_namespaced_per_role(tmp_path: Path) -> None:
    role = _role(tmp_path, "namespaced")
    _kernel, storage, _engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    cache = _RecordingCacheStore()
    evaluator = RBACEvaluator(data_store=storage.data, cache_store=cache)

    await evaluator.evaluate(
        _principal(roles=[role]), PermissionRequirement(capability_name="x", required_permissions=["x.read"])
    )

    assert f"security:rbac:role:{role}" in cache.get_calls
    assert cache.set_calls[0][0] == f"security:rbac:role:{role}"


# -- ABAC -------------------------------------------------------------------------


def test_evaluate_abac_allows_when_tenant_matches_and_clearance_sufficient() -> None:
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "CONFIDENTIAL"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.CONFIDENTIAL
    )

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is True
    assert decision.decision_code == "ABAC_ALLOWED"


def test_evaluate_abac_denies_on_tenant_mismatch() -> None:
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a")
    requirement = PermissionRequirement(capability_name="x", required_permissions=[])

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-b"})

    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_TENANT_MISMATCH"


def test_evaluate_abac_denies_when_resource_tenant_id_missing() -> None:
    """Locked M4 decision: missing `resource_tenant_id` denies, exactly like
    a mismatch — no silent skip, no SYSTEM/global exception."""
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.PUBLIC
    )

    decision = evaluator.evaluate(principal, requirement, {})

    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_TENANT_MISSING"


@pytest.mark.parametrize("bogus_resource_tenant_id", [123, 0, False, [], {}, ["tenant-a"]])
def test_evaluate_abac_non_string_resource_tenant_id_never_bypasses_tenant_check(bogus_resource_tenant_id: Any) -> None:
    """Tenant identity cannot be bypassed through malformed
    `resource_tenant_id` values — a non-string value is never treated as an
    accidental match, and never coerces into a false ALLOW."""
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.PUBLIC
    )

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": bogus_resource_tenant_id})

    assert decision.is_allowed is False
    assert decision.decision_code in ("ABAC_TENANT_MISMATCH", "ABAC_TENANT_MISSING")


def test_evaluate_abac_matching_tenant_allows_subject_to_remaining_policy() -> None:
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.PUBLIC
    )

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is True


def test_evaluate_abac_denies_on_insufficient_clearance() -> None:
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.RESTRICTED
    )

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_INSUFFICIENT_CLEARANCE"


def test_evaluate_abac_allows_with_sufficient_clearance() -> None:
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "RESTRICTED"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.RESTRICTED
    )

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is True


def test_evaluate_abac_defaults_missing_clearance_to_public() -> None:
    """No `clearance_level` attribute at all -> treated as PUBLIC -> denied
    for anything above PUBLIC (the default requirement classification is
    INTERNAL, per PermissionRequirement's own M1 default)."""
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={})
    requirement = PermissionRequirement(capability_name="x", required_permissions=[])  # defaults to INTERNAL

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_INSUFFICIENT_CLEARANCE"


def test_evaluate_abac_malformed_clearance_value_fails_closed_to_public() -> None:
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "NOT_A_REAL_LEVEL"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.PUBLIC
    )

    # Malformed value is treated as PUBLIC (lowest), which still satisfies a PUBLIC requirement.
    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})
    assert decision.is_allowed is True

    # But it never grants anything ABOVE PUBLIC.
    higher_requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.INTERNAL
    )
    denied = evaluator.evaluate(principal, higher_requirement, {"resource_tenant_id": "tenant-a"})
    assert denied.is_allowed is False
    assert denied.decision_code == "ABAC_INSUFFICIENT_CLEARANCE"


@pytest.mark.parametrize("malformed_context", ["not-a-dict", None, 12345, ["a", "list"]])
def test_evaluate_abac_malformed_context_fails_closed(malformed_context: Any) -> None:
    """A non-dict `context` normalizes to `{}`, which is missing
    `resource_tenant_id` — it must deny, never silently skip the check."""
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=ClassificationLevel.PUBLIC
    )

    decision = evaluator.evaluate(principal, requirement, malformed_context)  # type: ignore[arg-type]

    assert isinstance(decision, AccessDecision)
    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_TENANT_MISSING"


@pytest.mark.parametrize(
    ("principal_clearance", "required_classification", "expected_allowed"),
    [
        ("PUBLIC", ClassificationLevel.PUBLIC, True),
        ("PUBLIC", ClassificationLevel.INTERNAL, False),
        ("INTERNAL", ClassificationLevel.PUBLIC, True),
        ("INTERNAL", ClassificationLevel.INTERNAL, True),
        ("INTERNAL", ClassificationLevel.CONFIDENTIAL, False),
        ("CONFIDENTIAL", ClassificationLevel.INTERNAL, True),
        ("CONFIDENTIAL", ClassificationLevel.RESTRICTED, False),
        ("RESTRICTED", ClassificationLevel.RESTRICTED, True),
        ("RESTRICTED", ClassificationLevel.CONFIDENTIAL, True),
    ],
)
def test_classification_ordering_is_strictly_ascending(
    principal_clearance: str, required_classification: ClassificationLevel, expected_allowed: bool
) -> None:
    """Proves the PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED ordering
    (an M4 implementation decision, documented as such in abac.py — not a
    verbatim frozen rule) is applied consistently across every adjacent and
    non-adjacent pair."""
    evaluator = ABACEvaluator()
    principal = _principal(tenant_id="tenant-a", attributes={"clearance_level": principal_clearance})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=[], security_classification=required_classification
    )

    decision = evaluator.evaluate(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is expected_allowed


# -- Combined authorize() orchestration ------------------------------------------


@pytest.mark.asyncio
async def test_authorize_allows_when_both_rbac_and_abac_allow(tmp_path: Path) -> None:
    role = _role(tmp_path, "both-allow")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    principal = _principal(tenant_id="tenant-a", roles=[role], attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=["x.read"], security_classification=ClassificationLevel.PUBLIC
    )

    decision = await engine.authorize(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is True
    assert decision.decision_code == "AUTHORIZED"


@pytest.mark.asyncio
async def test_authorize_denies_when_rbac_denies_and_never_evaluates_abac(tmp_path: Path) -> None:
    """Proves the short-circuit: ABAC must never be reached once RBAC denies —
    RBAC deny cannot become an ALLOW through ABAC, because ABAC never runs."""
    _kernel, _storage, engine = await _make_authorization_engine(tmp_path)

    class _SpyABACEvaluator(ABACEvaluator):
        def __init__(self) -> None:
            self.called = False

        def evaluate(self, principal: Any, requirement: Any, context: Any) -> AccessDecision:  # type: ignore[override]
            self.called = True
            return AccessDecision(is_allowed=True, decision_code="SPY_SHOULD_NOT_BE_REACHED", reason="spy")

    spy = _SpyABACEvaluator()
    engine._abac = spy  # reach into internals, mirroring test_secret_store.py's own precedent

    principal = _principal(roles=[])  # guaranteed RBAC deny: no roles
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    # Context is deliberately tenant-matching and fully permissive — proving
    # that even when ABAC *would* allow, RBAC's deny is what's returned.
    decision = await engine.authorize(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert decision.is_allowed is False
    assert decision.decision_code == "RBAC_NO_ROLES"
    assert spy.called is False


@pytest.mark.asyncio
async def test_authorize_denies_when_rbac_allows_but_abac_denies(tmp_path: Path) -> None:
    """ABAC deny cannot become an ALLOW through RBAC — RBAC allowing is not sufficient."""
    role = _role(tmp_path, "rbac-allow-abac-deny")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    principal = _principal(tenant_id="tenant-a", roles=[role])
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    decision = await engine.authorize(principal, requirement, {"resource_tenant_id": "tenant-b"})

    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_TENANT_MISMATCH"


@pytest.mark.asyncio
async def test_authorize_missing_resource_tenant_denies_even_when_rbac_allows(tmp_path: Path) -> None:
    role = _role(tmp_path, "rbac-allow-missing-tenant")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    principal = _principal(tenant_id="tenant-a", roles=[role])
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    decision = await engine.authorize(principal, requirement, {})

    assert decision.is_allowed is False
    assert decision.decision_code == "ABAC_TENANT_MISSING"


@pytest.mark.asyncio
async def test_authorize_is_deterministic_across_repeated_identical_calls(tmp_path: Path) -> None:
    role = _role(tmp_path, "deterministic")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    principal = _principal(tenant_id="tenant-a", roles=[role], attributes={"clearance_level": "PUBLIC"})
    requirement = PermissionRequirement(
        capability_name="x", required_permissions=["x.read"], security_classification=ClassificationLevel.PUBLIC
    )

    first = await engine.authorize(principal, requirement, {"resource_tenant_id": "tenant-a"})
    second = await engine.authorize(principal, requirement, {"resource_tenant_id": "tenant-a"})

    assert first.is_allowed == second.is_allowed is True
    assert first.decision_code == second.decision_code


@pytest.mark.asyncio
async def test_authorize_storage_failure_propagates_as_security_engine_error_never_an_allow() -> None:
    """No exception path produces an allow decision — a storage failure
    raises, it never resolves to a truthy `AccessDecision`."""
    engine = AuthorizationEngine(data_store=_FailingDataStore())
    principal = _principal(roles=["some-role"])
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    with pytest.raises(SecurityEngineError):
        await engine.authorize(principal, requirement, {"resource_tenant_id": "tenant-a"})


# -- authorize_strict() -----------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_strict_raises_on_deny(tmp_path: Path) -> None:
    _kernel, _storage, engine = await _make_authorization_engine(tmp_path)
    principal = _principal(roles=[])
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    with pytest.raises(AuthorizationDeniedError):
        await engine.authorize_strict(principal, requirement)


@pytest.mark.asyncio
async def test_authorize_strict_succeeds_silently_on_allow(tmp_path: Path) -> None:
    role = _role(tmp_path, "strict-allow")
    _kernel, storage, engine = await _make_authorization_engine(tmp_path)
    await _grant(storage.data, role, "x.read")
    principal = _principal(tenant_id="tenant-a", roles=[role], attributes={"clearance_level": "INTERNAL"})
    requirement = PermissionRequirement(capability_name="x", required_permissions=["x.read"])

    result = await engine.authorize_strict(principal, requirement, {"resource_tenant_id": "tenant-a"})
    assert result is None
