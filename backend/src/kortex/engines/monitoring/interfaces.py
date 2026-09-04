"""Public protocols and interfaces for the KORTEX Monitoring Engine.

Defines the contracts for metric registries, time-series storage,
and the monitoring engine itself.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kortex.engines.monitoring.models import (
    DashboardData,
    MetricType,
    MetricValue,
    TimeSeriesPoint,
    TimeSeriesQueryResponse,
)


@runtime_checkable
class IMetric(Protocol):
    """Common interface for all metric primitives."""

    @property
    def name(self) -> str:
        """Canonical name of the metric."""
        ...

    @property
    def metric_type(self) -> MetricType:
        """Primitive type."""
        ...

    @property
    def labels(self) -> dict[str, str]:
        """Dimensional labels."""
        ...

    def snapshot(self, timestamp: str | None = None) -> MetricValue:
        """Generate an immutable snapshot of current metric state."""
        ...


@runtime_checkable
class IMetricRegistry(Protocol):
    """Container managing registered metrics with cardinality enforcement."""

    def counter(self, name: str, labels: dict[str, str] | None = None) -> Any:
        """Get or create a Counter metric."""
        ...

    def gauge(self, name: str, labels: dict[str, str] | None = None) -> Any:
        """Get or create a Gauge metric."""
        ...

    def histogram(self, name: str, labels: dict[str, str] | None = None) -> Any:
        """Get or create a Histogram metric."""
        ...

    def timer(self, name: str, labels: dict[str, str] | None = None) -> Any:
        """Get or create a Timer metric."""
        ...

    def get_all_metrics(self, subsystem: str | None = None) -> list[MetricValue]:
        """Return snapshots of all registered metrics."""
        ...


@runtime_checkable
class ITimeSeriesBuffer(Protocol):
    """Bounded circular buffer storing historical time-series points."""

    def append(self, series_id: str, point: TimeSeriesPoint) -> None:
        """Append a time-series point to the series buffer."""
        ...

    def query(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        duration_seconds: int = 3600,
    ) -> TimeSeriesQueryResponse:
        """Query historical points within duration."""
        ...


@runtime_checkable
class IMonitoringEngine(Protocol):
    """Public protocol for the Monitoring Engine."""

    async def get_metrics(
        self,
        subsystem: str | None = None,
        metric_names: list[str] | None = None,
    ) -> list[MetricValue]:
        """Query real-time metrics across subsystems."""
        ...

    async def get_timeseries(
        self,
        metric_name: str,
        labels: dict[str, str] | None = None,
        duration_seconds: int = 3600,
    ) -> TimeSeriesQueryResponse:
        """Query time-series points for a metric."""
        ...

    async def get_dashboard(self) -> DashboardData:
        """Query consolidated operational dashboard."""
        ...
