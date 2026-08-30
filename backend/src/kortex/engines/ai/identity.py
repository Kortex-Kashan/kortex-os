"""AI System Identity — session-token holder for the platform's own AI actor (M6.2-1).

Closes the identity gap M6.1 left open: `generate_response` became
tenant-safe when a dispatcher-injected `principal` is present (M6.1-1), but
nothing gave the AI a principal of its own to authenticate tool invocations
with — every AI tool call against an authenticated capability failed
(`AuthenticationError`, misclassified downstream as a generic execution
error). This module holds and refreshes the resulting session token; it
does not decide identity policy or perform any authentication itself.

AST-quarantine compliance: this module never imports `kortex.engines.security`
(a hard, test-enforced forbidden import for the AI package). `provisioner`
and `authenticator` are plain async callables constructed and owned by
`kortex.api.kernel_bootstrap`, which already imports `SecurityEngine`
directly for other bootstrap-time concerns (master/signing key
resolution) and is not subject to this package's quarantine. `AISystemIdentity`
only ever holds and returns the opaque token objects those callables
produce — it never sees a `SecurityEngine` reference or type.

Provisioning is lazy and per-tenant: no tenant's `PrincipalRecord` is
created until the AI is first asked to act within it, matching the
platform's existing "no eager multi-tenant onboarding" precedent for every
other principal type (there is no principal-provisioning capability
anywhere in KORTEX; rows are otherwise assumed to already exist).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

AI_SYSTEM_PRINCIPAL_ID = "kortex-ai-system"
"""Well-known principal_id for the platform's own AI system principal."""

AI_SYSTEM_ROLE = "AI_SYSTEM_ACTOR"
"""RBAC role granted to the AI system principal. Operators grant this role
exactly the permissions the AI's intended tool set requires — never a
broad or administrative permission set. This role must never also be a
ticket's `required_role` for any approval the AI itself creates, or the AI
could decide its own proposals (see `governance.py`'s
`KernelDurableApprovalBridge`)."""

_TOKEN_ASSUMED_TTL_SECONDS = 15 * 60.0
"""Mirrors `AuthenticationManager._TOKEN_TTL` (15 minutes) -- duplicated
here as a plain float rather than imported, since importing anything from
`kortex.engines.security` is exactly what this module must never do."""

_TOKEN_REFRESH_MARGIN_SECONDS = 120.0
"""Refresh this far ahead of the assumed expiry so an in-flight AI action
never fails mid-execution purely due to token expiry."""

TenantProvisioner = Callable[[str], Awaitable[None]]
"""Ensures the AI system principal exists for one tenant. Idempotent —
safe to call on every resolution, not just the first."""

TenantAuthenticator = Callable[[str], Awaitable[Any]]
"""Authenticates the AI system principal for one tenant and returns a
freshly issued session token object, opaque to this module."""


class AISystemIdentity:
    """Lazily provisions and authenticates the AI system principal, per tenant.

    One credential and one `PrincipalRecord` per tenant — compromise of one
    tenant's credential grants no access to another tenant's capabilities,
    unlike a single global principal shared across all tenants.
    """

    def __init__(
        self,
        provisioner: TenantProvisioner,
        authenticator: TenantAuthenticator,
    ) -> None:
        self._provisioner = provisioner
        self._authenticator = authenticator
        self._provisioned_tenants: set[str] = set()
        self._tokens: dict[str, tuple[Any, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, tenant_id: str) -> asyncio.Lock:
        lock = self._locks.get(tenant_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[tenant_id] = lock
        return lock

    async def get_session_token(self, tenant_id: str) -> Any:
        """Return a valid session token for the AI system principal within `tenant_id`.

        Refreshes proactively (a margin before the token's assumed 15-minute
        TTL) rather than reactively on a failed call. Provisioning happens
        at most once per tenant per process lifetime; authentication (and
        therefore token issuance) happens again whenever the cached token
        is within the refresh margin of its assumed expiry.
        """
        async with self._lock_for(tenant_id):
            now = time.monotonic()
            cached = self._tokens.get(tenant_id)
            if cached is not None and now < cached[1]:
                return cached[0]

            if tenant_id not in self._provisioned_tenants:
                await self._provisioner(tenant_id)
                self._provisioned_tenants.add(tenant_id)

            token = await self._authenticator(tenant_id)
            expires_at = now + _TOKEN_ASSUMED_TTL_SECONDS - _TOKEN_REFRESH_MARGIN_SECONDS
            self._tokens[tenant_id] = (token, expires_at)
            return token


__all__ = ["AI_SYSTEM_PRINCIPAL_ID", "AI_SYSTEM_ROLE", "AISystemIdentity"]
