"""
KORTEX Security Engine — RBAC Evaluator (Milestone M4).

Implements the RBAC half of `IAuthorizationEngine.evaluate_rbac` over
`IDataStore` exclusively — Security Engine never opens a database connection
or executes raw SQL directly, following the same boundary `SecretStore` (M2)
and `AuthenticationManager` (M3) already establish.

    RBACEvaluator
        |-- cache read-through --> ICacheStore  (optional, additive)
        |-- authoritative source --> IDataStore  (Storage Engine)

Evaluates static role-to-permission matrices (per
`docs/architecture/security_engine_implementation_spec.md` S8): a
principal's granted permissions are the union of every `RolePermissionRecord`
row matching any of the principal's `roles`. There is no provisioning
capability in M4 — a role with zero rows contributes zero permissions, so
RBAC fails closed (denies) for any unprovisioned role, exactly mirroring how
M3's `PrincipalRecord` has no provisioning capability either.

A `PermissionRequirement` with an empty `required_permissions` list is
treated as vacuously satisfied (no permission check applies) — this is a
deliberate design choice, not an oversight: what a capability requires is
that capability's own declaration, not something RBAC second-guesses.

Permission-matrix caching (S16/S18 compliance):
`security_engine_implementation_spec.md` S18 ties its "Authorization
decision evaluation ≤5ms" figure explicitly to "cached permission matrices
in `ICacheStore`" — this is implemented here as a per-role read-through
cache, following the exact, unanimous convention every other `ICacheStore`
consumer in this repository already uses (`document/lifecycle.py`,
`document/template_library.py`, `document/adapter_registry.py`,
`connector/rate_limiter.py`): `cache_store` is always `Optional`, always
defaults to `None`, and its absence never changes correctness — only
whether `IDataStore` is consulted on every call or only on a cache miss.

Cache key: `security:rbac:role:{role}` — namespaced by engine (`security:`)
because `MemoryCacheStore` is a single instance shared across every engine
in the process (confirmed via `StorageEngine.cache`); no tenant segment,
because RBAC role-to-permission grants are global, not tenant-scoped
(mirroring `document/adapter_registry.py`'s identical "process-global, not
tenant-scoped" cache-key precedent for its own discovery cache). Cache
value: `list[str]` of granted permission strings for that one role — never
a `set` (not JSON/pickle-portable by this repository's own convention; no
existing `ICacheStore` consumer caches a `set`). TTL: 300 seconds, matching
`document/security.py`'s generic multi-level-cache default — there is no
established "authorization-specific" TTL precedent to follow instead, and
M4 exposes no role-provisioning capability to hook explicit invalidation
into, so TTL is the sole staleness bound (mirroring
`document/adapter_registry.py`'s discovery cache, which has the same
no-write-path characteristic).

Fail-closed cache-failure discipline (mirrors `connector/rate_limiter.py`'s
identical `try/except: pass` pattern exactly): a cache **read** exception is
treated as a cache miss (falls through to `IDataStore`); a cache **write**
exception is silently ignored (the already-computed, authoritative result
is unaffected). Neither path can ever cause a permission grant that
`IDataStore` itself did not authorize, and neither can ever cause RBAC to
fail an evaluation it would otherwise have completed successfully — the
cache is purely an optional performance layer, never a source of truth.

Boot-time loading: `kernel_boot_sequence.md` S6's "Loads ... RBAC permission
matrices" is satisfied by lazy read-through population on first access, not
by an eager cache-warming step during `initialize()`. This matches the
unanimous, repository-wide convention — no existing `ICacheStore` consumer
anywhere in this codebase performs eager boot-time population, and
`MemoryCacheStore` itself starts empty every boot regardless (it has no
persistence), so an eager warm-up would be the only such example in the
entire codebase were it added here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.security.exceptions import SecurityEngineError
from kortex.engines.security.models import (
    AccessDecision,
    PermissionRequirement,
    RolePermissionRecord,
    SecurityPrincipal,
)
from kortex.engines.storage.interfaces import ICacheStore, IDataStore

_CACHE_KEY_PREFIX = "security:rbac:role:"
_CACHE_TTL_SECONDS = 300


class RBACEvaluator:
    """M4 RBAC evaluator. Backs `AuthorizationEngine.evaluate_rbac` over `IDataStore`,
    with an optional `ICacheStore` read-through cache for per-role permission grants."""

    def __init__(self, data_store: IDataStore, cache_store: ICacheStore | None = None) -> None:
        self._data_store = data_store
        self._cache_store = cache_store

    @staticmethod
    def _cache_key(role: str) -> str:
        return f"{_CACHE_KEY_PREFIX}{role}"

    async def _run_in_transaction(self, action: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        """Run `action` via `IDataStore.execute_in_transaction`, normalizing any
        failure that is not already a `SecurityEngineError` — an underlying
        storage failure must never be silently converted into an ALLOW or a
        misleading "no permissions" DENY. Mirrors `SecretStore`/
        `AuthenticationManager`'s identical pattern.
        """
        try:
            return await self._data_store.execute_in_transaction(action)
        except SecurityEngineError:
            raise
        except Exception as exc:
            raise SecurityEngineError("RBAC storage operation failed.") from exc

    async def _fetch_permissions_by_role(self, roles: list[str]) -> dict[str, list[str]]:
        """Authoritative `IDataStore` lookup: one permission list per role,
        including an empty list for any role with zero matching rows
        (an unprovisioned role — not an error)."""

        async def _action(session: AsyncSession) -> dict[str, list[str]]:
            stmt = select(RolePermissionRecord.role, RolePermissionRecord.permission).where(
                RolePermissionRecord.role.in_(roles)
            )
            res = await session.execute(stmt)
            result: dict[str, list[str]] = {role: [] for role in roles}
            for role, permission in res.all():
                result[role].append(permission)
            return result

        return cast(dict[str, list[str]], await self._run_in_transaction(_action))

    async def _load_granted_permissions(self, roles: list[str]) -> set[str]:
        """Union of every permission granted to any of `roles`, consulting the
        optional per-role `ICacheStore` read-through cache first.

        `IDataStore` remains the sole authoritative source: a cache hit only
        ever short-circuits a query for a role whose permissions are already
        known-good from a prior authoritative fetch; a cache miss, a cache
        read exception, or a malformed cached value always falls through to
        `IDataStore` for that role.
        """
        granted: set[str] = set()
        roles_to_fetch: list[str] = []

        for role in roles:
            cached = await self._get_cached_permissions(role)
            if cached is not None:
                granted.update(cached)
            else:
                roles_to_fetch.append(role)

        if roles_to_fetch:
            fetched = await self._fetch_permissions_by_role(roles_to_fetch)
            for role in roles_to_fetch:
                role_permissions = fetched.get(role, [])
                granted.update(role_permissions)
                await self._set_cached_permissions(role, role_permissions)

        return granted

    async def _get_cached_permissions(self, role: str) -> list[str] | None:
        """Read a role's cached permission list. Returns `None` (a cache miss)
        for a missing key, a malformed (non-`list`) cached value, or any
        cache-layer exception — never raises, never trusts unvalidated
        cache content."""
        if self._cache_store is None:
            return None
        try:
            cached = await self._cache_store.get(self._cache_key(role))
        except Exception:
            return None
        if isinstance(cached, list):
            return cached
        return None

    async def _set_cached_permissions(self, role: str, permissions: list[str]) -> None:
        """Populate a role's cache entry. Any cache-layer exception is
        silently ignored — a cache write failure must never affect the
        already-computed, authoritative result. Uses `contextlib.suppress`
        rather than `connector/rate_limiter.py`'s bare `try/except: pass`
        (same fail-safe behavior, lint-clean form)."""
        if self._cache_store is None:
            return
        with contextlib.suppress(Exception):
            await self._cache_store.set(self._cache_key(role), permissions, ttl_seconds=_CACHE_TTL_SECONDS)

    async def evaluate(self, principal: SecurityPrincipal, requirement: PermissionRequirement) -> AccessDecision:
        """Evaluate `requirement.required_permissions` against `principal.roles`'
        granted permissions.

        Returns a denying `AccessDecision` — never raises — for every normal
        policy outcome (no roles, unprovisioned role, missing permission).
        Raises `SecurityEngineError` only for a genuine storage failure,
        keeping "operational error" and "policy denial" clearly distinct.
        """
        if not requirement.required_permissions:
            return AccessDecision(
                is_allowed=True,
                decision_code="RBAC_NO_PERMISSIONS_REQUIRED",
                reason="Requirement declares no required permissions; RBAC check is vacuously satisfied.",
            )

        if not principal.roles:
            return AccessDecision(
                is_allowed=False,
                decision_code="RBAC_NO_ROLES",
                reason="Principal has no assigned roles.",
            )

        granted = await self._load_granted_permissions(principal.roles)
        missing = [permission for permission in requirement.required_permissions if permission not in granted]
        if missing:
            return AccessDecision(
                is_allowed=False,
                decision_code="RBAC_PERMISSION_DENIED",
                reason=f"Principal's roles do not grant required permission(s): {', '.join(sorted(missing))}.",
            )

        return AccessDecision(
            is_allowed=True,
            decision_code="RBAC_ALLOWED",
            reason="Principal's roles grant all required permissions.",
        )
