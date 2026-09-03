"""KORTEX Sentinel Engine — Domain Models and Enums.

Defines the domain contracts, enums, health classifications, and diagnostic models
for system health observation, invariant verification, and failure handoff.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from kortex.engines.sentinel.constants import (
    DEFAULT_CRASH_LOOP_THRESHOLD,
    DEFAULT_CRASH_LOOP_WINDOW_SECONDS,
    DEFAULT_HEARTBEAT_FAILURE_MULTIPLIER,
    DEFAULT_HEARTBEAT_WARNING_MULTIPLIER,
    DEFAULT_LOOP_LAG_THRESHOLD_MS,
    DEFAULT_MONITOR_INTERVAL_SECONDS,
    DEFAULT_OPERATION_TIMEOUT_SECONDS,
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    DEFAULT_RECOVERY_COOLDOWN_SECONDS,
    DEFAULT_RING_BUFFER_SIZE,
    DEFAULT_STARTUP_GRACE_SECONDS,
)


class SentinelStatus(str, enum.Enum):
    """Aggregate and subsystem operational health classification.

    Layered over engine lifecycle states (EngineState) without replacing them.
    """

    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    STOPPING = "STOPPING"
    DISABLED = "DISABLED"


class CheckStatus(str, enum.Enum):
    """Status for an individual probe or integrity verification check."""

    PASS = "PASS"  # noqa: S105
    WARN = "WARN"
    FAIL = "FAIL"


class FailureClass(str, enum.Enum):
    """Deterministic classification of detected failure or anomaly."""

    TRANSIENT = "TRANSIENT"
    REPEATED = "REPEATED"
    PERSISTENT = "PERSISTENT"
    CRASH_LOOP = "CRASH_LOOP"
    EVENT_LOOP_STARVATION = "EVENT_LOOP_STARVATION"
    STALLED_OPERATION = "STALLED_OPERATION"
    DEADLOCK_SUSPECTED = "DEADLOCK_SUSPECTED"


class ProbeResult(BaseModel):
    """Result of an individual check or probe."""

    model_config = ConfigDict(frozen=True)

    probe_name: str
    status: CheckStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    is_required: bool = True
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubsystemHealth(BaseModel):
    """Health classification and probe results for a single subsystem."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: SentinelStatus
    engine_state: str | None = None
    probes: dict[str, ProbeResult] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))


class StalledOperation(BaseModel):
    """Details of an observed operation exceeding its execution threshold."""

    model_config = ConfigDict(frozen=True)

    operation_id: str
    name: str
    duration_seconds: float
    threshold_seconds: float
    details: dict[str, Any] = Field(default_factory=dict)


class DeadlockReport(BaseModel):
    """Report from event loop responsiveness and stalled operation analysis."""

    model_config = ConfigDict(frozen=True)

    deadlock_suspected: bool
    starvation_detected: bool
    loop_lag_ms: float
    stalled_operations: list[StalledOperation] = Field(default_factory=list)
    active_operations_count: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntegrityReport(BaseModel):
    """Aggregated runtime integrity report covering invariant checks."""

    model_config = ConfigDict(frozen=True)

    overall_status: SentinelStatus
    passed: int
    warnings: int
    failures: int
    checks: list[ProbeResult]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SentinelHealthReport(BaseModel):
    """Aggregated system health report across KORTEX subsystems."""

    model_config = ConfigDict(frozen=True)

    status: SentinelStatus
    healthy: bool
    kernel_state: str
    subsystems: dict[str, SubsystemHealth] = Field(default_factory=dict)
    database_connected: bool
    event_engine_available: bool
    loop_lag_ms: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SentinelStatusReport(BaseModel):
    """High-level operational status report returned by kortex.sentinel.status.get."""

    model_config = ConfigDict(frozen=True)

    status: SentinelStatus
    engine: str = "sentinel"
    version: str = "1.0.0"
    uptime_seconds: float
    active_tasks: int
    tracked_operations: int
    registered_heartbeats: int
    subsystems_summary: dict[str, str] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SentinelDiagnosticsReport(BaseModel):
    """Detailed technical diagnostics report returned by kortex.sentinel.diagnostics.get."""

    model_config = ConfigDict(frozen=True)

    status: SentinelStatus
    version: str = "1.0.0"
    config: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    recent_incidents: list[dict[str, Any]] = Field(default_factory=list)
    deadlock_report: DeadlockReport | None = None
    integrity_report: IntegrityReport | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SentinelConfig(BaseModel):
    """Configuration options for Sentinel Engine."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    monitor_interval_seconds: float = Field(default=DEFAULT_MONITOR_INTERVAL_SECONDS, gt=0.0)
    heartbeat_warning_multiplier: float = Field(default=DEFAULT_HEARTBEAT_WARNING_MULTIPLIER, gt=1.0)
    heartbeat_failure_multiplier: float = Field(default=DEFAULT_HEARTBEAT_FAILURE_MULTIPLIER, gt=1.0)
    loop_lag_threshold_ms: float = Field(default=DEFAULT_LOOP_LAG_THRESHOLD_MS, gt=0.0)
    operation_timeout_seconds: float = Field(default=DEFAULT_OPERATION_TIMEOUT_SECONDS, gt=0.0)
    startup_grace_seconds: float = Field(default=DEFAULT_STARTUP_GRACE_SECONDS, ge=0.0)
    crash_loop_threshold: int = Field(default=DEFAULT_CRASH_LOOP_THRESHOLD, ge=2)
    crash_loop_window_seconds: float = Field(default=DEFAULT_CRASH_LOOP_WINDOW_SECONDS, gt=0.0)
    recovery_cooldown_seconds: float = Field(default=DEFAULT_RECOVERY_COOLDOWN_SECONDS, gt=0.0)
    ring_buffer_size: int = Field(default=DEFAULT_RING_BUFFER_SIZE, ge=10, le=1000)
    probe_timeout_seconds: float = Field(default=DEFAULT_PROBE_TIMEOUT_SECONDS, gt=0.0)
    enable_background_monitor: bool = True
