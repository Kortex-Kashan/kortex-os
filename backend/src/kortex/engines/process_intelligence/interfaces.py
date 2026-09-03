"""
KORTEX Process Intelligence Protocols & Interfaces.

Defines the abstract interface contracts for process graph mining,
statistical bottleneck analysis, and tenant-scoped repository projections.
"""

from __future__ import annotations

import datetime
from typing import Any, Protocol, runtime_checkable

from kortex.engines.process_intelligence.models import (
    BottlenecksResult,
    ProcessGraph,
    ProcessSummaryKPIs,
    RawInstanceTrace,
    StepBottleneck,
    VariantListResult,
)


@runtime_checkable
class IProcessMiner(Protocol):
    """Protocol for extracting directly-follows graphs and trace variants from traces."""

    def build_directly_follows_graph(
        self,
        definition_id: str,
        traces: list[RawInstanceTrace],
        total_matching: int,
        window_clamped: bool,
        version_analyzed: str | None,
        available_versions: list[str],
    ) -> ProcessGraph:
        """Construct a bounded directly-follows graph from observed instance traces."""
        ...

    def extract_trace_variants(
        self,
        definition_id: str,
        traces: list[RawInstanceTrace],
        limit: int,
        total_matching: int,
        window_clamped: bool,
        version_analyzed: str | None,
        available_versions: list[str],
    ) -> VariantListResult:
        """Extract unique sequential execution paths and their frequency distributions."""
        ...


@runtime_checkable
class IProcessAnalyzer(Protocol):
    """Protocol for calculating statistical percentiles, bottlenecks, and summary KPIs."""

    def calculate_percentile(self, values: list[float], percentile: float) -> float:
        """Calculate NIST Method 8 linear interpolation percentile for non-negative values."""
        ...

    def rank_bottlenecks(
        self,
        step_metrics_raw: list[dict[str, Any]],
        approval_wait_map: dict[str, float],
        limit: int,
    ) -> list[StepBottleneck]:
        """Rank workflow steps by bottleneck latency severity."""
        ...


@runtime_checkable
class IProcessAnalyticsRepository(Protocol):
    """Protocol for tenant-scoped relational database projections over IDataStore."""

    async def get_summary_kpis(
        self,
        definition_id: str | None,
        since: datetime.datetime,
        until: datetime.datetime,
        window_clamped: bool,
    ) -> ProcessSummaryKPIs:
        """Execute complete SQL aggregations for high-level business process KPIs."""
        ...

    async def get_bottlenecks(
        self,
        definition_id: str | None,
        since: datetime.datetime,
        until: datetime.datetime,
        window_clamped: bool,
        limit: int,
    ) -> BottlenecksResult:
        """Aggregate and rank step-level performance and approval wait latencies."""
        ...

    async def get_traces_for_mining(
        self,
        definition_id: str,
        version: str | None,
        since: datetime.datetime,
        until: datetime.datetime,
        max_instances: int,
    ) -> tuple[list[RawInstanceTrace], int, str | None, list[str]]:
        """Fetch raw instance traces ordered deterministically for graph and variant mining."""
        ...
