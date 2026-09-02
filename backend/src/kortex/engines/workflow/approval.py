"""
KORTEX Workflow Approval System & Human Governance Layer (Milestone M5.3).

Defines the ApprovalRepository and ApprovalProvider interfaces, the backward-compatible
in-memory MemoryApprovalManager, and the production SQLite-backed DurableApprovalManager.
Provides durable human governance, role delegations, expiration sweeps, and cryptographic
Ed25519 decision verification.
"""

from __future__ import annotations

import datetime
import logging
from datetime import UTC, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.outbox import OutboxStore
from kortex.engines.security.exceptions import (
    AuthorizationDeniedError,
    InvalidSignatureError,
)
from kortex.engines.security.models import CryptographicSignature, SecurityPrincipal, UniversalAuditEntry
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.workflow.exceptions import (
    ApprovalConflictError,
    WorkflowApprovalError,
)
from kortex.engines.workflow.models import (
    ApprovalDecision,
    ApprovalDelegation,
    ApprovalRequest,
    ApprovalState,
)
from kortex.engines.workflow.persistence import ApprovalStore

logger = logging.getLogger("kortex.engines.workflow.approval")

# Maps `PrincipalType` (USER/SERVICE_PRINCIPAL/AGENT) to
# `UniversalAuditEntry.actor_type`'s own, separate frozen vocabulary
# (HUMAN/AI_AGENT/SYSTEM_ENGINE/CONNECTOR). Duplicated from
# `kortex.engines.security.engine._actor_type_for_principal_type` /
# `kortex.core.dispatch._actor_type_for_principal_type` rather than
# imported -- both are module-private to their own files, and this module
# keeps its own copy following the same established precedent (M6.2-3: this
# fixes the approval audit trail's previous hardcoded
# `"HUMAN" if actor_id != "SYSTEM" else "SYSTEM_ENGINE"` mislabeling, which
# never consulted the actual authenticated principal's type at all).
_PRINCIPAL_TYPE_TO_ACTOR_TYPE: dict[str, str] = {
    "USER": "HUMAN",
    "AGENT": "AI_AGENT",
    "SERVICE_PRINCIPAL": "CONNECTOR",
}


def _actor_type_for_principal(principal: SecurityPrincipal | None, actor_id: str) -> str:
    """Derive an audit `actor_type` from a verified principal's own type.

    Falls back to the pre-M6.2 heuristic (`"HUMAN"`/`"SYSTEM_ENGINE"` by
    `actor_id`) only when no principal is available at all, preserving
    existing behavior for the few call sites that still legitimately have
    none (e.g. system-initiated expiry sweeps)."""
    if principal is not None:
        return _PRINCIPAL_TYPE_TO_ACTOR_TYPE.get(principal.principal_type.value, "SYSTEM_ENGINE")
    return "HUMAN" if actor_id != "SYSTEM" else "SYSTEM_ENGINE"


# ============================================================================
# 1. Protocols / Interfaces
# ============================================================================


@runtime_checkable
class ApprovalRepository(Protocol):
    """Protocol interface for storing and retrieving approval requests."""

    async def save_request(self, request: ApprovalRequest) -> None:
        """Save or update an approval request ticket."""
        ...

    async def get_request(self, request_id: UUID | str) -> ApprovalRequest | None:
        """Retrieve an approval request ticket by UUID."""
        ...

    async def list_pending_requests(self, role_filter: str | None = None) -> list[ApprovalRequest]:
        """List pending approval requests optionally filtered by required role."""
        ...


@runtime_checkable
class ApprovalProvider(Protocol):
    """Protocol interface for approval request lifecycle management."""

    async def create_request(
        self,
        instance_id: UUID | str | None = None,
        step_id: str | None = None,
        required_role: str = "",
        **kwargs: Any,
    ) -> ApprovalRequest:
        """Create a new approval request ticket."""
        ...

    async def submit_decision(self, decision: ApprovalDecision, **kwargs: Any) -> ApprovalRequest:
        """Submit an approval decision for a pending ticket."""
        ...


# ============================================================================
# 2. In-Memory Implementation (Testing / Development)
# ============================================================================


class MemoryApprovalManager(ApprovalRepository, ApprovalProvider):
    """In-memory approval manager implementing ApprovalRepository and ApprovalProvider."""

    def __init__(self) -> None:
        self._requests: dict[UUID, ApprovalRequest] = {}
        self._decisions: dict[UUID, ApprovalDecision] = {}

    async def save_request(self, request: ApprovalRequest) -> None:
        """Save or update an approval request ticket."""
        req_id = UUID(str(request.id)) if not isinstance(request.id, UUID) else request.id
        self._requests[req_id] = request
        logger.debug("Saved approval request '%s' for instance '%s'", request.id, request.instance_id)

    async def get_request(self, request_id: UUID | str) -> ApprovalRequest | None:
        """Retrieve an approval request ticket by UUID."""
        req_id = UUID(str(request_id)) if not isinstance(request_id, UUID) else request_id
        return self._requests.get(req_id)

    async def list_pending_requests(self, role_filter: str | None = None) -> list[ApprovalRequest]:
        """List pending approval requests."""
        results: list[ApprovalRequest] = []
        for req in self._requests.values():
            if req.state == ApprovalState.PENDING and (role_filter is None or req.required_role == role_filter):
                results.append(req)
        return results

    async def get_request_by_instance(self, instance_id: UUID | str) -> ApprovalRequest | None:
        """Retrieve the pending approval request for a workflow instance if one exists."""
        inst_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        for req in self._requests.values():
            req_inst_id = (
                UUID(str(req.instance_id))
                if req.instance_id and not isinstance(req.instance_id, UUID)
                else req.instance_id
            )
            if req_inst_id == inst_id and req.state == ApprovalState.PENDING:
                return req
        return None

    async def get_request_by_step(self, instance_id: UUID | str, step_id: str) -> ApprovalRequest | None:
        """Retrieve the approval request for a specific instance and step."""
        inst_id = UUID(str(instance_id)) if not isinstance(instance_id, UUID) else instance_id
        for req in self._requests.values():
            req_inst_id = (
                UUID(str(req.instance_id))
                if req.instance_id and not isinstance(req.instance_id, UUID)
                else req.instance_id
            )
            if req_inst_id == inst_id and req.step_id == step_id and req.state == ApprovalState.PENDING:
                return req
        return None

    async def create_request(
        self,
        instance_id: UUID | str | None = None,
        step_id: str | None = None,
        required_role: str = "",
        principal: SecurityPrincipal | None = None,
        correlation_id: str | None = None,
        action_fingerprint: str | None = None,
        **kwargs: Any,
    ) -> ApprovalRequest:
        """Create and register a new pending approval ticket."""
        inst_id: UUID | None = UUID(str(instance_id)) if instance_id is not None and str(instance_id).strip() else None
        request = ApprovalRequest(
            instance_id=inst_id,
            step_id=step_id,
            required_role=required_role,
            state=ApprovalState.PENDING,
            requester_principal_id=principal.principal_id if principal is not None else None,
            requester_principal_type=principal.principal_type.value if principal is not None else None,
            correlation_id=correlation_id,
            action_fingerprint=action_fingerprint,
        )
        await self.save_request(request)
        logger.info(
            "Created approval ticket '%s' for step '%s' (Required Role: '%s')",
            request.id,
            step_id,
            required_role,
        )
        return request

    async def submit_decision(self, decision: ApprovalDecision, **kwargs: Any) -> ApprovalRequest:
        """Submit an approval decision (APPROVED or REJECTED) for a pending ticket.

        Raises:
            WorkflowApprovalError: If request is missing or already decided.
        """
        req_id = UUID(str(decision.request_id)) if not isinstance(decision.request_id, UUID) else decision.request_id
        request = await self.get_request(req_id)
        if not request:
            raise WorkflowApprovalError(f"Approval request ticket '{decision.request_id}' not found.")

        if request.state != ApprovalState.PENDING:
            raise WorkflowApprovalError(
                f"Approval request ticket '{decision.request_id}' is already in state '{request.state.value}'."
            )

        if decision.decision not in (ApprovalState.APPROVED, ApprovalState.REJECTED):
            raise WorkflowApprovalError(f"Invalid decision state '{decision.decision}'. Must be APPROVED or REJECTED.")

        request.state = decision.decision
        self._decisions[req_id] = decision
        await self.save_request(request)

        logger.info(
            "Processed decision '%s' for approval ticket '%s' by approver '%s'",
            decision.decision.value,
            request.id,
            decision.approver_id,
        )
        return request


# ============================================================================
# 3. Durable Relational Implementation (M5.3 Production Authority)
# ============================================================================


class DurableApprovalManager(ApprovalRepository, ApprovalProvider):
    """Production SQLite-backed approval manager implementing ApprovalRepository and ApprovalProvider.

    Guarantees:
    - ACID transaction boundaries on ticket mutations and decision insertions.
    - Strict multi-tenant data partitioning.
    - Role authorization and time-bounded delegation enforcement.
    - Cryptographic Ed25519 signature verification on decision evidence.
    - Expiration sweeps and domain event staging via transactional outbox.
    - Immutable audit trails recorded via SecurityEngine.audit_manager.
    """

    def __init__(
        self,
        data_store: IDataStore,
        security_engine: Any = None,
        outbox_store: OutboxStore | None = None,
        event_engine: Any = None,
    ) -> None:
        self._data_store = data_store
        self._security_engine = security_engine
        self._outbox_store = outbox_store
        self._event_engine = event_engine
        self._store = ApprovalStore(data_store)
        logger.debug("DurableApprovalManager initialized with IDataStore.")

    # -- Internal Audit & Crypto Helpers --------------------------------------

    async def _record_audit(
        self,
        action: str,
        actor_id: str,
        tenant_id: str,
        resource_id: str | None = None,
        context: dict[str, Any] | None = None,
        principal: SecurityPrincipal | None = None,
    ) -> None:
        """Record an immutable UniversalAuditEntry via SecurityEngine.

        `actor_type` (M6.2-3) is derived from `principal.principal_type`
        when a verified principal is available, correctly labeling an
        AI-originated ticket/decision `AI_AGENT` instead of the previous
        hardcoded `"HUMAN"` fallback."""
        if self._security_engine is not None:
            audit_mgr = getattr(self._security_engine, "_audit_manager", None)
            if audit_mgr is None:
                try:
                    audit_mgr = getattr(self._security_engine, "audit_manager", None)
                except Exception:
                    audit_mgr = None
            if audit_mgr is not None:
                try:
                    entry = UniversalAuditEntry(
                        action=action,
                        actor_id=actor_id,
                        actor_type=_actor_type_for_principal(principal, actor_id),
                        tenant_id=tenant_id,
                        resource_id=resource_id,
                        context=sanitize_for_persistence(context or {}),
                    )
                    await audit_mgr.record_audit_entry(entry)
                except Exception as exc:
                    logger.error("Failed to record audit entry for '%s': %s", action, exc)

    def _get_verification_service(self) -> Any:
        """Resolve VerificationService from SecurityEngine if wired."""
        if self._security_engine is not None:
            vs = getattr(self._security_engine, "_verification_service", None)
            if vs is not None:
                return vs
            try:
                return getattr(self._security_engine, "verification_service", None)
            except Exception:
                return None
        return None

    # -- Ticket Repository Methods -------------------------------------------

    async def save_request(self, request: ApprovalRequest, tenant_id: str = "default") -> None:
        """Persist or update an ApprovalRequest ticket in the database."""
        await self._store.save_request(request, tenant_id=tenant_id)

    async def get_request(self, request_id: UUID | str, tenant_id: str | None = None) -> ApprovalRequest | None:
        """Retrieve an approval request ticket by UUID/ID with optional tenant isolation."""
        return await self._store.get_request(request_id, tenant_id=tenant_id)

    async def list_pending_requests(
        self, role_filter: str | None = None, tenant_id: str = "default"
    ) -> list[ApprovalRequest]:
        """List pending approval requests within a tenant boundary."""
        return await self._store.list_requests(tenant_id=tenant_id, role_filter=role_filter, state_filter="PENDING")

    async def list_requests(
        self,
        tenant_id: str = "default",
        role_filter: str | None = None,
        state_filter: str | None = None,
    ) -> list[ApprovalRequest]:
        """List approval requests matching criteria within a tenant boundary."""
        return await self._store.list_requests(tenant_id=tenant_id, role_filter=role_filter, state_filter=state_filter)

    async def get_request_by_instance(
        self, instance_id: UUID | str, tenant_id: str | None = None
    ) -> ApprovalRequest | None:
        """Retrieve the pending approval request for a workflow instance."""
        return await self._store.get_request_by_instance(instance_id, tenant_id=tenant_id)

    async def get_request_by_step(
        self, instance_id: UUID | str, step_id: str, tenant_id: str | None = None
    ) -> ApprovalRequest | None:
        """Retrieve the pending approval request for a specific instance and step."""
        return await self._store.get_request_by_step(instance_id, step_id, tenant_id=tenant_id)

    # -- Ticket Lifecycle & Decision Methods ----------------------------------

    async def create_request(
        self,
        instance_id: UUID | str | None = None,
        step_id: str | None = None,
        required_role: str = "",
        tenant_id: str = "default",
        timeout_seconds: int | None = None,
        context_snapshot: dict[str, Any] | None = None,
        signature_required: bool = False,
        principal: SecurityPrincipal | None = None,
        correlation_id: str | None = None,
        action_fingerprint: str | None = None,
        **kwargs: Any,
    ) -> ApprovalRequest:
        """Create and persist a new pending approval ticket.

        `principal` (M6.2-3), when present, is recorded as the ticket's
        `requester_principal_id`/`requester_principal_type` -- a real,
        persisted requester identity that `submit_decision` can compare a
        decision-maker against (self-approval prevention), and that the
        audit trail can use to correctly attribute an AI-originated ticket
        rather than mislabeling it `HUMAN`.
        """
        inst_id: UUID | None = UUID(str(instance_id)) if instance_id is not None and str(instance_id).strip() else None
        timeout_at = (
            datetime.datetime.now(UTC) + timedelta(seconds=timeout_seconds) if timeout_seconds is not None else None
        )
        sanitized_context = sanitize_for_persistence(context_snapshot or {})

        request = ApprovalRequest(
            id=uuid4(),
            tenant_id=tenant_id,
            instance_id=inst_id,
            step_id=step_id,
            required_role=required_role,
            state=ApprovalState.PENDING,
            timeout_at=timeout_at,
            context_snapshot=sanitized_context,
            signature_required=signature_required,
            requester_principal_id=principal.principal_id if principal is not None else None,
            requester_principal_type=principal.principal_type.value if principal is not None else None,
            correlation_id=correlation_id,
            action_fingerprint=action_fingerprint,
        )
        # Atomically save ticket and stage outbox event in same transaction
        await self._store.save_request(
            request,
            tenant_id=tenant_id,
            outbox_store=self._outbox_store,
        )

        # Record universal audit entry
        await self._record_audit(
            action="kortex.workflow.approval.create",
            actor_id=principal.principal_id if principal is not None else "SYSTEM",
            tenant_id=tenant_id,
            resource_id=str(request.id),
            context={
                "required_role": required_role,
                "instance_id": str(instance_id) if instance_id else None,
                "step_id": step_id,
                "timeout_seconds": timeout_seconds,
                "correlation_id": correlation_id,
            },
            principal=principal,
        )

        logger.info(
            "Created durable approval ticket '%s' for step '%s' (Required Role: '%s', Tenant: '%s')",
            request.id,
            step_id,
            required_role,
            tenant_id,
        )
        return request

    async def create_delegation(
        self,
        delegator_id: str,
        delegatee_id: str,
        role: str,
        valid_from: datetime.datetime,
        valid_until: datetime.datetime,
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
        **kwargs: Any,
    ) -> ApprovalDelegation:
        """Create and persist a new approver role delegation."""
        dt_from = valid_from.replace(tzinfo=UTC) if valid_from.tzinfo is None else valid_from
        dt_until = valid_until.replace(tzinfo=UTC) if valid_until.tzinfo is None else valid_until

        if dt_from >= dt_until:
            raise WorkflowApprovalError("Delegation valid_from timestamp must precede valid_until timestamp.")

        # SECURITY (M5-A2): same fail-closed requirement as `submit_decision` —
        # a delegation must be authorized against a verified principal, never
        # created unconditionally just because none was supplied.
        if principal is None:
            raise AuthorizationDeniedError(
                "Authentication required: a role delegation must be created by a verified, authenticated principal."
            )
        if principal.tenant_id != tenant_id:
            raise AuthorizationDeniedError(
                f"Principal tenant '{principal.tenant_id}' does not match delegation tenant '{tenant_id}'."
            )
        if (
            principal.principal_id != delegator_id
            and "admin" not in principal.roles
            and "SECURITY_ADMIN" not in principal.roles
        ):
            raise AuthorizationDeniedError(
                f"Principal '{principal.principal_id}' cannot delegate on behalf of delegator '{delegator_id}'."
            )
        has_role = role in principal.roles or "admin" in principal.roles or "SECURITY_ADMIN" in principal.roles
        if not has_role:
            raise AuthorizationDeniedError(f"Delegator '{delegator_id}' does not possess role '{role}' to delegate.")

        delegation = ApprovalDelegation(
            id=uuid4(),
            tenant_id=tenant_id,
            delegator_id=delegator_id,
            delegatee_id=delegatee_id,
            role=role,
            valid_from=dt_from,
            valid_until=dt_until,
            is_active=True,
        )
        await self._store.save_delegation(delegation, tenant_id=tenant_id)

        # Record universal audit entry
        await self._record_audit(
            action="kortex.workflow.approval.delegate",
            actor_id=delegator_id,
            tenant_id=tenant_id,
            resource_id=str(delegation.id),
            context={
                "delegatee_id": delegatee_id,
                "role": role,
                "valid_from": dt_from.isoformat(),
                "valid_until": dt_until.isoformat(),
            },
        )

        logger.info(
            "Created approval delegation '%s' from '%s' to '%s' for role '%s' (Tenant: '%s')",
            delegation.id,
            delegator_id,
            delegatee_id,
            role,
            tenant_id,
        )
        return delegation

    async def submit_decision(
        self,
        decision: ApprovalDecision,
        principal: SecurityPrincipal | None = None,
        tenant_id: str | None = None,
        **kwargs: Any,
    ) -> ApprovalRequest:
        """Submit an approval decision (APPROVED or REJECTED) with cryptographic verification.

        Raises:
            WorkflowApprovalError: If request is missing, invalid state, or authorization fails.
            ApprovalConflictError: If request is already decided or concurrent decision collision.
            InvalidSignatureError: If cryptographic signature is invalid, tampered, or missing when required.
            AuthorizationDeniedError: If principal lacks role and valid active delegation.
        """
        tid = tenant_id or decision.tenant_id or "default"

        # 1. Fetch ticket under authoritative tenant boundary
        ticket = await self._store.get_request(decision.request_id, tenant_id=tid)
        if ticket is None:
            raise WorkflowApprovalError(f"Approval request ticket '{decision.request_id}' not found.")

        if ticket.state != ApprovalState.PENDING:
            raise ApprovalConflictError(
                f"Approval request ticket '{decision.request_id}' is already in state '{ticket.state.value}'."
            )

        if decision.decision not in (ApprovalState.APPROVED, ApprovalState.REJECTED):
            raise WorkflowApprovalError(f"Invalid decision state '{decision.decision}'. Must be APPROVED or REJECTED.")

        # 2. Verify Principal Authorization & Delegation
        #
        # SECURITY (M5-A2): `principal` must arrive already verified — either
        # forwarded by the Kernel dispatcher from its own token verification
        # (`CapabilityDispatcher._invoke_handler`, M5-A1) or supplied directly
        # by a trusted in-process caller. There is deliberately no fallback
        # that resolves an identity from `decision.approver_id` (a plain,
        # caller-controlled string): looking a principal up by that ID with
        # no credential/token check would let any caller decide as anyone
        # they name, which is exactly the impersonation this method exists
        # to prevent. A missing principal fails closed unconditionally, even
        # when the ticket has no `required_role`, so a decision can never be
        # recorded against an unverified actor identity.
        if principal is None:
            raise AuthorizationDeniedError(
                "Authentication required: an approval decision must be submitted by a "
                "verified, authenticated principal — a caller-supplied approver_id alone "
                "is not sufficient."
            )

        if principal.tenant_id != ticket.tenant_id:
            raise AuthorizationDeniedError(
                f"Principal tenant '{principal.tenant_id}' does not match ticket tenant '{ticket.tenant_id}'."
            )
        if principal.principal_id != decision.approver_id:
            raise AuthorizationDeniedError(
                f"Approver ID '{decision.approver_id}' does not match "
                f"authenticated principal ID '{principal.principal_id}'."
            )

        # SECURITY (M6.2-3): defense in depth against self-approval. The
        # primary control is role-scoping (an AI or human requester is never
        # granted the `required_role` its own tickets carry), but that is an
        # operational/configuration invariant, not something this method can
        # verify on its own. `requester_principal_id` (recorded at ticket
        # creation, M6.2-3) is a real, persisted fact this method CAN check
        # directly: a principal may never decide a ticket it itself
        # requested, regardless of role. Only enforced when the ticket
        # actually recorded a requester -- tickets created before this field
        # existed, or by callers that never supplied `principal`, have
        # `requester_principal_id is None` and are unaffected (they carry no
        # requester claim to compare against).
        if ticket.requester_principal_id is not None and ticket.requester_principal_id == principal.principal_id:
            raise AuthorizationDeniedError(
                f"Principal '{principal.principal_id}' cannot decide an approval ticket it itself requested."
            )

        # Check direct role
        has_role = ticket.required_role in principal.roles
        if not has_role and ticket.required_role:
            # Check active delegation
            delegation = await self._store.get_active_delegation(
                tenant_id=ticket.tenant_id,
                delegatee_id=principal.principal_id,
                role=ticket.required_role,
                at_time=datetime.datetime.now(UTC),
            )
            if delegation is None:
                raise AuthorizationDeniedError(
                    f"Principal '{principal.principal_id}' lacks required role '{ticket.required_role}' "
                    f"and possesses no active delegation."
                )

        if decision.decided_at.tzinfo is None:
            decision.decided_at = decision.decided_at.replace(tzinfo=UTC)

        # 3. Cryptographic Signature Verification
        if ticket.signature_required and not decision.signature_hex:
            raise InvalidSignatureError("Cryptographic Ed25519 signature is mandatory for this approval ticket.")

        if decision.signature_hex:
            verifier = self._get_verification_service()
            if verifier is None:
                raise InvalidSignatureError(
                    "No cryptographic verification service available to verify decision signature."
                )

            # Authoritative identity-to-key binding:
            if principal is None or "public_key" not in principal.attributes:
                raise InvalidSignatureError(
                    "Cannot verify signature: approver identity has no authoritative public key bound "
                    "in principal attributes."
                )

            auth_pk_raw = principal.attributes["public_key"]
            auth_pk_hex = auth_pk_raw if isinstance(auth_pk_raw, str) else bytes(auth_pk_raw).hex()

            if decision.public_key_hex and decision.public_key_hex.lower() != auth_pk_hex.lower():
                raise InvalidSignatureError(
                    "Provided public key does not match authoritative public key registered for approver identity."
                )

            try:
                pk_bytes = bytes.fromhex(auth_pk_hex)
            except ValueError as err:
                raise InvalidSignatureError("Authoritative principal public key attribute is malformed.") from err

            try:
                sig_bytes = bytes.fromhex(decision.signature_hex)
            except ValueError as err:
                raise InvalidSignatureError("Malformed signature hex string.") from err

            # Canonical representation: f"{request_id}:{decision}:{approver_id}:{decided_at_iso}"
            dec_val = decision.decision.value if hasattr(decision.decision, "value") else str(decision.decision)
            canonical_payload = (
                f"{ticket.id}:{dec_val}:{decision.approver_id}:{decision.decided_at.isoformat()}".encode()
            )

            sig_obj = CryptographicSignature(
                algorithm="ed25519",
                signature=sig_bytes,
                public_key=pk_bytes,
            )

            is_valid = verifier.verify_signature(canonical_payload, sig_obj)
            if not is_valid:
                raise InvalidSignatureError("Signature verification failed: payload, key, or algorithm mismatch.")

        # 4. Atomic Transactional Decision Commit (updates ticket, saves decision, stages outbox event)
        updated_ticket = await self._store.atomic_submit_decision(
            decision=decision,
            tenant_id=tid,
            outbox_store=self._outbox_store,
        )

        # 5. Record Universal Audit Entry
        dec_val = decision.decision.value if hasattr(decision.decision, "value") else str(decision.decision)
        await self._record_audit(
            action="kortex.workflow.approval.decide",
            actor_id=decision.approver_id,
            tenant_id=tid,
            resource_id=str(decision.request_id),
            context={
                "decision": dec_val,
                "reason": decision.reason,
                "signed": bool(decision.signature_hex),
                "instance_id": str(ticket.instance_id) if ticket.instance_id else None,
                "step_id": ticket.step_id,
                "correlation_id": ticket.correlation_id,
            },
            principal=principal,
        )

        logger.info(
            "Processed decision '%s' for approval ticket '%s' by approver '%s' (Tenant: '%s')",
            dec_val,
            updated_ticket.id,
            decision.approver_id,
            tid,
        )
        return updated_ticket

    async def sweep_expired_requests(self, tenant_id: str | None = None) -> list[ApprovalRequest]:
        """Sweep and transition all timed-out PENDING tickets to EXPIRED."""
        now_utc = datetime.datetime.now(UTC)
        expired_pending = await self._store.get_expired_pending_requests(before_time=now_utc, tenant_id=tenant_id)
        expired_results: list[ApprovalRequest] = []

        for req in expired_pending:
            tid = req.tenant_id
            expired_ticket = await self._store.atomic_expire_request(
                request_id=req.id,
                tenant_id=tid,
                outbox_store=self._outbox_store,
            )
            if expired_ticket:
                expired_results.append(expired_ticket)
                await self._record_audit(
                    action="kortex.workflow.approval.expire",
                    actor_id="SYSTEM",
                    tenant_id=tid,
                    resource_id=str(req.id),
                    context={
                        "instance_id": str(req.instance_id) if req.instance_id else None,
                        "step_id": req.step_id,
                        "timeout_at": req.timeout_at.isoformat() if req.timeout_at else None,
                    },
                )
                logger.info(
                    "Expired approval ticket '%s' for instance '%s' (Tenant: '%s')",
                    req.id,
                    req.instance_id,
                    tid,
                )

                # M6.4-1: publish on the SAME topic/contract as a human
                # APPROVED/REJECTED decision (`WorkflowEngine
                # .decide_approval_request`), rather than the separate,
                # never-delivered `workflow.approval.expired` outbox event
                # `atomic_expire_request` already stages (M6.3 planning
                # audit: `OutboxStore.dispatch_pending` has no production
                # caller anywhere in this codebase, so that outbox event is
                # dead on arrival regardless of this change). Both existing
                # subscribers (`AIOrchestrationEngine._on_approval_decided`,
                # `ExternalExecutionManager.on_approval_decided`) already
                # treat any `decision != "APPROVED"` as a cancellation, with
                # no `"REJECTED"`-specific check -- so EXPIRED is handled
                # correctly by them with zero subscriber-side changes.
                # Every value below is read from the ticket's own persisted,
                # already-tenant-scoped record -- there is no caller/
                # principal at sweep time to (mis)trust instead, and
                # `decider_session_token` is deliberately omitted: no human
                # decider exists for an expiry, so there is no token to mint
                # (and, per M6.4-0, this event is also relayed externally --
                # omitting a token here needs no redaction to already be safe).
                if self._event_engine is not None:
                    try:
                        await self._event_engine.publish(
                            topic="workflow.approval.decided",
                            payload={
                                "request_id": str(expired_ticket.id),
                                "tenant_id": tid,
                                "decision": ApprovalState.EXPIRED.value,
                                "correlation_id": expired_ticket.correlation_id,
                                "action_fingerprint": expired_ticket.action_fingerprint,
                                "context_snapshot": expired_ticket.context_snapshot,
                            },
                            sender="workflow.approval",
                        )
                    except Exception as exc:
                        logger.warning("Failed to publish expiry decision event for ticket '%s': %s", req.id, exc)

        return expired_results
