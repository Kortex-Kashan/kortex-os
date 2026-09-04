"""Pydantic and Enum data models for the KORTEX Monitoring Engine.

Provides types, metric representations, threshold state containers,
and dashboard payloads adhering to strict validation and immutability.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricType(str, Enum):
    """Supported metric primitive types."""

    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    TIMER = "timer"


class ThresholdSeverity(str, Enum):
    """Severity levels for operational threshold alerts."""

    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class ThresholdState(str, Enum):
    """Operational state of a monitored threshold."""

    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TimeSeriesPoint(BaseModel):
    """Single point in a time-series circular buffer."""

    model_config = ConfigDict(frozen=True)

    timestamp: str = Field(description="ISO-8601 UTC timestamp string")
    value: float = Field(description="Finite numerical metric value")


class HistogramSnapshot(BaseModel):
    """Statistical summary of a histogram's sliding-window sample."""

    model_config = ConfigDict(frozen=True)

    count: int = Field(default=0, ge=0, description="Total sample observations in reservoir")
    sum: float = Field(default=0.0, description="Sum of sample observations in reservoir")
    min: float | None = Field(default=None, description="Minimum observation in reservoir")
    max: float | None = Field(default=None, description="Maximum observation in reservoir")
    avg: float = Field(default=0.0, description="Average of observations in reservoir")
    p50: float | None = Field(default=None, description="Approximate 50th percentile")
    p90: float | None = Field(default=None, description="Approximate 90th percentile")
    p95: float | None = Field(default=None, description="Approximate 95th percentile")
    p99: float | None = Field(default=None, description="Approximate 99th percentile")


class MetricValue(BaseModel):
    """Canonical representation of an individual metric series."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Canonical metric name")
    type: MetricType = Field(description="Primitive metric type")
    labels: dict[str, str] = Field(default_factory=dict, description="Dimensions / labels")
    value: float | None = Field(default=None, description="Scalar value for Counter / Gauge")
    histogram: HistogramSnapshot | None = Field(default=None, description="Statistical summary for Histogram / Timer")
    timestamp: str = Field(description="ISO-8601 UTC collection timestamp")


class TimeSeriesQueryResponse(BaseModel):
    """Response payload for time-series point queries."""

    model_config = ConfigDict(frozen=True)

    metric_name: str = Field(description="Queried metric name")
    labels: dict[str, str] = Field(default_factory=dict, description="Queried labels")
    points: list[TimeSeriesPoint] = Field(default_factory=list, description="Ordered time-series points")
    duration_seconds: int = Field(description="Time window covered in seconds")


class AlertRecord(BaseModel):
    """Active operational threshold alert state."""

    metric_name: str = Field(description="Monitored metric name")
    subsystem: str = Field(description="Originating subsystem")
    current_value: float = Field(description="Current value triggering alert")
    threshold: float = Field(description="Configured threshold value")
    severity: ThresholdSeverity = Field(description="Alert severity")
    consecutive_breaches: int = Field(ge=0, description="Number of consecutive cycles in breach")
    first_breached_at: str = Field(description="ISO-8601 UTC first breach timestamp")
    last_evaluated_at: str = Field(description="ISO-8601 UTC last evaluation timestamp")
    last_event_emitted_at: str | None = Field(default=None, description="ISO-8601 UTC last emitted event timestamp")


class DashboardData(BaseModel):
    """Consolidated operational dashboard query payload."""

    model_config = ConfigDict(frozen=True)

    timestamp: str = Field(description="ISO-8601 UTC generation timestamp")
    sentinel_health: dict[str, Any] = Field(description="Summary of Sentinel subsystem health")
    system_resources: dict[str, Any] = Field(description="Current host / process resource telemetry")
    top_metrics: list[dict[str, Any]] = Field(default_factory=list, description="Key operational metrics")
    active_alerts: list[dict[str, Any]] = Field(default_factory=list, description="Active threshold alerts")
    engines_monitored: list[str] = Field(default_factory=list, description="List of polled engine names")


class MonitoringConfig(BaseModel):
    """Validated configuration for the Monitoring Engine."""

    collect_interval_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    buffer_max_points: int = Field(default=360, ge=60, le=1440)
    enabled: bool = Field(default=True)
    probe_timeout_seconds: float = Field(default=1.0, ge=0.2, le=5.0)
    memory_warning_threshold_mb: float = Field(default=1024.0, gt=0.0)
    memory_critical_threshold_mb: float = Field(default=2048.0, gt=0.0)
    event_loop_lag_warning_seconds: float = Field(default=0.5, gt=0.0)
    event_loop_lag_critical_seconds: float = Field(default=1.5, gt=0.0)
