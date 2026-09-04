"""Constants and configuration defaults for the KORTEX Monitoring Engine.

Defines capability identifiers, event topics, security permissions, cardinality
limits, and operational threshold defaults according to Phase 7 specifications.
"""

from __future__ import annotations

import re

# -- Capabilities ------------------------------------------------------------
CAPABILITY_MONITORING_METRICS_GET = "kortex.monitoring.metrics.get"
CAPABILITY_MONITORING_TIMESERIES_GET = "kortex.monitoring.timeseries.get"
CAPABILITY_MONITORING_DASHBOARD_GET = "kortex.monitoring.dashboard.get"
CAPABILITY_MONITORING_DIAGNOSTICS_GET = "kortex.monitoring.diagnostics.get"

MONITORING_CAPABILITIES: tuple[str, ...] = (
    CAPABILITY_MONITORING_METRICS_GET,
    CAPABILITY_MONITORING_TIMESERIES_GET,
    CAPABILITY_MONITORING_DASHBOARD_GET,
    CAPABILITY_MONITORING_DIAGNOSTICS_GET,
)

# -- Event Topics ------------------------------------------------------------
EVENT_MONITORING_THRESHOLD_EXCEEDED = "kortex.monitoring.threshold.exceeded"
EVENT_MONITORING_THRESHOLD_RECOVERED = "kortex.monitoring.threshold.recovered"

MONITORING_EVENTS: tuple[str, ...] = (
    EVENT_MONITORING_THRESHOLD_EXCEEDED,
    EVENT_MONITORING_THRESHOLD_RECOVERED,
)

# External event consumed from Sentinel
EVENT_SENTINEL_HEALTH_CHANGED = "kortex.sentinel.health.changed"

# -- Security ----------------------------------------------------------------
MONITORING_BACKGROUND_PRINCIPAL_ID = "kortex-monitoring-system"
PERMISSION_MONITORING_READ = "system:monitoring:read"
MONITORING_SECURITY_CLASSIFICATION = "INTERNAL"
MONITORING_ENGINE_VERSION = "1.0.0"

# -- Cardinality and Label Bounds --------------------------------------------
MAX_METRIC_NAMES: int = 200
MAX_ACTIVE_SERIES: int = 500
MAX_LABELS_PER_SERIES: int = 5
MAX_LABEL_VALUE_LENGTH: int = 64

# Strict whitelist of permitted label keys for diagnostic decomposition
ALLOWED_LABEL_KEYS: frozenset[str] = frozenset(
    {
        "subsystem",
        "driver",
        "status",
        "error_type",
        "action_type",
        "severity",
        "entity_type",
    }
)

# Metric naming pattern: dotted lowercase alphanumeric with underscores
METRIC_NAME_PATTERN: str = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"
METRIC_NAME_REGEX: re.Pattern[str] = re.compile(METRIC_NAME_PATTERN)

# -- Retention and Histogram Defaults ----------------------------------------
HISTOGRAM_RESERVOIR_SIZE: int = 1000
DEFAULT_COLLECT_INTERVAL_SECONDS: float = 10.0
DEFAULT_RETENTION_POINTS: int = 360  # 360 * 10s = 3600s (60 minutes)
PER_ENGINE_TIMEOUT_SECONDS: float = 1.0

# -- Threshold Evaluation Defaults -------------------------------------------
DEFAULT_HYSTERESIS_PERCENT: float = 0.10  # 10%
DEFAULT_COOLDOWN_SECONDS: float = 60.0
CONSECUTIVE_CYCLES_REQUIRED: int = 2

# Engine name for self-exclusion
MONITORING_ENGINE_NAME = "monitoring"
