"""
KORTEX Security Engine — Authorization Engine (Milestone M4).

Implements `IAuthorizationEngine` by delegating to two internal evaluators,
mirroring `VerificationService`'s delegation to `ICryptoProvider` (M1):

    AuthorizationEngine
        |-- evaluate_rbac --> RBACEvaluator (rbac.py)   --> ICacheStore (optional) --> IDataStore
        |-- evaluate_abac --> ABACEvaluator (abac.py)    (stateless)

`authorize()` is an additive orchestration method beyond the bare
`IAuthorizationEngine` Protocol, needed because the frozen spec describes
Authorization Engine as a single "Hybrid RBAC and ABAC evaluation engine"
(S1) and the `kortex.security.access.authorize` capability needs one entry
point. Precedence (deliberately conservative, least-privilege): RBAC is
evaluated first; a deny there short-circuits and ABAC is never reached.
Only if RBAC allows is ABAC evaluated. Both must independently allow for
the combined result to allow — logical AND, never OR.

Per S7's literal wording ("returning deterministic AccessDecision results"),
`authorize()` returns an `AccessDecision` — allow or deny — rather than
raising on a deny. A policy "no" is a normal, expected outcome a caller
inspects and acts on, not an exceptional circumstance (unlike
`SecretStore.get_secret`/`AuthenticationManager.authenticate`, which raise
because their failure means the caller literally cannot proceed at all).
`authorize_strict()` is the raise-on-deny variant for callers that want hard
failure semantics, mirroring `VerificationService.verify_signature`/
`verify_signature_strict`'s exact precedent — this is what finally gives the
M1-declared, previously-never-raised `AuthorizationDeniedError` a real use.

Only a genuine storage failure (from the RBAC evaluator) raises
`SecurityEngineError` — normal policy outcomes (allow or deny) are always
data, never exceptions, keeping "operational error" and "policy denial"
clearly distinct (mirroring `SecretStore`/`AuthenticationManager`'s
identical storage-failure-normalization discipline).

S16/S18 "authorization decision caches" reconciliation (read-only audit
finding, documented here rather than left implicit): S18 ties its "≤5ms"
performance figure specifically to "cached permission matrices in
`ICacheStore`" — a different, more specific phrase than S16's "authorization
decision caches." No document anywhere requires caching complete
`AccessDecision` results, and no existing `ICacheStore` consumer in this
repository caches a "whole decision" shape at any granularity coarser than
a single resolved value per identifier (per-`template_id`, per-adapter-list,
per-rate-limit-key) — the established convention caches *inputs* to a
computation, not the computation's own final output. S16's "session token
caches" half of the same sentence was also never implemented (M3's own
ratified, written decision), without that being treated as requiring a
further architectural decision. Reading these together, "authorization
decision caches" is interpreted as descriptive terminology for the cache
used *by* the authorization decision-making process (i.e. the same
permission-matrix cache `rbac.py` implements) — **interpretation A**, not a
separate mandate to cache `AccessDecision` objects (**interpretation B**).
Full-decision caching remains a documented, deferred non-goal, not
something silently assumed unnecessary.

Explicit M4 non-goals: no Kernel capability dispatcher, no platform-wide
enforcement of this decision (nothing routes capability invocation through
`authorize()` automatically — that remains a later, platform-wide gap
outside this milestone, exactly as M2/M3 left `secret.get`/`auth.authenticate`
real-but-not-platform-enforced). Authentication does not imply authorization,
and this milestone does not imply the reverse either — this evaluates
policy against a caller-supplied `SecurityPrincipal`; it does not itself
verify that principal was ever genuinely authenticated.
"""

from __future__ import annotations

from typing import Any

from kortex.engines.security.abac import ABACEvaluator
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.security.interfaces import IAuthorizationEngine
from kortex.engines.security.models import AccessDecision, PermissionRequirement, SecurityPrincipal
from kortex.engines.security.rbac import RBACEvaluator
from kortex.engines.storage.interfaces import ICacheStore, IDataStore


class AuthorizationEngine(IAuthorizationEngine):
    """M4 hybrid RBAC + ABAC authorization engine. Implements `IAuthorizationEngine`."""

    def __init__(self, data_store: IDataStore, cache_store: ICacheStore | None = None) -> None:
        """Args:
        data_store: Storage Engine's `IDataStore` — the exclusive authoritative
            RBAC permission-matrix source.
        cache_store: Optional `ICacheStore` (normally `storage_engine.cache`)
            backing a read-through permission-matrix cache (S16/S18). Absence
            never changes correctness — only whether `IDataStore` is
            consulted on every RBAC evaluation or only on a cache miss.
        """
        self._rbac = RBACEvaluator(data_store, cache_store=cache_store)
        self._abac = ABACEvaluator()

    async def evaluate_rbac(self, principal: SecurityPrincipal, requirement: PermissionRequirement) -> AccessDecision:
        """Evaluate a static role-to-permission matrix against `requirement`."""
        return await self._rbac.evaluate(principal, requirement)

    async def evaluate_abac(
        self, principal: SecurityPrincipal, requirement: PermissionRequirement, context: dict[str, Any]
    ) -> AccessDecision:
        """Evaluate dynamic attribute-based rules against `requirement` and `context`."""
        return self._abac.evaluate(principal, requirement, context)

    async def authorize(
        self,
        principal: SecurityPrincipal,
        requirement: PermissionRequirement,
        context: dict[str, Any] | None = None,
    ) -> AccessDecision:
        """Evaluate RBAC then ABAC; RBAC deny short-circuits. Both must allow."""
        rbac_decision = await self.evaluate_rbac(principal, requirement)
        if not rbac_decision.is_allowed:
            return rbac_decision

        abac_decision = await self.evaluate_abac(principal, requirement, context or {})
        if not abac_decision.is_allowed:
            return abac_decision

        return AccessDecision(
            is_allowed=True,
            decision_code="AUTHORIZED",
            reason="RBAC and ABAC checks both passed.",
        )

    async def authorize_strict(
        self,
        principal: SecurityPrincipal,
        requirement: PermissionRequirement,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Same as `authorize`, raising `AuthorizationDeniedError` on any deny."""
        decision = await self.authorize(principal, requirement, context)
        if not decision.is_allowed:
            raise AuthorizationDeniedError(decision.reason)
