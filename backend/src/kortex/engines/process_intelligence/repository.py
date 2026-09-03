"""
KORTEX Process Intelligence Tenant-Scoped Analytics Repository.

Implements read-only database query projections over IDataStore for Workflow
instances, step executions, and human approval tickets. Structurally enforces
tenant isolation by capturing the authoritative tenant_id at construction.
"""

from __future__ import annotations

import asyncio
import datetime
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import and_, desc, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.process_intelligence.exceptions import (
    InvalidTimeRangeError,
    ProcessAnalyticsTimeoutError,
)
from kortex.engines.process_intelligence.interfaces import (
    IProcessAnalyticsRepository,
    IProcessAnalyzer,
)
from kortex.engines.process_intelligence.models import (
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_FAILED,
    BottlenecksResult,
    ProcessSummaryKPIs,
    RawInstanceTrace,
    RawStepExecution,
)
from kortex.engines.process_intelligence.tables import (
    t_approval_requests,
    t_workflow_instances,
    t_workflow_step_runs,
)
from kortex.engines.storage.interfaces import IDataStore

T = TypeVar("T")


class TenantScopedProcessAnalyticsRepository(IProcessAnalyticsRepository):
    """Repository executing read-only queries with structurally enforced tenant scoping."""

    def __init__(
        self,
        data_store: IDataStore,
        tenant_id: str,
        analyzer: IProcessAnalyzer,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Initialize repository with authoritative tenant_id and IDataStore.

        Args:
            data_store: StorageEngine IDataStore facade.
            tenant_id: Authoritative tenant identity from execution context.
            analyzer: Statistical analyzer for percentiles and metric calculation.
            timeout_seconds: Operation-level query timeout in seconds.
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("Authoritative tenant_id must be a non-empty string.")
        self._data_store = data_store
        self._tenant_id = tenant_id
        self._analyzer = analyzer
        self._timeout_seconds = timeout_seconds

    async def _execute_with_timeout(self, action: Callable[[AsyncSession], Awaitable[T]]) -> T:
        """Execute a database action under the application-level operation timeout."""
        try:
            return await asyncio.wait_for(
                self._data_store.execute_in_transaction(action),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            raise ProcessAnalyticsTimeoutError(
                f"Process Intelligence query exceeded operation timeout of {self._timeout_seconds}s"
            ) from exc

    async def get_summary_kpis(
        self,
        definition_id: str | None,
        since: datetime.datetime,
        until: datetime.datetime,
        window_clamped: bool,
    ) -> ProcessSummaryKPIs:
        """Execute complete SQL aggregations for high-level business process KPIs."""
        if until < since:
            raise InvalidTimeRangeError(f"until timestamp ({until}) cannot be earlier than since ({since})")

        tenant = self._tenant_id

        async def _query(session: AsyncSession) -> dict[str, Any]:
            # 1. Base filter for instances
            where_clauses = [
                t_workflow_instances.c.tenant_id == tenant,
                t_workflow_instances.c.created_at >= since,
                t_workflow_instances.c.created_at <= until,
            ]
            if definition_id:
                where_clauses.append(t_workflow_instances.c.definition_id == definition_id)

            # 2. Aggregation counts
            stmt_counts = select(
                func.count(t_workflow_instances.c.id).label("total"),
                func.count(func.nullif(t_workflow_instances.c.state != STATE_COMPLETED, True)).label("completed"),
                func.count(func.nullif(t_workflow_instances.c.state != STATE_FAILED, True)).label("failed"),
                func.count(func.nullif(t_workflow_instances.c.state != STATE_CANCELLED, True)).label("cancelled"),
            ).where(and_(*where_clauses))

            res_counts = await session.execute(stmt_counts)
            row_counts = res_counts.one()

            total_inst = row_counts.total or 0
            completed_runs = row_counts.completed or 0
            failed_runs = row_counts.failed or 0
            cancelled_runs = row_counts.cancelled or 0
            terminal_runs = completed_runs + failed_runs + cancelled_runs
            active_runs = max(0, total_inst - terminal_runs)

            # 3. Fetch completed instance timestamps for cycle-time calculations
            stmt_completed_times = select(
                t_workflow_instances.c.created_at,
                t_workflow_instances.c.updated_at,
            ).where(
                and_(
                    *where_clauses,
                    t_workflow_instances.c.state == STATE_COMPLETED,
                )
            )
            res_completed = await session.execute(stmt_completed_times)
            cycle_times_ms: list[float] = []
            for row in res_completed.all():
                if row.created_at and row.updated_at:
                    dur_ms = (row.updated_at - row.created_at).total_seconds() * 1000.0
                    if dur_ms >= 0.0:
                        cycle_times_ms.append(dur_ms)

            # 4. Discover all distinct versions aggregated
            stmt_versions = select(distinct(t_workflow_instances.c.definition_version)).where(and_(*where_clauses))
            res_versions = await session.execute(stmt_versions)
            versions_included = sorted([v[0] for v in res_versions.all() if v[0]])

            # 5. Fetch approval wait times for this tenant in query window
            stmt_approvals = select(
                t_approval_requests.c.created_at,
                t_approval_requests.c.updated_at,
            ).where(
                and_(
                    t_approval_requests.c.tenant_id == tenant,
                    t_approval_requests.c.state.in_(["APPROVED", "REJECTED"]),
                    t_approval_requests.c.created_at >= since,
                    t_approval_requests.c.created_at <= until,
                )
            )
            res_approvals = await session.execute(stmt_approvals)
            approval_waits: list[float] = []
            for row in res_approvals.all():
                if row.created_at and row.updated_at:
                    wait_ms = (row.updated_at - row.created_at).total_seconds() * 1000.0
                    if wait_ms >= 0.0:
                        approval_waits.append(wait_ms)

            return {
                "total": total_inst,
                "completed": completed_runs,
                "failed": failed_runs,
                "cancelled": cancelled_runs,
                "active": active_runs,
                "cycle_times_ms": cycle_times_ms,
                "versions_included": versions_included,
                "approval_waits": approval_waits,
            }

        data = await self._execute_with_timeout(_query)

        # Statistical calculations
        total_inst = data["total"]
        completed = data["completed"]
        failed = data["failed"]
        cancelled = data["cancelled"]
        active = data["active"]
        terminal = completed + failed + cancelled

        window_days = max(1.0, (until - since).total_seconds() / 86400.0)
        throughput_per_day = round(completed / window_days, 2)

        success_rate = round((completed / terminal) * 100.0, 2) if terminal > 0 else 0.0
        failure_rate = round((failed / terminal) * 100.0, 2) if terminal > 0 else 0.0

        cycle_times: list[float] = data["cycle_times_ms"]
        avg_cycle = round(sum(cycle_times) / len(cycle_times), 2) if cycle_times else 0.0
        p50_cycle = self._analyzer.calculate_percentile(cycle_times, 50.0)
        p90_cycle = self._analyzer.calculate_percentile(cycle_times, 90.0)
        p99_cycle = self._analyzer.calculate_percentile(cycle_times, 99.0)

        app_waits: list[float] = data["approval_waits"]
        avg_app_wait = round(sum(app_waits) / len(app_waits), 2) if app_waits else None

        return ProcessSummaryKPIs(
            definition_id=definition_id,
            total_instances=total_inst,
            completed_runs=completed,
            failed_runs=failed,
            cancelled_runs=cancelled,
            active_runs=active,
            throughput_per_day=throughput_per_day,
            success_rate=success_rate,
            failure_rate=failure_rate,
            avg_cycle_time_ms=avg_cycle,
            p50_cycle_time_ms=p50_cycle,
            p90_cycle_time_ms=p90_cycle,
            p99_cycle_time_ms=p99_cycle,
            avg_approval_turnaround_ms=avg_app_wait,
            versions_included=data["versions_included"],
            window_clamped=window_clamped,
            effective_since=since,
            effective_until=until,
            is_sampled=False,
        )

    async def get_bottlenecks(
        self,
        definition_id: str | None,
        since: datetime.datetime,
        until: datetime.datetime,
        window_clamped: bool,
        limit: int = 20,
    ) -> BottlenecksResult:
        """Aggregate and rank step-level performance and approval wait latencies."""
        if until < since:
            raise InvalidTimeRangeError(f"until timestamp ({until}) cannot be earlier than since ({since})")

        tenant = self._tenant_id

        async def _query(session: AsyncSession) -> tuple[list[dict[str, Any]], dict[str, float]]:
            # Join step runs to instances strictly enforcing tenant isolation
            join_cond = t_workflow_step_runs.c.instance_id == t_workflow_instances.c.id
            where_clauses = [
                t_workflow_instances.c.tenant_id == tenant,
                t_workflow_step_runs.c.started_at >= since,
                t_workflow_step_runs.c.started_at <= until,
            ]
            if definition_id:
                where_clauses.append(t_workflow_instances.c.definition_id == definition_id)

            stmt_steps = (
                select(
                    t_workflow_step_runs.c.step_id,
                    t_workflow_step_runs.c.status,
                    t_workflow_step_runs.c.started_at,
                    t_workflow_step_runs.c.completed_at,
                )
                .select_from(t_workflow_step_runs.join(t_workflow_instances, join_cond))
                .where(and_(*where_clauses))
            )

            res_steps = await session.execute(stmt_steps)
            step_groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "failed": 0, "durations": []})

            for row in res_steps.all():
                s_id = row.step_id
                step_groups[s_id]["total"] += 1
                if row.status == STATE_FAILED:
                    step_groups[s_id]["failed"] += 1
                if row.started_at and row.completed_at:
                    dur_ms = (row.completed_at - row.started_at).total_seconds() * 1000.0
                    if dur_ms >= 0.0:
                        step_groups[s_id]["durations"].append(dur_ms)

            # Query approval request latencies per step_id for this tenant
            stmt_approvals = select(
                t_approval_requests.c.step_id,
                t_approval_requests.c.created_at,
                t_approval_requests.c.updated_at,
            ).where(
                and_(
                    t_approval_requests.c.tenant_id == tenant,
                    t_approval_requests.c.state.in_(["APPROVED", "REJECTED"]),
                    t_approval_requests.c.created_at >= since,
                    t_approval_requests.c.created_at <= until,
                    t_approval_requests.c.step_id.isnot(None),
                )
            )
            res_app = await session.execute(stmt_approvals)
            approval_step_waits: dict[str, list[float]] = defaultdict(list)
            for row in res_app.all():
                if row.step_id and row.created_at and row.updated_at:
                    wait_ms = (row.updated_at - row.created_at).total_seconds() * 1000.0
                    if wait_ms >= 0.0:
                        approval_step_waits[row.step_id].append(wait_ms)

            approval_avg_map: dict[str, float] = {}
            for step_key, waits in approval_step_waits.items():
                if waits:
                    approval_avg_map[step_key] = sum(waits) / len(waits)

            raw_metrics: list[dict[str, Any]] = []
            for step_key, g in step_groups.items():
                raw_metrics.append(
                    {
                        "step_id": step_key,
                        "total_executions": g["total"],
                        "failed_executions": g["failed"],
                        "durations": g["durations"],
                    }
                )

            return raw_metrics, approval_avg_map

        raw_metrics, app_map = await self._execute_with_timeout(_query)
        ranked = self._analyzer.rank_bottlenecks(raw_metrics, app_map, limit=limit)

        return BottlenecksResult(
            definition_id=definition_id,
            steps=ranked,
            window_clamped=window_clamped,
            effective_since=since,
            effective_until=until,
        )

    async def get_traces_for_mining(
        self,
        definition_id: str,
        version: str | None,
        since: datetime.datetime,
        until: datetime.datetime,
        max_instances: int = 1000,
    ) -> tuple[list[RawInstanceTrace], int, str | None, list[str]]:
        """Fetch raw instance traces ordered deterministically for graph and variant mining."""
        if until < since:
            raise InvalidTimeRangeError(f"until timestamp ({until}) cannot be earlier than since ({since})")

        tenant = self._tenant_id
        clamped_sample_limit = max(1, min(max_instances, 1000))

        async def _query(session: AsyncSession) -> tuple[list[RawInstanceTrace], int, str | None, list[str]]:
            # 1. Base filter for instances
            where_clauses = [
                t_workflow_instances.c.tenant_id == tenant,
                t_workflow_instances.c.definition_id == definition_id,
                t_workflow_instances.c.created_at >= since,
                t_workflow_instances.c.created_at <= until,
            ]

            # Discover available versions
            stmt_versions = (
                select(distinct(t_workflow_instances.c.definition_version))
                .where(and_(*where_clauses))
                .order_by(t_workflow_instances.c.definition_version.desc())
            )
            res_v = await session.execute(stmt_versions)
            available_versions = [row[0] for row in res_v.all() if row[0]]

            # Total matching count
            stmt_count = select(func.count(t_workflow_instances.c.id)).where(and_(*where_clauses))
            res_c = await session.execute(stmt_count)
            total_matching = res_c.scalar() or 0

            # 2. Determine target version to analyze
            target_version: str | None = version
            if target_version:
                where_clauses.append(t_workflow_instances.c.definition_version == target_version)
            elif available_versions:
                # Deterministically select latest version discovered
                target_version = available_versions[0]
                where_clauses.append(t_workflow_instances.c.definition_version == target_version)

            # 3. Fetch instance sample (ordered deterministically: created_at DESC, id DESC)
            stmt_instances = (
                select(
                    t_workflow_instances.c.id,
                    t_workflow_instances.c.definition_id,
                    t_workflow_instances.c.definition_version,
                    t_workflow_instances.c.state,
                    t_workflow_instances.c.status,
                    t_workflow_instances.c.created_at,
                    t_workflow_instances.c.updated_at,
                )
                .where(and_(*where_clauses))
                .order_by(
                    desc(t_workflow_instances.c.created_at),
                    desc(t_workflow_instances.c.id),
                )
                .limit(clamped_sample_limit)
            )
            res_inst = await session.execute(stmt_instances)
            instances = res_inst.all()

            if not instances:
                return [], total_matching, target_version, available_versions

            inst_ids = [row.id for row in instances]
            inst_dict: dict[str, RawInstanceTrace] = {
                row.id: RawInstanceTrace(
                    instance_id=row.id,
                    definition_id=row.definition_id,
                    definition_version=row.definition_version,
                    state=row.state,
                    status=row.status,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    steps=[],
                )
                for row in instances
            }

            # 4. Fetch step runs for these instances (ordered by started_at ASC, id ASC)
            stmt_steps = (
                select(
                    t_workflow_step_runs.c.id,
                    t_workflow_step_runs.c.instance_id,
                    t_workflow_step_runs.c.step_id,
                    t_workflow_step_runs.c.status,
                    t_workflow_step_runs.c.started_at,
                    t_workflow_step_runs.c.completed_at,
                    t_workflow_step_runs.c.attempt,
                )
                .where(t_workflow_step_runs.c.instance_id.in_(inst_ids))
                .order_by(
                    t_workflow_step_runs.c.started_at.asc(),
                    t_workflow_step_runs.c.id.asc(),
                )
            )
            res_steps = await session.execute(stmt_steps)
            for s_row in res_steps.all():
                trace = inst_dict.get(s_row.instance_id)
                if trace:
                    trace.steps.append(
                        RawStepExecution(
                            id=s_row.id,
                            instance_id=s_row.instance_id,
                            step_id=s_row.step_id,
                            status=s_row.status,
                            started_at=s_row.started_at,
                            completed_at=s_row.completed_at,
                            attempt=s_row.attempt,
                        )
                    )

            traces = [inst_dict[row.id] for row in instances]
            return traces, total_matching, target_version, available_versions

        return await self._execute_with_timeout(_query)
