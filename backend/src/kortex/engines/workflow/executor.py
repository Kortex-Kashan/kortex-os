"""
KORTEX Governed External Execution Subsystem (Milestone M5.4).

Provides safe, audited, idempotent execution of external operations (capabilities, connectors,
HTTP integrations) with configurable timeouts, retries with exponential backoff, pre-execution
human approval checkpoints, and full transactional outbox lineage.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import random
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from kortex.core.dispatch import CapabilityRequest
from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.outbox import OutboxStore
from kortex.engines.security.models import SecurityPrincipal, TokenPayload, UniversalAuditEntry
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.workflow.approval import DurableApprovalManager
from kortex.engines.workflow.exceptions import (
    ExternalExecutionError,
    ExternalExecutionTimeoutError,
)
from kortex.engines.workflow.models import (
    ExternalExecutionRecord,
    ExternalExecutionRequest,
    ExternalExecutionStatus,
    RetryPolicy,
)
from kortex.engines.workflow.persistence import ExternalExecutionStore

logger = logging.getLogger("kortex.engines.workflow.executor")


class ExternalExecutionManager:
    """Governed external execution manager enforcing capability-based safety, timeouts, and approvals.

    Guarantees:
    - Pre-execution human approval gating when `requires_approval=True`.
    - CapabilityDispatcher invocation envelope propagation (`request_id`, `correlation_id`, `idempotency_key`).
    - Configurable execution timeouts with strict asynchronous boundaries.
    - Automatic exponential backoff retries with optional jitter.
    - Strict multi-tenant data isolation.
    - Transactional outbox event staging (`workflow.external.*`).
    - Immutable audit lineage via SecurityEngine.
    - Secret sanitization on parameters and outputs.
    """

    def __init__(
        self,
        data_store: IDataStore,
        kernel: Any = None,
        approval_manager: DurableApprovalManager | Any = None,
        security_engine: Any = None,
        outbox_store: OutboxStore | None = None,
    ) -> None:
        self._data_store = data_store
        self._kernel = kernel
        self._approval_manager = approval_manager
        self._security_engine = security_engine
        self._outbox_store = outbox_store
        self._store = ExternalExecutionStore(data_store)
        logger.debug("ExternalExecutionManager initialized with IDataStore.")

    # -- Internal Helpers ----------------------------------------------------

    async def _record_audit(
        self,
        action: str,
        actor_id: str,
        tenant_id: str,
        resource_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Record an immutable UniversalAuditEntry via SecurityEngine."""
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
                        actor_type="HUMAN" if actor_id not in ("SYSTEM", "EXECUTOR") else "SYSTEM_ENGINE",
                        tenant_id=tenant_id,
                        resource_id=resource_id,
                        context=sanitize_for_persistence(context or {}),
                    )
                    await audit_mgr.record_audit_entry(entry)
                except Exception as exc:
                    logger.error("Failed to record external execution audit entry for '%s': %s", action, exc)

    # -- Primary External Execution APIs --------------------------------------

    @staticmethod
    def _compute_action_fingerprint(target: str, operation_type: str, parameters: dict[str, Any]) -> str:
        """Stable hash binding an approval decision to the exact operation it
        gates (M6.3-3) -- re-verified before a durable APPROVED decision is
        ever allowed to resume execution, mirroring the identical pattern
        already used for the AI's own tool-invocation approvals
        (`AIOrchestrationEngine._action_fingerprint`, M6.2)."""
        payload = json.dumps(
            {
                "target": target,
                "operation_type": operation_type,
                "parameters": sanitize_for_persistence(parameters),
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def execute_operation(
        self,
        request: ExternalExecutionRequest,
        principal: SecurityPrincipal | None = None,
        session_token: TokenPayload | dict[str, Any] | None = None,
    ) -> ExternalExecutionRecord:
        """Execute a governed external operation with safety guards, timeouts, and optional approvals."""
        tid = principal.tenant_id if principal is not None else request.tenant_id
        actor = principal.principal_id if principal is not None else request.created_by
        now = datetime.now(UTC)
        policy_json = json.dumps(request.retry_policy.model_dump()) if request.retry_policy else None

        # 0. Idempotency Replay Guard (M6.3-4): a retried request carrying the
        # same caller-supplied idempotency key returns the ORIGINAL outcome
        # instead of executing (or re-queuing for approval) a second time.
        # Checked before the approval gate too -- a duplicate of an
        # already-waiting-for-approval request must not create a second
        # ticket. This is a lookup-before-create fast path; the DB-level
        # `UniqueConstraint` on `(tenant_id, idempotency_key)` is the actual
        # enforcement backstop against a genuine race between two concurrent
        # first-time requests sharing a key.
        if request.idempotency_key is not None:
            existing = await self._store.get_execution_by_idempotency_key(tid, request.idempotency_key)
            if existing is not None:
                logger.info(
                    "Idempotent replay: returning existing execution '%s' for key '%s' (Tenant: '%s') "
                    "instead of re-executing.",
                    existing.id,
                    request.idempotency_key,
                    tid,
                )
                return existing

        # 1. Human Approval Governance Gate
        if request.requires_approval:
            approval_ticket_id: UUID | None = None
            if self._approval_manager is not None:
                action_fingerprint = self._compute_action_fingerprint(
                    request.target, request.operation_type, request.parameters
                )
                appr_ticket = await self._approval_manager.create_request(
                    required_role=request.required_approval_role or "",
                    tenant_id=tid,
                    timeout_seconds=request.approval_timeout_seconds,
                    context_snapshot={
                        "action": "external_execution",
                        "execution_id": str(request.id),
                        "target": request.target,
                    },
                    principal=principal,
                    correlation_id=request.correlation_id,
                    action_fingerprint=action_fingerprint,
                )
                approval_ticket_id = appr_ticket.id

            record = ExternalExecutionRecord(
                id=request.id,
                request_id=request.id,
                tenant_id=tid,
                operation_type=request.operation_type,
                target=request.target,
                status=ExternalExecutionStatus.WAITING_APPROVAL,
                idempotency_key=request.idempotency_key,
                correlation_id=request.correlation_id,
                approval_request_id=approval_ticket_id,
                created_by=actor,
                created_at=now,
                updated_at=now,
            )

            await self._store.save_execution(
                record=record,
                parameters=request.parameters,
                tenant_id=tid,
                outbox_store=self._outbox_store,
                timeout_seconds=request.timeout_seconds,
                retry_policy_json=policy_json,
            )

            await self._record_audit(
                action="kortex.workflow.external.waiting_approval",
                actor_id=actor,
                tenant_id=tid,
                resource_id=str(record.id),
                context={
                    "target": request.target,
                    "approval_request_id": str(approval_ticket_id) if approval_ticket_id else None,
                    "required_role": request.required_approval_role,
                },
            )

            logger.info(
                "External execution '%s' paused for human approval (Ticket: %s, Tenant: '%s')",
                record.id,
                approval_ticket_id,
                tid,
            )
            return record

        # 2. No approval gate: dispatch immediately under this same execution id.
        return await self._run_dispatch_and_record(
            execution_id=request.id,
            tenant_id=tid,
            actor=actor,
            target=request.target,
            operation_type=request.operation_type,
            parameters=request.parameters,
            timeout_seconds=request.timeout_seconds,
            retry_policy=request.retry_policy,
            retry_policy_json=policy_json,
            idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id,
            session_token=session_token,
        )

    async def _run_dispatch_and_record(
        self,
        execution_id: UUID,
        tenant_id: str,
        actor: str,
        target: str,
        operation_type: str,
        parameters: dict[str, Any],
        timeout_seconds: float,
        retry_policy: RetryPolicy | None,
        retry_policy_json: str | None,
        idempotency_key: str | None,
        correlation_id: str | None,
        session_token: TokenPayload | dict[str, Any] | None = None,
    ) -> ExternalExecutionRecord:
        """Persist a RUNNING record and run the retry/timeout/dispatch loop.

        Reused by both `execute_operation`'s immediate (no-approval) path and
        the resume-after-approval path (M6.3-3) -- both write to the SAME
        `execution_id`, so a resumed execution updates its own original
        record in place (WAITING_APPROVAL -> RUNNING -> terminal) rather than
        creating an orphaned second row.
        """
        now = datetime.now(UTC)
        record = ExternalExecutionRecord(
            id=execution_id,
            request_id=execution_id,
            tenant_id=tenant_id,
            operation_type=operation_type,
            target=target,
            status=ExternalExecutionStatus.RUNNING,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            approval_request_id=None,
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        await self._store.save_execution(
            record=record,
            parameters=parameters,
            tenant_id=tenant_id,
            outbox_store=self._outbox_store,
            timeout_seconds=timeout_seconds,
            retry_policy_json=retry_policy_json,
        )

        # 3. Execution Dispatch with Retries and Timeout
        policy = retry_policy or RetryPolicy(max_attempts=1)
        max_attempts = max(1, policy.max_attempts)
        delay = policy.initial_delay_seconds
        last_error: Exception | None = None
        start_time = time.monotonic()

        # Parse TokenPayload if dict was provided
        tok_payload: TokenPayload | None = None
        if isinstance(session_token, TokenPayload):
            tok_payload = session_token
        elif isinstance(session_token, dict):
            try:
                tok_payload = TokenPayload(**session_token)
            except Exception:
                tok_payload = None

        # M5-A5: a timeout is folded into the exact same attempt-counted
        # retry path as any other failure below, instead of being treated
        # as unconditionally terminal on the very first occurrence. A
        # transient network timeout is the single most common transient
        # failure mode for an external call — permanently failing on attempt
        # 1 regardless of a caller-configured `max_attempts` defeated the
        # retry policy's entire purpose for exactly the failure it exists to
        # absorb. `last_was_timeout` remembers whether the *final* attempt
        # (the one actually reported) was a timeout, so the terminal status/
        # error type distinction (TIMED_OUT vs FAILED) is preserved.
        last_was_timeout = False

        for attempt in range(1, max_attempts + 1):
            try:
                # Enforce execution timeout
                async with asyncio.timeout(timeout_seconds):
                    result_output = await self._dispatch_target(
                        target=target,
                        parameters=parameters,
                        tenant_id=tenant_id,
                        session_token=tok_payload,
                        idempotency_key=idempotency_key,
                        correlation_id=correlation_id,
                    )

                # Success
                duration_ms = (time.monotonic() - start_time) * 1000
                completed_at = datetime.now(UTC)
                updated_record = await self._store.update_execution_status(
                    execution_id=record.id,
                    status=ExternalExecutionStatus.COMPLETED.value,
                    tenant_id=tenant_id,
                    output=result_output,
                    attempts=attempt,
                    execution_time_ms=duration_ms,
                    completed_at=completed_at,
                    outbox_store=self._outbox_store,
                )

                await self._record_audit(
                    action="kortex.workflow.external.completed",
                    actor_id=actor,
                    tenant_id=tenant_id,
                    resource_id=str(record.id),
                    context={
                        "target": target,
                        "attempts": attempt,
                        "duration_ms": duration_ms,
                        "status": "COMPLETED",
                    },
                )

                logger.info(
                    "External operation '%s' completed successfully in %.2fms (Attempt: %d/%d)",
                    target,
                    duration_ms,
                    attempt,
                    max_attempts,
                )
                return updated_record or record

            except TimeoutError as exc:
                last_error = exc
                last_was_timeout = True
                logger.warning(
                    "External operation '%s' timed out on attempt %d/%d (limit %.2fs)",
                    target,
                    attempt,
                    max_attempts,
                    timeout_seconds,
                )
                if attempt < max_attempts:
                    jitter = random.uniform(0.8, 1.2) if policy.jitter else 1.0  # noqa: S311
                    sleep_time = max(0.01, delay * jitter)
                    await asyncio.sleep(sleep_time)
                    delay *= policy.backoff_factor

            except Exception as exc:
                last_error = exc
                last_was_timeout = False
                logger.warning(
                    "External operation '%s' failed on attempt %d/%d: %s",
                    target,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt < max_attempts:
                    jitter = random.uniform(0.8, 1.2) if policy.jitter else 1.0  # noqa: S311
                    sleep_time = max(0.01, delay * jitter)
                    await asyncio.sleep(sleep_time)
                    delay *= policy.backoff_factor

        # All attempts failed (or every attempt timed out)
        duration_ms = (time.monotonic() - start_time) * 1000
        completed_at = datetime.now(UTC)

        if last_was_timeout:
            err_msg = f"Execution timed out after {timeout_seconds:.2f}s"
            await self._store.update_execution_status(
                execution_id=record.id,
                status=ExternalExecutionStatus.TIMED_OUT.value,
                tenant_id=tenant_id,
                error=err_msg,
                attempts=max_attempts,
                execution_time_ms=duration_ms,
                completed_at=completed_at,
                outbox_store=self._outbox_store,
            )
            await self._record_audit(
                action="kortex.workflow.external.timed_out",
                actor_id=actor,
                tenant_id=tenant_id,
                resource_id=str(record.id),
                context={
                    "target": target,
                    "timeout_seconds": timeout_seconds,
                    "attempts": max_attempts,
                },
            )
            raise ExternalExecutionTimeoutError(
                f"External operation '{target}' timed out after {max_attempts} attempt(s), "
                f"each bounded at {timeout_seconds:.2f} seconds."
            ) from last_error

        err_msg = str(last_error) if last_error else "Unknown execution error"

        await self._store.update_execution_status(
            execution_id=record.id,
            status=ExternalExecutionStatus.FAILED.value,
            tenant_id=tenant_id,
            error=err_msg,
            attempts=max_attempts,
            execution_time_ms=duration_ms,
            completed_at=completed_at,
            outbox_store=self._outbox_store,
        )

        await self._record_audit(
            action="kortex.workflow.external.failed",
            actor_id=actor,
            tenant_id=tenant_id,
            resource_id=str(record.id),
            context={
                "target": target,
                "error": err_msg[:500],
                "attempts": max_attempts,
            },
        )

        raise ExternalExecutionError(
            f"External operation '{target}' failed after {max_attempts} attempts: {err_msg}"
        ) from last_error

    async def _dispatch_target(
        self,
        target: str,
        parameters: dict[str, Any],
        tenant_id: str,
        session_token: TokenPayload | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """Dispatch target operation through Kernel capability boundary or registered engine.

        M6.0-4: this previously had a third branch that fabricated
        `{"status": "SUCCESS", "target": target}` for any target that wasn't
        a test-only `_handler` callable and didn't pass an existence guard —
        that guard called `has_capability` on the registry engine, a method
        that does not exist anywhere in this codebase, so it always evaluated
        false and the "real capability dispatch" branch below was dead code.
        In practice, every non-`_handler` target silently reported success
        with zero I/O. The fix is to always attempt real dispatch through the
        Kernel once a kernel is bound, and let `Kernel.invoke_capability`'s
        own `CapabilityNotFoundError` (already caught and retried like any
        other failure by this method's caller) surface an unresolvable
        target as a real failure instead of a fabricated success.
        """
        # 1. Direct handler callable in parameters (useful for local integrations or testing)
        if "_handler" in parameters and callable(parameters["_handler"]):
            fn = parameters["_handler"]
            call_kwargs = {k: v for k, v in parameters.items() if k != "_handler"}
            try:
                res = fn(**call_kwargs)
            except TypeError:
                res = fn()
            if asyncio.iscoroutine(res):
                return await res
            return res

        # 2. Kernel capability dispatch boundary — always attempted; an
        # unresolvable target raises `CapabilityNotFoundError` rather than
        # being silently treated as success.
        if self._kernel is not None:
            cap_req = CapabilityRequest(
                capability_name=target,
                session_token=session_token,
                parameters=parameters,
                context={"resource_tenant_id": tenant_id},
                idempotency_key=idempotency_key,
                correlation_id=correlation_id or str(uuid4()),
            )
            return await self._kernel.invoke_capability(cap_req)

        raise ExternalExecutionError(
            f"Cannot dispatch external execution target '{target}': no Kernel is bound to resolve it."
        )

    async def get_execution(
        self, execution_id: UUID | str, tenant_id: str | None = None
    ) -> ExternalExecutionRecord | None:
        """Retrieve an external execution record by ID with tenant isolation."""
        return await self._store.get_execution(execution_id, tenant_id=tenant_id)

    async def list_executions(
        self,
        tenant_id: str = "default",
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExternalExecutionRecord]:
        """List external executions within tenant boundary."""
        return await self._store.list_executions(tenant_id=tenant_id, status_filter=status, limit=limit)

    async def cancel_execution(
        self,
        execution_id: UUID | str,
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
        reason: str | None = None,
    ) -> ExternalExecutionRecord:
        """Cancel a pending or waiting external execution.

        `reason` (M6.4-3), when supplied, is recorded in the cancellation
        audit context so the trail distinguishes WHY the execution was
        cancelled (e.g. an approval ticket's actual `"REJECTED"` vs
        `"EXPIRED"` decision) without requiring a join back to the
        approval-ticket table to find out. Optional and defaults to `None`
        for the pre-existing, directly-caller-invoked cancellation path
        (`kortex.workflow.external.cancel`), which has no such decision to
        report.
        """
        tid = principal.tenant_id if principal is not None else tenant_id
        actor = principal.principal_id if principal is not None else "SYSTEM"

        rec = await self._store.get_execution(execution_id, tenant_id=tid)
        if rec is None:
            raise ExternalExecutionError(f"External execution '{execution_id}' not found in tenant '{tid}'.")

        if rec.status not in (ExternalExecutionStatus.PENDING, ExternalExecutionStatus.WAITING_APPROVAL):
            raise ExternalExecutionError(
                f"Cannot cancel execution '{execution_id}' in terminal or running state '{rec.status.value}'."
            )

        updated = await self._store.update_execution_status(
            execution_id=execution_id,
            status=ExternalExecutionStatus.CANCELLED.value,
            tenant_id=tid,
            completed_at=datetime.now(UTC),
            outbox_store=self._outbox_store,
        )
        if updated is None:
            raise ExternalExecutionError(f"Failed to cancel execution '{execution_id}'.")

        await self._record_audit(
            action="kortex.workflow.external.cancelled",
            actor_id=actor,
            tenant_id=tid,
            resource_id=str(execution_id),
            context={"target": rec.target, "reason": reason},
        )

        logger.info("Cancelled external execution '%s' in tenant '%s'", execution_id, tid)
        return updated

    # -- Boot Recovery (M6.3-4) ------------------------------------------------

    async def recover_stranded_executions(self, tenant_id: str | None = None) -> list[ExternalExecutionRecord]:
        """Reconcile external executions stranded in RUNNING by a process crash/restart.

        Mirrors `WorkflowEngine.hydrate_and_recover`'s startup recovery scan.
        A RUNNING row was interrupted mid-dispatch -- whether its external
        side effect actually landed before the interruption is unknown, so
        it is never blindly auto-resumed (that could duplicate a real-world
        action against the external system). Instead it is deterministically
        failed closed, surfacing a clear, actionable error; a caller that
        needs the operation to genuinely complete can safely resubmit using
        the same `idempotency_key`, which either replays the stranded
        record's own now-terminal outcome or -- if the underlying system
        never actually received the call -- executes it for the first time.

        WAITING_APPROVAL rows are intentionally left untouched HERE: nothing
        dispatches them until a real `workflow.approval.decided` event
        arrives, and the ticket itself is independently durable. They get
        their own, separate reconciliation pass --
        `reconcile_stranded_waiting_approvals` (M6.4-4) -- since the
        question for a WAITING_APPROVAL row is different (did its already-
        durable ticket resolve to a terminal state whose event never
        arrived?), not "was a side effect possibly already in flight?".
        """
        stranded = await self._store.get_stranded_executions(tenant_id=tenant_id)
        recovered: list[ExternalExecutionRecord] = []
        for rec in stranded:
            updated = await self._store.update_execution_status(
                execution_id=rec.id,
                status=ExternalExecutionStatus.FAILED.value,
                tenant_id=rec.tenant_id,
                error=(
                    "Execution was interrupted by a process restart while RUNNING and was not "
                    "automatically resumed to avoid a duplicate side effect on the external system. "
                    "Resubmit with the same idempotency_key to safely retry."
                ),
                attempts=rec.attempts,
                execution_time_ms=rec.execution_time_ms,
                completed_at=datetime.now(UTC),
                outbox_store=self._outbox_store,
            )
            if updated is not None:
                recovered.append(updated)
                await self._record_audit(
                    action="kortex.workflow.external.recovery_failed",
                    actor_id="SYSTEM",
                    tenant_id=rec.tenant_id,
                    resource_id=str(rec.id),
                    context={"target": rec.target, "reason": "stranded_running_on_boot"},
                )
                logger.warning(
                    "Reconciled stranded RUNNING external execution '%s' (Tenant: '%s') to FAILED on boot recovery.",
                    rec.id,
                    rec.tenant_id,
                )
        return recovered

    async def reconcile_stranded_waiting_approvals(self, tenant_id: str | None = None) -> list[ExternalExecutionRecord]:
        """Reconcile WAITING_APPROVAL executions whose ticket already
        resolved while the event that should have propagated it never
        arrived (M6.4-4).

        The Event Engine's `publish` is a direct, synchronous, in-process
        call with no retry and no outbox/queue backing it -- if the process
        crashes between an approval ticket's DB transition (APPROVED/
        REJECTED/EXPIRED) and `on_approval_decided` actually running, or if
        the event fires but the handler never completes before the crash,
        the execution is left parked in WAITING_APPROVAL even though its
        ticket has already reached a real terminal state.

        Fails closed on every ambiguity, exactly like `on_approval_decided`
        itself:
        - Ticket missing entirely: cancelled (reason="ticket_missing") --
          nothing to safely wait on.
        - Ticket REJECTED or EXPIRED: cancelled (reason=ticket.state) --
          identical outcome `on_approval_decided` would have produced had
          the event actually arrived.
        - Ticket still PENDING: left untouched -- genuinely still waiting,
          nothing wrong.
        - Ticket APPROVED: deliberately NEVER auto-resumed here, even
          though that would be a "safe" resume in isolation (nothing was
          ever dispatched) -- boot-time recovery is not the place to
          re-introduce an automatic resume decision. Logged for operator
          visibility; the ticket and execution both remain inspectable and
          resolvable (a fresh, real `workflow.approval.decided` redelivery
          -- e.g. an operator re-submitting the decision -- still works,
          since `on_approval_decided`'s own idempotency guard only blocks a
          *second* delivery after the execution has actually left
          WAITING_APPROVAL).
        """
        waiting = await self._store.get_waiting_approval_executions(tenant_id=tenant_id)
        reconciled: list[ExternalExecutionRecord] = []
        for rec in waiting:
            if rec.approval_request_id is None:
                logger.error(
                    "Execution '%s' (Tenant: '%s') is WAITING_APPROVAL with no approval_request_id -- "
                    "malformed state, cancelling rather than guessing.",
                    rec.id,
                    rec.tenant_id,
                )
                cancelled = await self._safe_cancel_for_reconciliation(rec, "malformed_context")
                if cancelled is not None:
                    reconciled.append(cancelled)
                continue

            ticket = None
            if self._approval_manager is not None:
                try:
                    ticket = await self._approval_manager.get_request(rec.approval_request_id, tenant_id=rec.tenant_id)
                except Exception as exc:
                    logger.error(
                        "Failed to look up approval ticket '%s' for execution '%s' during boot reconciliation: %s",
                        rec.approval_request_id,
                        rec.id,
                        exc,
                    )

            if ticket is None:
                cancelled = await self._safe_cancel_for_reconciliation(rec, "ticket_missing")
                if cancelled is not None:
                    reconciled.append(cancelled)
                continue

            ticket_state = ticket.state.value if hasattr(ticket.state, "value") else str(ticket.state)
            if ticket_state in ("REJECTED", "EXPIRED"):
                cancelled = await self._safe_cancel_for_reconciliation(rec, ticket_state)
                if cancelled is not None:
                    reconciled.append(cancelled)
            elif ticket_state == "APPROVED":
                logger.warning(
                    "Execution '%s' (Tenant: '%s') is WAITING_APPROVAL but its ticket '%s' is already "
                    "APPROVED -- the resume event was likely lost to a crash. Not auto-resuming on boot; "
                    "resolve manually (e.g. re-submit the decision) or investigate.",
                    rec.id,
                    rec.tenant_id,
                    rec.approval_request_id,
                )
            # PENDING: genuinely still waiting -- nothing to reconcile.
        return reconciled

    async def _safe_cancel_for_reconciliation(
        self, rec: ExternalExecutionRecord, reason: str
    ) -> ExternalExecutionRecord | None:
        """Best-effort `cancel_execution` wrapper for the reconciliation scan
        above -- a single execution's cancellation failing (e.g. it was
        concurrently resumed/cancelled by a real, late-arriving event
        between the scan's read and this call) must not abort the rest of
        the boot-time scan."""
        try:
            return await self.cancel_execution(rec.id, tenant_id=rec.tenant_id, reason=reason)
        except Exception as exc:
            logger.warning(
                "Failed to reconcile stranded WAITING_APPROVAL execution '%s' (Tenant: '%s'): %s",
                rec.id,
                rec.tenant_id,
                exc,
            )
            return None

    # -- Approval Resume Event Handler (M6.3-3) --------------------------------

    async def on_approval_decided(self, event: Any) -> None:
        """React to a durable approval decision for an external-execution ticket.

        Subscribed to the generic `workflow.approval.decided` event (published
        unconditionally by `WorkflowEngine.decide_approval_request`), mirroring
        `AIOrchestrationEngine._on_approval_decided` (M6.2-4). Filters on the
        ticket's own `context_snapshot["action"] == "external_execution"`
        marker so every other (human workflow-step / AI tool-invocation)
        decision is ignored.

        Fails closed on every ambiguity: execution not found, wrong status
        (already resumed/cancelled by a prior delivery -- idempotent no-op),
        missing dispatch context, or a mismatched action fingerprint all
        result in the paused execution being left alone or cancelled --
        never resumed on uncertain grounds.
        """
        try:
            payload = getattr(event, "payload", None)
            if not isinstance(payload, dict):
                return
            context_snapshot = payload.get("context_snapshot")
            if not isinstance(context_snapshot, dict) or context_snapshot.get("action") != "external_execution":
                return

            execution_id = context_snapshot.get("execution_id")
            tid = payload.get("tenant_id")
            decision = payload.get("decision")
            if not execution_id or not tid:
                return

            rec = await self._store.get_execution(execution_id, tenant_id=tid)
            if rec is None or rec.status != ExternalExecutionStatus.WAITING_APPROVAL:
                # Already resumed/cancelled by a prior delivery of this
                # event, or the execution never reached this pause state --
                # idempotent no-op either way.
                return

            if decision != "APPROVED":
                # REJECTED (or any other terminal, non-approved decision,
                # e.g. EXPIRED): the paused execution must never dispatch
                # the operation it was paused on. `decision` (M6.4-3) is
                # recorded in the cancellation audit context so the trail
                # distinguishes a human REJECTED from a timed-out EXPIRED.
                await self.cancel_execution(execution_id, tenant_id=tid, reason=decision)
                return

            ctx = await self._store.get_dispatch_context(execution_id, tenant_id=tid)
            if ctx is None:
                logger.error(
                    "Refusing to resume external execution '%s': dispatch context missing.",
                    execution_id,
                )
                await self.cancel_execution(execution_id, tenant_id=tid, reason="dispatch_context_missing")
                return

            stored_fingerprint = payload.get("action_fingerprint")
            actual_fingerprint = self._compute_action_fingerprint(
                ctx["target"], ctx["operation_type"], ctx["parameters"]
            )
            if stored_fingerprint and stored_fingerprint != actual_fingerprint:
                logger.error(
                    "Refusing to resume external execution '%s': approved action fingerprint does not "
                    "match the execution's current dispatch context (approve-one/execute-another "
                    "attempt or stale approval).",
                    execution_id,
                )
                await self.cancel_execution(execution_id, tenant_id=tid, reason="fingerprint_mismatch")
                return

            retry_policy = RetryPolicy(**ctx["retry_policy"]) if ctx["retry_policy"] else None
            retry_policy_json = json.dumps(ctx["retry_policy"]) if ctx["retry_policy"] else None
            session_token = payload.get("decider_session_token")

            # Already recorded as FAILED/TIMED_OUT with a full audit trail by
            # `_run_dispatch_and_record` itself -- the event handler must not
            # propagate the exception back to the Event Engine's synchronous
            # dispatch loop.
            with contextlib.suppress(ExternalExecutionError, ExternalExecutionTimeoutError):
                await self._run_dispatch_and_record(
                    execution_id=UUID(str(execution_id)),
                    tenant_id=tid,
                    actor=ctx["created_by"],
                    target=ctx["target"],
                    operation_type=ctx["operation_type"],
                    parameters=ctx["parameters"],
                    timeout_seconds=ctx["timeout_seconds"],
                    retry_policy=retry_policy,
                    retry_policy_json=retry_policy_json,
                    idempotency_key=ctx["idempotency_key"],
                    correlation_id=ctx["correlation_id"],
                    session_token=session_token,
                )
        except Exception as exc:
            logger.error("Failed to process approval decision event for external execution: %s", exc, exc_info=True)
