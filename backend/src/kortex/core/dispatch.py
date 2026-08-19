"""
KORTEX Kernel Capability Enforcement Boundary.

Implements the sanctioned execution path for capability invocation:
`Kernel.invoke_capability()` resolves the requested `CapabilityDescriptor`,
authenticates the caller via the unmodified Milestone-3 `AuthenticationManager`,
constructs a `PermissionRequirement` exclusively from the resolved
descriptor's own security metadata (never from caller-supplied request
data), and authorizes via Milestone-4/M6 `SecurityEngine.authorize()`
before invoking the handler. All RBAC/ABAC/authentication decision logic
remains entirely inside Security Engine — this module only coordinates
calling it in the correct order, per `engineering_constitution.md` Art. 7
("The Kernel owns... execution coordination... The Kernel owns no business
logic").

Security Engine's own capability, `kortex.security.access.authorize`,
remains registered and independently callable exactly as Milestone 4 left
it. This dispatcher never calls it as a *capability* — it calls
`SecurityEngine.authorize()` directly as a method on the Security Engine
instance, resolved via `kernel.get_engine("security")`. Calling
`access.authorize` yourself is therefore never mistaken for having
authorized execution through this boundary.

Audit hook (Milestone M5 completion): every dispatched invocation that
reaches the authentication/authorization checkpoint produces an audit
trail through Security Engine's existing, already-established Milestone M6
`AuditManager` — the same mechanism `SecurityEngine.authenticate()`/
`authorize()` already use, never a new audit system. Authorization is
routed through `SecurityEngine.authorize()` (not the lower
`AuthorizationEngine.authorize_strict()`) specifically so that every
capability grant/deny decision is recorded via that method's own built-in
`_record_security_audit()` + `SecurityAccessGrantedEvent`/
`SecurityAccessDeniedEvent` publication — this dispatcher then raises
`AuthorizationDeniedError` itself on a deny decision, reproducing
`authorize_strict()`'s exact raise semantics on top of the audited path.
Token verification has no equivalent pre-existing audited wrapper (`
SecurityEngine.authenticate()` performs full credential login, a different
operation from verifying an already-issued session token), so this module
records that step's audit trail directly via the public
`SecurityEngine.audit_manager` API and the same `SecurityAuthSuccessEvent`/
`SecurityAuthFailureEvent` types Milestone M6 already defined for
"authentication attempt succeeds/fails" — reusing existing audit
infrastructure, not inventing new event types. Audit recording is
best-effort and never blocks or fails closed the dispatch decision itself
— mirroring `SecurityEngine`'s own established policy that an audit-store
outage must not itself become a lockout of an already-correctly-made
security decision.

`required_permissions=None` on a `CapabilityDescriptor` means the capability
has never been explicitly classified: no RBAC permission check is performed
for it, but authentication (when `requires_authentication=True`, the
default) and every other fail-closed check — ABAC tenant matching,
classification — remain fully applicable. `None` does not mean unrestricted,
does not grant permissions, and does not disable any other check. This
milestone establishes the enforcement mechanism; authoring real permission
metadata for every existing engine's capabilities is separate, future,
engine-by-engine work — this milestone must not be described as achieving
least-privilege completion for currently-unclassified capabilities.

Core -> Security Engine imports here are consistent with `kernel.py`'s own
existing precedent of importing concrete classes directly from
`kortex.engines.boot`, `kortex.engines.configuration`, `kortex.engines.event`,
and `kortex.engines.registry` (see `kernel.py`'s own import block). Kernel is
not hermetically isolated from `kortex.engines.*`; the constraint is that it
must not contain business logic, which this module does not — it holds
references and calls existing methods, evaluating nothing itself.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from pydantic import BaseModel, Field

from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.events import SecurityAuthFailureEvent, SecurityAuthSuccessEvent
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import ClassificationLevel, PermissionRequirement, TokenPayload

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.registry.engine import CapabilityDescriptor

logger = logging.getLogger("kortex.core.dispatch")


# Duplicated from `kortex.engines.security.engine._actor_type_for_principal_type`
# rather than imported: that helper is module-private (single-underscore) to
# `engine.py`, so this dispatcher keeps its own copy instead of reaching
# across that privacy boundary. Both copies must stay in sync with
# `UniversalAuditEntry.actor_type`'s frozen vocabulary (`shared_domain_models.md`
# §11: HUMAN/AI_AGENT/SYSTEM_ENGINE/CONNECTOR) if it ever changes.
_PRINCIPAL_TYPE_TO_ACTOR_TYPE: Dict[str, str] = {
    "USER": "HUMAN",
    "AGENT": "AI_AGENT",
    "SERVICE_PRINCIPAL": "CONNECTOR",
}


def _actor_type_for_principal_type(principal_type: str) -> str:
    """Fail-closed to `SYSTEM_ENGINE` for any unrecognized principal type
    string, rather than propagating an unknown value into the audit trail."""
    return _PRINCIPAL_TYPE_TO_ACTOR_TYPE.get(principal_type, "SYSTEM_ENGINE")


class CapabilityRequest(BaseModel):
    """Untrusted, caller-constructed request to invoke a capability through
    the Kernel's sanctioned dispatch path.

    Nothing in this model is trusted as authoritative security state —
    `session_token` is verified (never assumed genuine), and
    `parameters`/`context` can never supply or override `required_permissions`,
    `requires_authentication`, `security_classification`, an authentication
    result, or an authorization decision. Those always come from the
    resolved `CapabilityDescriptor` and from calling Security Engine's own
    `AuthenticationManager`/`AuthorizationEngine`.
    """

    capability_name: str
    session_token: Optional[TokenPayload] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)


def _safe_classification(value: str) -> ClassificationLevel:
    """Convert a descriptor's plain `security_classification` string into a
    `ClassificationLevel`, defaulting any unparseable value to `RESTRICTED`.

    This is the opposite fail-closed direction from `abac.py`'s own
    `_classification_rank` fallback, which correctly defaults an unparseable
    *principal clearance* to `PUBLIC` so an unset clearance never grants
    elevation. Here we are defaulting the *requirement's* classification —
    the fail-closed direction for a requirement is the most restrictive
    rank, not the least. Defaulting to `PUBLIC` here would make a malformed
    classification trivially satisfiable by any principal, which is
    fail-open, not fail-closed.
    """
    try:
        return ClassificationLevel(value)
    except ValueError:
        return ClassificationLevel.RESTRICTED


class CapabilityDispatcher:
    """Coordinates the enforcement boundary for `Kernel.invoke_capability()`.

    A plain coordinating object — not a `BaseEngine`, not lifecycle-managed.
    Holds only a reference back to the owning `Kernel`; carries no
    per-request state as instance attributes, so concurrent `dispatch()`
    calls cannot leak identity or decision state into one another.
    """

    def __init__(self, kernel: "Kernel") -> None:
        self._kernel = kernel

    async def dispatch(self, request: CapabilityRequest) -> Any:
        """Resolve, authenticate, authorize, then invoke — in that exact order.

        Raises `CapabilityNotFoundError` if the capability is unregistered,
        `AuthenticationError` (or a subtype) if authentication is required
        and fails, `AuthorizationDeniedError` if authorization denies, and
        `SecurityEngineError` if Security Engine itself is unreachable or an
        underlying operation fails. The handler is invoked only after every
        applicable check succeeds. Authentication and authorization outcomes
        are recorded to Security Engine's Milestone M6 audit trail (see
        module docstring) before the handler is ever reached.
        """
        descriptor: "CapabilityDescriptor" = self._kernel.get_capability(request.capability_name)

        if not descriptor.requires_authentication:
            return await self._invoke_handler(request)

        security_engine = cast(SecurityEngine, self._kernel.get_engine("security"))

        if request.session_token is None:
            raise AuthenticationError(
                f"A session token is required to invoke capability '{request.capability_name}'."
            )

        try:
            principal = await security_engine.authentication_manager.verify_token(request.session_token)
        except Exception as exc:
            await self._audit_authentication_failure(security_engine, request.session_token, exc)
            raise
        await self._audit_authentication_success(security_engine, principal)

        requirement = PermissionRequirement(
            capability_name=descriptor.name,
            required_permissions=list(descriptor.required_permissions or []),
            security_classification=_safe_classification(descriptor.security_classification),
        )

        # `SecurityEngine.authorize()` (not the lower `AuthorizationEngine.authorize_strict()`)
        # so this decision is recorded to the audit trail — see module docstring.
        decision = await security_engine.authorize(principal, requirement, dict(request.context))
        if not decision.is_allowed:
            raise AuthorizationDeniedError(decision.reason)

        return await self._invoke_handler(request)

    async def _audit_authentication_success(self, security_engine: SecurityEngine, principal: Any) -> None:
        """Best-effort audit recording for a successful token verification.
        Never raises — an audit-store outage must not block a security
        decision that has already been correctly made (see module docstring)."""
        try:
            await security_engine.audit_manager.record_event(
                action="kortex.kernel.dispatch.authenticate",
                actor_id=principal.principal_id,
                actor_type=_actor_type_for_principal_type(principal.principal_type.value),
                tenant_id=principal.tenant_id,
                context={"result": "success"},
            )
            await security_engine.audit_manager.publish_security_event(
                SecurityAuthSuccessEvent(
                    tenant_id=principal.tenant_id,
                    principal_id=principal.principal_id,
                    principal_type=principal.principal_type.value,
                )
            )
        except Exception as exc:
            logger.warning("Failed to record dispatch authentication-success audit entry: %s", exc)

    async def _audit_authentication_failure(
        self, security_engine: SecurityEngine, session_token: TokenPayload, exc: Exception
    ) -> None:
        """Best-effort audit recording for a failed token verification.

        Uses the *claimed* identity from the presented (unverified) token —
        the only identity available before verification succeeds, mirroring
        `SecurityEngine.authenticate()`'s own use of caller-supplied
        credentials on an authentication failure. A wholly absent token
        (`session_token is None`) never reaches this method — that case is
        rejected before any Security Engine call is made, so `session_token`
        is always present here.
        """
        claimed_principal_id = session_token.principal_id
        claimed_tenant_id = session_token.tenant_id
        claimed_principal_type = session_token.principal_type.value
        try:
            await security_engine.audit_manager.record_event(
                action="kortex.kernel.dispatch.authenticate",
                actor_id=claimed_principal_id,
                actor_type=_actor_type_for_principal_type(claimed_principal_type),
                tenant_id=claimed_tenant_id,
                context={"result": "failure", "reason": type(exc).__name__},
            )
            await security_engine.audit_manager.publish_security_event(
                SecurityAuthFailureEvent(
                    tenant_id=claimed_tenant_id,
                    principal_id=claimed_principal_id,
                    reason=type(exc).__name__,
                )
            )
        except Exception as audit_exc:
            logger.warning("Failed to record dispatch authentication-failure audit entry: %s", audit_exc)

    async def _invoke_handler(self, request: CapabilityRequest) -> Any:
        """Resolve and invoke the real handler, awaiting the result only if
        it is actually awaitable.

        Milestone M8: the handler is resolved via `RegistryEngine`'s
        internal, dispatcher-only `_resolve_handler()` — never via
        `descriptor.handler`, which no longer exists. This is reached
        through `Kernel`'s own already-private `_registry_engine`
        attribute; `CapabilityDispatcher` lives inside the same trust
        boundary as `Kernel` (it is constructed by `Kernel.__init__` and
        holds a `Kernel` reference), so this is not a new public API —
        no new method was added to `Kernel`'s public surface for this.

        Checking `inspect.isawaitable(result)` *after* calling — rather
        than `asyncio.iscoroutinefunction(handler)` *before* calling —
        correctly handles a callable class instance with an `async def
        __call__` (which `iscoroutinefunction` misclassifies as sync, since
        it inspects the object itself, not what calling it produces), in
        addition to plain sync and async functions.
        """
        handler = self._kernel._registry_engine._resolve_handler(request.capability_name)
        if handler is None:
            raise RuntimeError(f"Capability '{request.capability_name}' has no registered handler.")
        result = handler(**request.parameters)
        if inspect.isawaitable(result):
            return await result
        return result
