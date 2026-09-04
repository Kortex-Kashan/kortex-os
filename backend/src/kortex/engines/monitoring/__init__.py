"""KORTEX Monitoring Engine.

Phase 7 — Production Hardening — Monitoring Engine.
Provides bounded in-memory metrics, time-series storage, system telemetry,
operational threshold evaluation, and dashboard state presentation.
"""

from __future__ import annotations

from kortex.engines.monitoring.collector import MetricsCollector, SystemTelemetryCollector
from kortex.engines.monitoring.constants import (
    CAPABILITY_MONITORING_DASHBOARD_GET,
    CAPABILITY_MONITORING_DIAGNOSTICS_GET,
    CAPABILITY_MONITORING_METRICS_GET,
    CAPABILITY_MONITORING_TIMESERIES_GET,
    EVENT_MONITORING_THRESHOLD_EXCEEDED,
    EVENT_MONITORING_THRESHOLD_RECOVERED,
    EVENT_SENTINEL_HEALTH_CHANGED,
    MAX_ACTIVE_SERIES,
    MAX_METRIC_NAMES,
    MONITORING_CAPABILITIES,
    MONITORING_ENGINE_NAME,
    MONITORING_ENGINE_VERSION,
    MONITORING_EVENTS,
    MONITORING_SECURITY_CLASSIFICATION,
    PERMISSION_MONITORING_READ,
)
from kortex.engines.monitoring.diagnostics import MonitoringDiagnostics
from kortex.engines.monitoring.engine import MonitoringEngine
from kortex.engines.monitoring.events import (
    MonitoringEventPayload,
    MonitoringEventPublisher,
    ThresholdExceededPayload,
    ThresholdRecoveredPayload,
)
from kortex.engines.monitoring.interfaces import (
    IMetric,
    IMetricRegistry,
    IMonitoringEngine,
    ITimeSeriesBuffer,
)
from kortex.engines.monitoring.metrics import Counter, Gauge, Histogram, Timer
from kortex.engines.monitoring.models import (
    AlertRecord,
    DashboardData,
    HistogramSnapshot,
    MetricType,
    MetricValue,
    MonitoringConfig,
    ThresholdSeverity,
    ThresholdState,
    TimeSeriesPoint,
    TimeSeriesQueryResponse,
)
from kortex.engines.monitoring.normalizer import (
    DiagnosticsNormalizer,
    NormalizationResult,
    NormalizedMetric,
)
from kortex.engines.monitoring.registry import MetricRegistry
from kortex.engines.monitoring.thresholds import ThresholdEvaluator, ThresholdRule
from kortex.engines.monitoring.timeseries import TimeSeriesBuffer

__all__ = [
    "CAPABILITY_MONITORING_DASHBOARD_GET",
    "CAPABILITY_MONITORING_DIAGNOSTICS_GET",
    "CAPABILITY_MONITORING_METRICS_GET",
    "CAPABILITY_MONITORING_TIMESERIES_GET",
    "EVENT_MONITORING_THRESHOLD_EXCEEDED",
    "EVENT_MONITORING_THRESHOLD_RECOVERED",
    "EVENT_SENTINEL_HEALTH_CHANGED",
    "MAX_ACTIVE_SERIES",
    "MAX_METRIC_NAMES",
    "MONITORING_CAPABILITIES",
    "MONITORING_ENGINE_NAME",
    "MONITORING_ENGINE_VERSION",
    "MONITORING_EVENTS",
    "MONITORING_SECURITY_CLASSIFICATION",
    "PERMISSION_MONITORING_READ",
    "AlertRecord",
    "Counter",
    "DashboardData",
    "DiagnosticsNormalizer",
    "Gauge",
    "Histogram",
    "HistogramSnapshot",
    "IMetric",
    "IMetricRegistry",
    "IMonitoringEngine",
    "ITimeSeriesBuffer",
    "MetricRegistry",
    "MetricType",
    "MetricValue",
    "MetricsCollector",
    "MonitoringConfig",
    "MonitoringDiagnostics",
    "MonitoringEngine",
    "MonitoringEventPayload",
    "MonitoringEventPublisher",
    "NormalizationResult",
    "NormalizedMetric",
    "SystemTelemetryCollector",
    "ThresholdEvaluator",
    "ThresholdExceededPayload",
    "ThresholdRecoveredPayload",
    "ThresholdRule",
    "ThresholdSeverity",
    "ThresholdState",
    "TimeSeriesBuffer",
    "TimeSeriesPoint",
    "TimeSeriesQueryResponse",
    "Timer",
]
