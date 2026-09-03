"""
KORTEX Process Intelligence Engine.

Provides execution telemetry, statistical process diagnostics, directly-follows graph
mining, trace variant clustering, and step bottleneck analytics for business workflows.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.process_intelligence.analyzer import ProcessAnalyzer
from kortex.engines.process_intelligence.miner import ProcessMiner
from kortex.engines.process_intelligence.models import (
    BottlenecksResult,
    ProcessGraph,
    ProcessSummaryKPIs,
    VariantListResult,
)
from kortex.engines.process_intelligence.repository import (
    TenantScopedProcessAnalyticsRepository,
)
from kortex.engines.security.exceptions import AuthorizationDeniedError
from kortex.engines.storage.interfaces import IDataStore, IEngineDiagnostics

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.process_intelligence")

MAX_QUERY_WINDOW_DAYS = 90
DEFAULT_QUERY_WINDOW_DAYS = 30


class ProcessIntelligenceEngine(BaseEngine, IEngineDiagnostics):
    """Process Intelligence and execution telemetry engine."""

    def __init__(self) -> None:
        super().__init__()
        self._kernel: Kernel | None = None
        self._data_store: IDataStore | None = None
        self._miner: ProcessMiner | None = None
        self._analyzer: ProcessAnalyzer | None = None
        self._queries_executed_total: int = 0
        self._last_query_timestamp: str | None = None

    @property
    def name(self) -> str:
        return "process_intelligence"

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "registry", "configuration"]

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize the Process Intelligence engine and register capabilities."""
        self._set_state(EngineState.INITIALIZING)
        self._kernel = kernel
        self.logger.info("Initializing Process Intelligence Engine...")

        # Resolve storage data store dependency
        storage_engine = kernel.get_engine("storage")
        if not storage_engine or not hasattr(storage_engine, "data"):
            raise RuntimeError("Storage Engine with IDataStore is required for Process Intelligence.")
        data_attr = storage_engine.data
        self._data_store = data_attr() if callable(data_attr) else data_attr

        # Initialize algorithmic components
        self._miner = ProcessMiner()
        self._analyzer = ProcessAnalyzer()

        # Register Phase 5 Owner-Approved Capabilities
        # 1. Summary KPIs
        kernel.register_capability(
            name="kortex.process_intelligence.summary.get",
            description="Retrieve high-level execution KPIs and throughput summary for workflows",
            provider=self.name,
            handler=self.get_summary,
            required_permissions=["workflow:read"],
            requires_execution_context=True,
        )

        # 2. Bottlenecks
        kernel.register_capability(
            name="kortex.process_intelligence.bottlenecks.get",
            description="Retrieve step-level performance, failure rates, and bottleneck rankings",
            provider=self.name,
            handler=self.get_bottlenecks,
            required_permissions=["workflow:read"],
            requires_execution_context=True,
        )

        # 3. Process Graph (DFG)
        kernel.register_capability(
            name="kortex.process_intelligence.process_graph.get",
            description="Construct a directly-follows process graph from observed execution traces",
            provider=self.name,
            handler=self.get_process_graph,
            required_permissions=["workflow:read"],
            requires_execution_context=True,
        )

        # 4. Trace Variants
        kernel.register_capability(
            name="kortex.process_intelligence.variants.list",
            description="List unique sequential execution paths and their frequency distribution",
            provider=self.name,
            handler=self.list_variants,
            required_permissions=["workflow:read"],
            requires_execution_context=True,
        )

        self._set_state(EngineState.READY)
        self.logger.info("Process Intelligence Engine initialized successfully.")

    async def start(self) -> None:
        """Start the Process Intelligence Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Process Intelligence Engine running.")

    async def stop(self) -> None:
        """Stop the Process Intelligence Engine."""
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Process Intelligence Engine stopped.")

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health check telemetry."""
        return {
            "engine": self.name,
            "status": "healthy" if self.state == EngineState.RUNNING else "unhealthy",
            "state": self.state.value,
            "queries_executed_total": self._queries_executed_total,
            "last_query_timestamp": self._last_query_timestamp,
        }

    # -- Internal Helpers ---------------------------------------------------

    def _resolve_time_window(
        self,
        since: datetime.datetime | None,
        until: datetime.datetime | None,
    ) -> tuple[datetime.datetime, datetime.datetime, bool]:
        """Resolve and clamp query time window to safety boundaries."""
        effective_until = until or datetime.datetime.now(datetime.UTC)
        if since is None:
            effective_since = effective_until - datetime.timedelta(days=DEFAULT_QUERY_WINDOW_DAYS)
            return effective_since, effective_until, False

        diff = effective_until - since
        if diff > datetime.timedelta(days=MAX_QUERY_WINDOW_DAYS):
            clamped_since = effective_until - datetime.timedelta(days=MAX_QUERY_WINDOW_DAYS)
            return clamped_since, effective_until, True

        return since, effective_until, False

    def _get_scoped_repository(
        self,
        execution_context: CapabilityExecutionContext,
        caller_tenant_id: str | None = None,
    ) -> TenantScopedProcessAnalyticsRepository:
        """Construct a tenant-scoped repository bound strictly to execution context."""
        if not execution_context or not execution_context.tenant_id:
            raise AuthorizationDeniedError("An authenticated execution context with tenant_id is required.")

        auth_tenant = execution_context.tenant_id
        if caller_tenant_id is not None and caller_tenant_id != auth_tenant:
            raise AuthorizationDeniedError(
                f"Supplied tenant_id '{caller_tenant_id}' does not match the authenticated tenant '{auth_tenant}'."
            )

        if not self._data_store or not self._analyzer:
            raise RuntimeError("Process Intelligence Engine is not properly initialized.")

        self._queries_executed_total += 1
        self._last_query_timestamp = datetime.datetime.now(datetime.UTC).isoformat()

        return TenantScopedProcessAnalyticsRepository(
            data_store=self._data_store,
            tenant_id=auth_tenant,
            analyzer=self._analyzer,
        )

    # -- Capability Handlers ------------------------------------------------

    async def get_summary(
        self,
        execution_context: CapabilityExecutionContext,
        definition_id: str | None = None,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        tenant_id: str | None = None,
    ) -> ProcessSummaryKPIs:
        """Handler for `kortex.process_intelligence.summary.get`."""
        eff_since, eff_until, clamped = self._resolve_time_window(since, until)
        repo = self._get_scoped_repository(execution_context, caller_tenant_id=tenant_id)
        return await repo.get_summary_kpis(
            definition_id=definition_id,
            since=eff_since,
            until=eff_until,
            window_clamped=clamped,
        )

    async def get_bottlenecks(
        self,
        execution_context: CapabilityExecutionContext,
        definition_id: str | None = None,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        limit: int = 20,
        tenant_id: str | None = None,
    ) -> BottlenecksResult:
        """Handler for `kortex.process_intelligence.bottlenecks.get`."""
        eff_since, eff_until, clamped = self._resolve_time_window(since, until)
        repo = self._get_scoped_repository(execution_context, caller_tenant_id=tenant_id)
        return await repo.get_bottlenecks(
            definition_id=definition_id,
            since=eff_since,
            until=eff_until,
            window_clamped=clamped,
            limit=limit,
        )

    async def get_process_graph(
        self,
        execution_context: CapabilityExecutionContext,
        definition_id: str,
        version: str | None = None,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        max_instances: int = 1000,
        tenant_id: str | None = None,
    ) -> ProcessGraph:
        """Handler for `kortex.process_intelligence.process_graph.get`."""
        if not definition_id or not isinstance(definition_id, str):
            raise ValueError("definition_id must be a non-empty string.")

        eff_since, eff_until, clamped = self._resolve_time_window(since, until)
        repo = self._get_scoped_repository(execution_context, caller_tenant_id=tenant_id)

        traces, total_matching, ver_analyzed, avail_versions = await repo.get_traces_for_mining(
            definition_id=definition_id,
            version=version,
            since=eff_since,
            until=eff_until,
            max_instances=max_instances,
        )

        if not self._miner:
            raise RuntimeError("ProcessMiner is not initialized.")

        return self._miner.build_directly_follows_graph(
            definition_id=definition_id,
            traces=traces,
            total_matching=total_matching,
            window_clamped=clamped,
            version_analyzed=ver_analyzed,
            available_versions=avail_versions,
        )

    async def list_variants(
        self,
        execution_context: CapabilityExecutionContext,
        definition_id: str,
        version: str | None = None,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        limit: int = 20,
        max_instances: int = 1000,
        tenant_id: str | None = None,
    ) -> VariantListResult:
        """Handler for `kortex.process_intelligence.variants.list`."""
        if not definition_id or not isinstance(definition_id, str):
            raise ValueError("definition_id must be a non-empty string.")

        eff_since, eff_until, clamped = self._resolve_time_window(since, until)
        repo = self._get_scoped_repository(execution_context, caller_tenant_id=tenant_id)

        traces, total_matching, ver_analyzed, avail_versions = await repo.get_traces_for_mining(
            definition_id=definition_id,
            version=version,
            since=eff_since,
            until=eff_until,
            max_instances=max_instances,
        )

        if not self._miner:
            raise RuntimeError("ProcessMiner is not initialized.")

        return self._miner.extract_trace_variants(
            definition_id=definition_id,
            traces=traces,
            limit=limit,
            total_matching=total_matching,
            window_clamped=clamped,
            version_analyzed=ver_analyzed,
            available_versions=avail_versions,
        )
