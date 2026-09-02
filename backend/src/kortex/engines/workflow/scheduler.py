"""
KORTEX Durable Workflow Scheduler & Time-Based Engine (Milestone M5.4).

Implements the ISchedulerProvider protocol backed by relational IDataStore persistence.
Supports 5-field Cron expressions, fixed intervals, and one-shot delayed executions.
Guarantees multi-tenant isolation, atomic schedule ticks, crash recovery, missed-run catchup,
idempotent trigger propagation, and audit lineage.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from datetime import UTC, timedelta
from typing import Any
from uuid import UUID, uuid4

from kortex.core.idempotency import sanitize_for_persistence
from kortex.core.outbox import OutboxStore
from kortex.engines.security.models import SecurityPrincipal, UniversalAuditEntry
from kortex.engines.storage.interfaces import IDataStore
from kortex.engines.workflow.cron import compute_next_cron_run, validate_cron_expression
from kortex.engines.workflow.exceptions import (
    ScheduleNotFoundError,
    WorkflowScheduleError,
    WorkflowValidationError,
)
from kortex.engines.workflow.interfaces import ISchedulerProvider
from kortex.engines.workflow.models import (
    ScheduleStatus,
    ScheduleType,
    WorkflowInstance,
    WorkflowSchedule,
)
from kortex.engines.workflow.persistence import SchedulerStore

logger = logging.getLogger("kortex.engines.workflow.scheduler")


class DurableWorkflowScheduler(ISchedulerProvider):
    """Production SQLite/PostgreSQL-backed workflow scheduler implementing ISchedulerProvider.

    Guarantees:
    - Durable persistence of schedule specifications and runtime trigger state.
    - Deterministic next-run calculations for Cron, Interval, and Once schedules.
    - Strict multi-tenant isolation.
    - Atomic schedule claiming to prevent duplicate triggers across concurrent workers.
    - Missed run recovery and crash hydration upon engine boot.
    - Transactional outbox domain event staging (workflow.schedule.*).
    - Immutable audit trails recorded via SecurityEngine.
    """

    def __init__(
        self,
        data_store: IDataStore,
        workflow_engine: Any = None,
        security_engine: Any = None,
        outbox_store: OutboxStore | None = None,
        event_engine: Any = None,
    ) -> None:
        self._data_store = data_store
        self._workflow_engine = workflow_engine
        self._security_engine = security_engine
        self._outbox_store = outbox_store
        self._event_engine = event_engine
        self._store = SchedulerStore(data_store)
        self._background_task: asyncio.Task[None] | None = None
        self._is_running = False
        logger.debug("DurableWorkflowScheduler initialized with IDataStore.")

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
                        actor_type="HUMAN" if actor_id not in ("SYSTEM", "SCHEDULER") else "SYSTEM_ENGINE",
                        tenant_id=tenant_id,
                        resource_id=resource_id,
                        context=sanitize_for_persistence(context or {}),
                    )
                    await audit_mgr.record_audit_entry(entry)
                except Exception as exc:
                    logger.error("Failed to record scheduler audit entry for '%s': %s", action, exc)

    def _compute_next_run(
        self,
        schedule_type: ScheduleType,
        cron_expression: str | None,
        interval_seconds: int | None,
        run_at: datetime.datetime | None,
        after_dt: datetime.datetime,
        timezone: str = "UTC",
    ) -> datetime.datetime | None:
        """Calculate the next execution UTC datetime based on schedule strategy.

        `timezone` (M5-A5) only affects CRON schedules — a cron expression's
        fields are a local wall-clock specification. INTERVAL is a fixed
        elapsed-time period and ONCE is a fixed absolute instant; neither is
        anchored to a particular timezone's wall clock, so both remain
        timezone-agnostic exactly as before.
        """
        base_dt = after_dt.replace(tzinfo=UTC) if after_dt.tzinfo is None else after_dt

        if schedule_type == ScheduleType.CRON:
            if not cron_expression:
                raise WorkflowValidationError("Cron expression is required for CRON schedule type.")
            validate_cron_expression(cron_expression)
            return compute_next_cron_run(cron_expression, after_dt=base_dt, timezone=timezone)

        elif schedule_type == ScheduleType.INTERVAL:
            if not interval_seconds or interval_seconds <= 0:
                raise WorkflowValidationError("interval_seconds must be positive integer for INTERVAL schedule type.")
            return base_dt + timedelta(seconds=interval_seconds)

        elif schedule_type == ScheduleType.ONCE:
            if run_at is not None:
                target_dt = run_at.replace(tzinfo=UTC) if run_at.tzinfo is None else run_at
                return target_dt
            return base_dt

        return None

    # -- ISchedulerProvider Protocol Methods ---------------------------------

    async def schedule_workflow(
        self,
        definition_name: str,
        cron_expression_or_delay_seconds: Any,
        initial_context: dict[str, Any] | None = None,
        tenant_id: str = "default",
        name: str | None = None,
        max_runs: int | None = None,
        timezone: str = "UTC",
        principal: SecurityPrincipal | None = None,
    ) -> str:
        """Schedule a workflow for recurring or delayed execution (ISchedulerProvider contract).

        Args:
            definition_name: Target workflow definition ID or name.
            cron_expression_or_delay_seconds: 5-field cron string, or integer seconds interval/delay.
            initial_context: Optional initial context dict.
            tenant_id: Tenant organization ID.
            name: Optional human-readable schedule name (auto-generated if omitted).
            max_runs: Maximum run count limit.
            timezone: Timezone string.
            principal: Authenticated caller security principal.

        Returns:
            Schedule UUID string.
        """
        sch_name = name or f"sched_{definition_name}_{uuid4().hex[:8]}"

        if isinstance(cron_expression_or_delay_seconds, int):
            # Interval in seconds
            stype = ScheduleType.INTERVAL
            interval_sec = cron_expression_or_delay_seconds
            cron_expr = None
            run_at = None
        elif isinstance(cron_expression_or_delay_seconds, str):
            # Check if string is integer or cron
            if cron_expression_or_delay_seconds.isdigit():
                stype = ScheduleType.INTERVAL
                interval_sec = int(cron_expression_or_delay_seconds)
                cron_expr = None
                run_at = None
            else:
                stype = ScheduleType.CRON
                cron_expr = cron_expression_or_delay_seconds
                interval_sec = None
                run_at = None
        elif isinstance(cron_expression_or_delay_seconds, datetime.datetime):
            stype = ScheduleType.ONCE
            run_at = cron_expression_or_delay_seconds
            cron_expr = None
            interval_sec = None
        else:
            raise WorkflowValidationError(f"Unsupported schedule specification: '{cron_expression_or_delay_seconds}'")

        schedule = await self.create_schedule(
            name=sch_name,
            definition_id=definition_name,
            schedule_type=stype,
            cron_expression=cron_expr,
            interval_seconds=interval_sec,
            run_at=run_at,
            initial_context=initial_context,
            max_runs=max_runs,
            timezone=timezone,
            tenant_id=tenant_id,
            principal=principal,
        )
        return str(schedule.id)

    async def cancel_scheduled_workflow(self, schedule_id: str, tenant_id: str = "default") -> bool:
        """Cancel an active scheduled workflow job (ISchedulerProvider contract)."""
        try:
            await self.cancel_schedule(schedule_id, tenant_id=tenant_id)
            return True
        except ScheduleNotFoundError:
            return False

    async def list_scheduled_workflows(self, tenant_id: str = "default") -> list[dict[str, Any]]:
        """List active scheduled workflow jobs as dictionary summaries (ISchedulerProvider contract)."""
        schedules = await self.list_schedules(tenant_id=tenant_id, status="ACTIVE")
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "definition_id": s.definition_id,
                "schedule_type": s.schedule_type.value,
                "cron_expression": s.cron_expression,
                "interval_seconds": s.interval_seconds,
                "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
                "status": s.status.value,
                "run_count": s.run_count,
            }
            for s in schedules
        ]

    # -- Primary Scheduling Management APIs -----------------------------------

    async def create_schedule(
        self,
        name: str,
        definition_id: str,
        schedule_type: ScheduleType | str,
        cron_expression: str | None = None,
        interval_seconds: int | None = None,
        run_at: datetime.datetime | None = None,
        initial_context: dict[str, Any] | None = None,
        max_runs: int | None = None,
        timezone: str = "UTC",
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
    ) -> WorkflowSchedule:
        """Create and persist a new workflow execution schedule."""
        tid = principal.tenant_id if principal is not None else tenant_id
        creator = principal.principal_id if principal is not None else "SYSTEM"

        stype = ScheduleType(schedule_type) if not isinstance(schedule_type, ScheduleType) else schedule_type
        now = datetime.datetime.now(UTC)

        next_run = self._compute_next_run(
            schedule_type=stype,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            run_at=run_at,
            after_dt=now,
            timezone=timezone,
        )

        schedule = WorkflowSchedule(
            id=uuid4(),
            tenant_id=tid,
            name=name,
            definition_id=definition_id,
            schedule_type=stype,
            cron_expression=cron_expression,
            interval_seconds=interval_seconds,
            run_at=run_at.replace(tzinfo=UTC) if run_at and run_at.tzinfo is None else run_at,
            next_run_at=next_run,
            last_run_at=None,
            last_instance_id=None,
            status=ScheduleStatus.ACTIVE,
            initial_context=sanitize_for_persistence(initial_context or {}),
            max_runs=max_runs,
            run_count=0,
            timezone=timezone,
            created_by=creator,
            created_at=now,
            updated_at=now,
        )

        await self._store.save_schedule(schedule, tenant_id=tid, outbox_store=self._outbox_store)

        await self._record_audit(
            action="kortex.workflow.schedule.create",
            actor_id=creator,
            tenant_id=tid,
            resource_id=str(schedule.id),
            context={
                "name": name,
                "definition_id": definition_id,
                "schedule_type": stype.value,
                "cron_expression": cron_expression,
                "interval_seconds": interval_seconds,
                "next_run_at": next_run.isoformat() if next_run else None,
            },
        )

        logger.info(
            "Created workflow schedule '%s' (%s) for definition '%s' (Next Run: %s, Tenant: '%s')",
            schedule.name,
            schedule.id,
            definition_id,
            next_run.isoformat() if next_run else "None",
            tid,
        )
        return schedule

    async def get_schedule(self, schedule_id: UUID | str, tenant_id: str | None = None) -> WorkflowSchedule | None:
        """Retrieve a schedule by ID with tenant isolation."""
        return await self._store.get_schedule(schedule_id, tenant_id=tenant_id)

    async def get_schedule_by_name(self, name: str, tenant_id: str = "default") -> WorkflowSchedule | None:
        """Retrieve a schedule by unique name within tenant."""
        return await self._store.get_schedule_by_name(name, tenant_id=tenant_id)

    async def list_schedules(
        self,
        tenant_id: str = "default",
        status: str | None = None,
    ) -> list[WorkflowSchedule]:
        """List schedules matching filters within tenant boundary."""
        return await self._store.list_schedules(tenant_id=tenant_id, status_filter=status)

    async def pause_schedule(
        self,
        schedule_id: UUID | str,
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
    ) -> WorkflowSchedule:
        """Pause an active schedule."""
        tid = principal.tenant_id if principal is not None else tenant_id
        actor = principal.principal_id if principal is not None else "SYSTEM"

        sch = await self._store.get_schedule(schedule_id, tenant_id=tid)
        if sch is None:
            raise ScheduleNotFoundError(f"Schedule '{schedule_id}' not found in tenant '{tid}'.")

        updated = await self._store.update_schedule_status(
            schedule_id=schedule_id,
            status=ScheduleStatus.PAUSED.value,
            tenant_id=tid,
            outbox_store=self._outbox_store,
        )
        if updated is None:
            raise ScheduleNotFoundError(f"Schedule '{schedule_id}' not found in tenant '{tid}'.")

        await self._record_audit(
            action="kortex.workflow.schedule.pause",
            actor_id=actor,
            tenant_id=tid,
            resource_id=str(schedule_id),
            context={"name": sch.name},
        )
        logger.info("Paused workflow schedule '%s' in tenant '%s'", sch.name, tid)
        return updated

    async def resume_schedule(
        self,
        schedule_id: UUID | str,
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
    ) -> WorkflowSchedule:
        """Resume a paused schedule and re-align its next run time."""
        tid = principal.tenant_id if principal is not None else tenant_id
        actor = principal.principal_id if principal is not None else "SYSTEM"

        sch = await self._store.get_schedule(schedule_id, tenant_id=tid)
        if sch is None:
            raise ScheduleNotFoundError(f"Schedule '{schedule_id}' not found in tenant '{tid}'.")

        now = datetime.datetime.now(UTC)
        next_run = self._compute_next_run(
            schedule_type=sch.schedule_type,
            cron_expression=sch.cron_expression,
            interval_seconds=sch.interval_seconds,
            run_at=sch.run_at,
            after_dt=now,
            timezone=sch.timezone,
        )

        sch.status = ScheduleStatus.ACTIVE
        sch.next_run_at = next_run
        await self._store.save_schedule(sch, tenant_id=tid, outbox_store=self._outbox_store)

        await self._record_audit(
            action="kortex.workflow.schedule.resume",
            actor_id=actor,
            tenant_id=tid,
            resource_id=str(schedule_id),
            context={"name": sch.name, "next_run_at": next_run.isoformat() if next_run else None},
        )
        logger.info("Resumed workflow schedule '%s' (Next Run: %s)", sch.name, next_run)
        return sch

    async def cancel_schedule(
        self,
        schedule_id: UUID | str,
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
    ) -> WorkflowSchedule:
        """Cancel (disable) a schedule."""
        tid = principal.tenant_id if principal is not None else tenant_id
        actor = principal.principal_id if principal is not None else "SYSTEM"

        sch = await self._store.get_schedule(schedule_id, tenant_id=tid)
        if sch is None:
            raise ScheduleNotFoundError(f"Schedule '{schedule_id}' not found in tenant '{tid}'.")

        updated = await self._store.update_schedule_status(
            schedule_id=schedule_id,
            status=ScheduleStatus.DISABLED.value,
            tenant_id=tid,
            outbox_store=self._outbox_store,
        )
        if updated is None:
            raise ScheduleNotFoundError(f"Schedule '{schedule_id}' not found in tenant '{tid}'.")

        await self._record_audit(
            action="kortex.workflow.schedule.cancel",
            actor_id=actor,
            tenant_id=tid,
            resource_id=str(schedule_id),
            context={"name": sch.name},
        )
        logger.info("Cancelled workflow schedule '%s' in tenant '%s'", sch.name, tid)
        return updated

    async def trigger_schedule(
        self,
        schedule_id: UUID | str,
        tenant_id: str = "default",
        principal: SecurityPrincipal | None = None,
    ) -> WorkflowInstance:
        """Immediately trigger a manual execution of a schedule out of band."""
        tid = principal.tenant_id if principal is not None else tenant_id
        actor = principal.principal_id if principal is not None else "SYSTEM"

        sch = await self._store.get_schedule(schedule_id, tenant_id=tid)
        if sch is None:
            raise ScheduleNotFoundError(f"Schedule '{schedule_id}' not found in tenant '{tid}'.")

        if self._workflow_engine is None:
            raise WorkflowScheduleError("WorkflowEngine is not attached to DurableWorkflowScheduler.")

        now = datetime.datetime.now(UTC)
        context_payload = dict(sch.initial_context)
        context_payload["_schedule_id"] = str(sch.id)
        context_payload["_schedule_name"] = sch.name
        context_payload["_triggered_by"] = actor

        instance = await self._workflow_engine.start_workflow(
            definition_id=sch.definition_id,
            initial_context=context_payload,
            tenant_id=tid,
        )

        new_count = sch.run_count + 1
        new_status = ScheduleStatus.COMPLETED.value if sch.max_runs and new_count >= sch.max_runs else sch.status.value
        next_run = (
            None
            if new_status == ScheduleStatus.COMPLETED.value
            else self._compute_next_run(
                sch.schedule_type,
                sch.cron_expression,
                sch.interval_seconds,
                sch.run_at,
                after_dt=now,
                timezone=sch.timezone,
            )
        )

        await self._store.record_schedule_tick(
            schedule_id=sch.id,
            last_run_at=now,
            next_run_at=next_run,
            last_instance_id=instance.id,
            run_count=new_count,
            new_status=new_status,
            tenant_id=tid,
            outbox_store=self._outbox_store,
        )

        await self._record_audit(
            action="kortex.workflow.schedule.manual_trigger",
            actor_id=actor,
            tenant_id=tid,
            resource_id=str(schedule_id),
            context={"name": sch.name, "instance_id": str(instance.id)},
        )

        logger.info(
            "Manually triggered schedule '%s' -> instance '%s' (Tenant: '%s')",
            sch.name,
            instance.id,
            tid,
        )
        return instance

    # -- Scheduler Engine Tick & Execution Dispatch --------------------------

    async def tick(
        self,
        now_dt: datetime.datetime | None = None,
        tenant_id: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowSchedule]:
        """Execute one scheduler tick cycle: find due schedules, trigger executions, and update next runs."""
        now = now_dt or datetime.datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        # `claim_due_schedules` (M5-A5) atomically transitions each returned
        # row ACTIVE -> TRIGGERING before returning it, so every schedule in
        # `due_schedules` is now exclusively ours to execute.
        due_schedules = await self._store.claim_due_schedules(before_time=now, tenant_id=tenant_id, limit=limit)
        triggered: list[WorkflowSchedule] = []

        for sch in due_schedules:
            updated = await self._execute_claimed_schedule(sch, now)
            if updated is not None:
                triggered.append(updated)

        return triggered

    async def _execute_claimed_schedule(self, sch: WorkflowSchedule, now: datetime.datetime) -> WorkflowSchedule | None:
        """Start the workflow instance for one already-claimed (TRIGGERING)
        due schedule and record the tick. Shared by `tick()` and
        `hydrate_and_recover_schedules()` (M5-A5) — the latter must drive
        the schedules it claims directly through this, never back through
        `tick()`'s own `claim_due_schedules` call, which would find nothing
        to claim (they are no longer `ACTIVE`) and silently do nothing."""
        tid = sch.tenant_id
        try:
            inst_id: UUID | None = None
            if self._workflow_engine is not None:
                context_payload = dict(sch.initial_context)
                context_payload["_schedule_id"] = str(sch.id)
                context_payload["_schedule_name"] = sch.name
                context_payload["_schedule_tick_at"] = now.isoformat()

                instance = await self._workflow_engine.start_workflow(
                    definition_id=sch.definition_id,
                    initial_context=context_payload,
                    tenant_id=tid,
                )
                inst_id = instance.id

            new_count = sch.run_count + 1
            if (sch.max_runs and new_count >= sch.max_runs) or sch.schedule_type == ScheduleType.ONCE:
                new_status = ScheduleStatus.COMPLETED.value
                next_run = None
            else:
                new_status = ScheduleStatus.ACTIVE.value
                next_run = self._compute_next_run(
                    sch.schedule_type,
                    sch.cron_expression,
                    sch.interval_seconds,
                    sch.run_at,
                    after_dt=now,
                    timezone=sch.timezone,
                )

            updated = await self._store.record_schedule_tick(
                schedule_id=sch.id,
                last_run_at=now,
                next_run_at=next_run,
                last_instance_id=inst_id,
                run_count=new_count,
                new_status=new_status,
                tenant_id=tid,
                outbox_store=self._outbox_store,
            )

            await self._record_audit(
                action="kortex.workflow.schedule.tick",
                actor_id="SCHEDULER",
                tenant_id=tid,
                resource_id=str(sch.id),
                context={
                    "name": sch.name,
                    "instance_id": str(inst_id) if inst_id else None,
                    "run_count": new_count,
                    "next_run_at": next_run.isoformat() if next_run else None,
                },
            )

            logger.info(
                "Scheduled tick triggered '%s' -> instance '%s' (Run: %d, Next: %s)",
                sch.name,
                inst_id,
                new_count,
                next_run.isoformat() if next_run else "None",
            )
            return updated

        except Exception as exc:
            logger.error("Failed to execute schedule tick for '%s': %s", sch.name, exc, exc_info=True)
            return None

    # -- Hydration, Crash Recovery & Catch-Up Policy --------------------------

    def _estimate_missed_occurrences(self, sch: WorkflowSchedule, now: datetime.datetime) -> int:
        """Best-effort count of how many scheduled fires were skipped between
        this schedule's last recorded due time and `now` (M5-A5).

        Recovery still executes exactly one catch-up run regardless of this
        count — replaying every missed fire after a long outage could mean
        starting an unbounded flood of workflow instances, which is its own
        hazard. What this fixes is the previous behavior of dropping every
        occurrence but one with no record of it happening at all: the count
        computed here is persisted to the audit trail below, so "62 payroll
        runs were coalesced into 1 catch-up run at date X" is answerable
        after the fact instead of silently invisible.
        """
        if sch.next_run_at is None:
            return 1
        original_due = sch.next_run_at.replace(tzinfo=UTC) if sch.next_run_at.tzinfo is None else sch.next_run_at
        if sch.schedule_type == ScheduleType.INTERVAL and sch.interval_seconds:
            elapsed = (now - original_due).total_seconds()
            return max(1, int(elapsed // sch.interval_seconds) + 1)
        if sch.schedule_type == ScheduleType.ONCE:
            return 1
        if sch.schedule_type == ScheduleType.CRON and sch.cron_expression:
            count = 0
            cursor = original_due
            try:
                while cursor <= now and count < 10_000:
                    count += 1
                    cursor = compute_next_cron_run(sch.cron_expression, after_dt=cursor, timezone=sch.timezone)
            except Exception as exc:
                logger.debug("Could not fully count missed cron occurrences for '%s': %s", sch.name, exc)
            return max(1, count)
        return 1

    async def hydrate_and_recover_schedules(self, tenant_id: str | None = None) -> list[WorkflowSchedule]:
        """Recover active schedules upon engine boot and evaluate missed execution windows."""
        now = datetime.datetime.now(UTC)
        logger.info("Hydrating and recovering durable workflow schedules (Tenant: %s)...", tenant_id or "ALL")

        # Query all active schedules that are overdue. Like `tick()`, this
        # atomically claims them (ACTIVE -> TRIGGERING) — they must be
        # executed directly via `_execute_claimed_schedule` below, NOT by
        # calling `self.tick()` (M5-A5), which would try to claim the same
        # rows again, find them no longer `ACTIVE`, and do nothing.
        overdue = await self._store.claim_due_schedules(before_time=now, tenant_id=tenant_id, limit=200)
        recovered: list[WorkflowSchedule] = []

        for sch in overdue:
            missed = self._estimate_missed_occurrences(sch, now)
            logger.warning(
                "Schedule '%s' missed scheduled run at '%s' while offline (~%d occurrence(s) "
                "coalesced into one catch-up run). Executing catch-up run...",
                sch.name,
                sch.next_run_at.isoformat() if sch.next_run_at else "unknown",
                missed,
            )
            await self._record_audit(
                action="kortex.workflow.schedule.recovery_catchup",
                actor_id="SCHEDULER",
                tenant_id=sch.tenant_id,
                resource_id=str(sch.id),
                context={
                    "name": sch.name,
                    "missed_occurrences": missed,
                    "originally_due_at": sch.next_run_at.isoformat() if sch.next_run_at else None,
                    "recovered_at": now.isoformat(),
                },
            )
            updated = await self._execute_claimed_schedule(sch, now)
            if updated is not None:
                recovered.append(updated)

        return recovered

    # -- Background Polling Worker Daemon ------------------------------------

    def start_background_loop(self, poll_interval_seconds: float = 1.0) -> None:
        """Start the background scheduler polling task."""
        if self._background_task is not None and not self._background_task.done():
            return
        self._is_running = True
        self._background_task = asyncio.create_task(self._poll_loop(poll_interval_seconds))
        logger.info("Durable workflow scheduler background daemon started (poll: %.2fs).", poll_interval_seconds)

    def stop_background_loop(self) -> None:
        """Stop the background scheduler polling task."""
        self._is_running = False
        if self._background_task is not None:
            self._background_task.cancel()
            self._background_task = None
            logger.info("Durable workflow scheduler background daemon stopped.")

    async def _poll_loop(self, poll_interval: float) -> None:
        """Continuous background tick loop."""
        while self._is_running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in scheduler daemon loop: %s", exc)
            await asyncio.sleep(poll_interval)
