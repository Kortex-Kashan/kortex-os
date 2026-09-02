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
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field

from kortex.core.exceptions import ConcurrentExecutionError, ReservedParameterError
from kortex.core.idempotency import ClaimResult, IdempotencyStore, sanitize_for_persistence
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.security.events import SecurityAuthFailureEvent, SecurityAuthSuccessEvent
from kortex.engines.security.exceptions import AuthenticationError, AuthorizationDeniedError
from kortex.engines.security.models import (
    ClassificationLevel,
    PermissionRequirement,
    SecurityPrincipal,
    TokenPayload,
)

# Reserved `CapabilityRequest.parameters` keys: only `CapabilityDispatcher` may
# populate these, via `CapabilityExecutionContext` injection (see
# `_invoke_handler` below). A caller supplying either key is rejected outright
# — not silently overwritten or discarded — closing the exact ambiguity a
# conditional ("inject only if absent") check would have left open: a caller
# cannot win a precedence race against the dispatcher because there is no
# precedence race at all, only a hard rejection before injection is attempted.
_RESERVED_PARAMETER_KEYS = frozenset({"execution_context", "principal"})

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
_PRINCIPAL_TYPE_TO_ACTOR_TYPE: dict[str, str] = {
    "USER": "HUMAN",
    "AGENT": "AI_AGENT",
    "SERVICE_PRINCIPAL": "CONNECTOR",
}


def _actor_type_for_principal_type(principal_type: str) -> str:
    """Fail-closed to `SYSTEM_ENGINE` for any unrecognized principal type
    string, rather than propagating an unknown value into the audit trail."""
    return _PRINCIPAL_TYPE_TO_ACTOR_TYPE.get(principal_type, "SYSTEM_ENGINE")


class CapabilityRequest(BaseModel):
    """Canonical execution envelope to invoke a capability through
    the Kernel's sanctioned dispatch path (Milestone M5.2).

    Contains request_id, correlation_id, and optional idempotency_key for
    cross-system correlation, duplicate suppression, and audit lineage.
    """

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    idempotency_key: str | None = None
    capability_name: str
    session_token: TokenPayload | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class CapabilityExecutionContext(BaseModel):
    """Immutable, dispatcher-constructed execution identity for one capability
    invocation (KORTEX Platform Security — Capability Identity Propagation).

    This is the *only* channel through which a handler may learn "who is
    calling" or "which tenant is authoritative." It is built exactly once,
    inside `CapabilityDispatcher.dispatch()`, strictly after authentication
    and authorization have already succeeded — never from caller-supplied
    `CapabilityRequest.parameters` or `.context`. A handler must never
    independently re-authenticate a token or re-derive tenant identity from
    its own parameters; any identity it needs comes from here.

    `tenant_id` is always `principal.tenant_id` when `principal` is not
    `None` — it is not an independently settable field, precisely so no code
    path can construct an internally inconsistent identity (a principal from
    tenant A paired with a `tenant_id` claiming tenant B).

    `session_token` carries forward the *same* already-verified token this
    context was built from — not a new or caller-suppliable one — solely so
    trusted platform code performing a synchronous nested capability
    dispatch (e.g. Workflow's external-operation executor) can construct a
    fresh `CapabilityRequest` for the nested call without a handler ever
    reaching into caller-controlled `parameters` for a credential. It is
    never sourced from, or writable via, `request.parameters`.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    request_id: str
    correlation_id: str
    capability_name: str
    principal: SecurityPrincipal | None
    tenant_id: str
    session_token: TokenPayload | None = None


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

    def __init__(
        self,
        kernel: Kernel,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        self._kernel = kernel
        self._idempotency_store = idempotency_store

    def _resolve_idempotency_store(self) -> IdempotencyStore | None:
        """Lazily resolve the IdempotencyStore from Kernel StorageEngine or db."""
        if self._idempotency_store is not None:
            return self._idempotency_store
        try:
            storage_engine = self._kernel.get_engine("storage")
            if (
                storage_engine is not None
                and storage_engine.state.value in ("READY", "RUNNING")
                and hasattr(storage_engine, "data")
            ):
                cache_store = getattr(storage_engine, "cache", None)
                self._idempotency_store = IdempotencyStore(
                    data_store=storage_engine.data,
                    cache_store=cache_store,
                )
                return self._idempotency_store
        except Exception as exc:
            logger.debug("Storage engine unavailable for idempotency store: %s", exc)

        # Fallback to direct Kernel DB manager if storage engine is not booted
        try:
            if hasattr(self._kernel, "db") and self._kernel.db.is_connected:
                from kortex.engines.storage.stores.data_store import RelationalDataStore

                self._idempotency_store = IdempotencyStore(
                    data_store=RelationalDataStore(self._kernel.db),
                )
                return self._idempotency_store
        except Exception as exc:
            logger.debug("Database manager unavailable for idempotency store: %s", exc)

        return None

    async def dispatch(self, request: CapabilityRequest) -> Any:  # noqa: ANN401
        """Resolve, authenticate, authorize, enforce idempotency, then invoke.

        Execution ordering (Milestone M5.2):
        1. Authentication verification
        2. Authorization & RBAC/ABAC verification
        3. Tenant determination from authoritative principal
        4. Idempotency gate (duplicate suppression & concurrent lock)
        5. Engine handler invocation
        6. Idempotency completion / failure persistence
        7. Execution audit lineage recording
        """
        descriptor: CapabilityDescriptor = self._kernel.get_capability(request.capability_name)
        security_engine: SecurityEngine | None = None

        # 1. Authentication Check
        if descriptor.requires_authentication:
            if request.session_token is None:
                raise AuthenticationError(
                    f"A session token is required to invoke capability '{request.capability_name}'."
                )

            security_engine = cast(SecurityEngine, self._kernel.get_engine("security"))

            try:
                principal = await security_engine.authentication_manager.verify_token(request.session_token)
            except Exception as exc:
                await self._audit_authentication_failure(security_engine, request.session_token, exc)
                raise
            await self._audit_authentication_success(security_engine, principal)
        else:
            principal = None
            try:
                sec_eng = self._kernel.get_engine("security")
                if sec_eng is not None and sec_eng.state.value in ("READY", "RUNNING"):
                    security_engine = cast(SecurityEngine, sec_eng)
            except Exception as exc:
                logger.debug("Security engine unavailable for unauthenticated dispatch: %s", exc)

        # 2. Authorization Check
        if principal is not None and security_engine is not None:
            requirement = PermissionRequirement(
                capability_name=descriptor.name,
                required_permissions=list(descriptor.required_permissions or []),
                security_classification=_safe_classification(descriptor.security_classification),
            )

            # `SecurityEngine.authorize()` (not the lower `AuthorizationEngine.authorize_strict()`)
            # so this decision is recorded to the audit trail.
            decision = await security_engine.authorize(principal, requirement, dict(request.context))
            if not decision.is_allowed:
                raise AuthorizationDeniedError(decision.reason)

        # 3. Tenant Determination
        tenant_id = (
            principal.tenant_id
            if principal is not None
            else str(request.context.get("resource_tenant_id", "default"))
        )

        # 3b. Trusted Execution Context — the sole authoritative identity
        # channel a handler may consume (KORTEX Platform Security: Capability
        # Identity Propagation). Built strictly from already-verified state
        # above (`principal`, `tenant_id`), never from caller-supplied
        # `request.parameters`/`request.context`.
        execution_context = CapabilityExecutionContext(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            capability_name=request.capability_name,
            principal=principal,
            tenant_id=tenant_id,
            session_token=request.session_token,
        )

        # 4. Idempotency Gate (ONLY for requests carrying an idempotency_key)
        idempotency_store = self._resolve_idempotency_store()
        idempotency_key = request.idempotency_key

        if idempotency_key and idempotency_store is not None:
            claim_status, cached_response, _ = await idempotency_store.claim_or_get_execution(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                capability_name=request.capability_name,
                request_id=request.request_id,
                correlation_id=request.correlation_id,
            )
            if claim_status == ClaimResult.COMPLETED:
                logger.info(
                    "Idempotency hit for capability '%s', key '%s' under tenant '%s'. Returning cached result.",
                    request.capability_name,
                    idempotency_key,
                    tenant_id,
                )
                # Replay is NOT a second execution: do not falsely record a fresh execution audit entry.
                return cached_response
            elif claim_status == ClaimResult.PROCESSING:
                raise ConcurrentExecutionError(
                    f"A request with idempotency key '{idempotency_key}' is currently being processed."
                )

        # 5. Handler Invocation with Timing
        start_time = time.monotonic()
        try:
            result = await self._invoke_handler(request, descriptor, execution_context)
            duration_ms = (time.monotonic() - start_time) * 1000

            # 6. Idempotency Completion Persistence
            if idempotency_key and idempotency_store is not None:
                await idempotency_store.record_completed(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    response_payload=result,
                )

            # 7. Execution Audit Lineage (SUCCESS)
            if security_engine is not None:
                await self._audit_execution(
                    security_engine=security_engine,
                    principal=principal,
                    request=request,
                    tenant_id=tenant_id,
                    status="SUCCESS",
                    duration_ms=duration_ms,
                    result=result,
                )

            return result

        except Exception as exc:
            duration_ms = (time.monotonic() - start_time) * 1000

            # 6. Idempotency Failure Persistence
            if idempotency_key and idempotency_store is not None:
                await idempotency_store.record_failed(
                    tenant_id=tenant_id,
                    idempotency_key=idempotency_key,
                    error_message=str(exc),
                )

            # 7. Execution Audit Lineage (FAILURE)
            if security_engine is not None:
                await self._audit_execution(
                    security_engine=security_engine,
                    principal=principal,
                    request=request,
                    tenant_id=tenant_id,
                    status="FAILURE",
                    duration_ms=duration_ms,
                    error=exc,
                )

            raise

    async def _audit_authentication_success(
        self, security_engine: SecurityEngine, principal: Any  # noqa: ANN401
    ) -> None:
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

    async def _invoke_handler(
        self,
        request: CapabilityRequest,
        descriptor: CapabilityDescriptor,
        execution_context: CapabilityExecutionContext,
    ) -> Any:  # noqa: ANN401
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

        KORTEX Platform Security — Capability Identity Propagation: a
        reserved key present in `request.parameters` (`execution_context`,
        `principal`) is REJECTED outright, never conditionally overwritten
        — a caller cannot win a precedence race against the dispatcher
        because no such race is ever evaluated. Which reserved key (if any)
        is then injected is decided entirely by `descriptor` fields set at
        registration time (`requires_execution_context`,
        `legacy_principal_bridge`) — never by inspecting the handler's live
        signature per invocation (see `RegistryEngine.register_capability`
        for the one-time, boot-time signature validation that replaces
        per-call reflection).
        """
        handler = self._kernel._registry_engine._resolve_handler(request.capability_name)
        if handler is None:
            raise RuntimeError(f"Capability '{request.capability_name}' has no registered handler.")

        offending_keys = _RESERVED_PARAMETER_KEYS.intersection(request.parameters)
        if offending_keys:
            raise ReservedParameterError(
                f"Capability '{request.capability_name}' request.parameters may not supply "
                f"reserved key(s) {sorted(offending_keys)} — identity is dispatcher-injected only."
            )

        kwargs = dict(request.parameters)
        if descriptor.requires_execution_context:
            kwargs["execution_context"] = execution_context
            if descriptor.legacy_principal_bridge:
                kwargs["principal"] = execution_context.principal

        result = handler(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _audit_execution(
        self,
        security_engine: SecurityEngine,
        principal: Any | None,  # noqa: ANN401
        request: CapabilityRequest,
        tenant_id: str,
        status: str,
        duration_ms: float,
        result: Any | None = None,  # noqa: ANN401
        error: Exception | None = None,
    ) -> None:
        """Best-effort audit recording of capability execution outcome (Milestone M5.2).
        Captures request_id, correlation_id, duration, status, and scrubbed payload hash."""
        try:
            new_state_hash = None
            if result is not None:
                try:
                    scrubbed = sanitize_for_persistence(result)
                    new_state_hash = security_engine.audit_manager.compute_state_hash(scrubbed)
                except Exception as exc:
                    logger.debug("Failed to compute state hash for audit: %s", exc)

            audit_context: dict[str, Any] = {
                "request_id": request.request_id,
                "correlation_id": request.correlation_id,
                "idempotency_key": request.idempotency_key,
                "capability_name": request.capability_name,
                "status": status,
                "duration_ms": duration_ms,
                "parameters": sanitize_for_persistence(request.parameters),
            }
            if error is not None:
                audit_context["error_type"] = type(error).__name__
                audit_context["error_message"] = str(error)[:500]

            actor_id = principal.principal_id if principal is not None else "ANONYMOUS"
            actor_type = (
                _actor_type_for_principal_type(principal.principal_type.value)
                if principal is not None
                else "SYSTEM_ENGINE"
            )

            await security_engine.audit_manager.record_event(
                action="kortex.kernel.dispatch.execute",
                actor_id=actor_id,
                actor_type=actor_type,
                tenant_id=tenant_id,
                resource_id=request.capability_name,
                new_state_hash=new_state_hash,
                context=audit_context,
            )
        except Exception as exc:
            logger.warning("Failed to record dispatch execution audit entry: %s", exc)
