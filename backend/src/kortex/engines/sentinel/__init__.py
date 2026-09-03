"""KORTEX Sentinel — System health monitoring, invariant integrity, and liveness observation.

Phase 7 Production Hardening engine responsible for:
- Layered health classification over EngineState
- Architectural invariant integrity verification
- Non-invasive deadlock suspicion and event loop starvation detection
- Explicit heartbeat & watchdog tracking
- Deterministic failure classification and recovery request handoff
"""

from kortex.engines.sentinel.constants import (
    CAPABILITY_DIAGNOSTICS_GET,
    CAPABILITY_HEALTH_GET,
    CAPABILITY_STATUS_GET,
    ENGINE_NAME,
    ENGINE_VERSION,
    SENTINEL_CAPABILITIES,
    SENTINEL_EVENTS,
    SENTINEL_PERMISSION_READ,
    TOPIC_CRASH_LOOP_DETECTED,
    TOPIC_DEADLOCK_DETECTED,
    TOPIC_HEALTH_CHANGED,
    TOPIC_RECOVERY_REQUESTED,
    TOPIC_SUBSYSTEM_FAILED,
    TOPIC_SUBSYSTEM_RECOVERED,
)
from kortex.engines.sentinel.engine import SentinelEngine
from kortex.engines.sentinel.interfaces import IHeartbeatSource
from kortex.engines.sentinel.models import (
    CheckStatus,
    DeadlockReport,
    FailureClass,
    IntegrityReport,
    ProbeResult,
    SentinelConfig,
    SentinelDiagnosticsReport,
    SentinelHealthReport,
    SentinelStatus,
    SentinelStatusReport,
    StalledOperation,
    SubsystemHealth,
)

__all__ = [
    "CAPABILITY_DIAGNOSTICS_GET",
    "CAPABILITY_HEALTH_GET",
    "CAPABILITY_STATUS_GET",
    "ENGINE_NAME",
    "ENGINE_VERSION",
    "SENTINEL_CAPABILITIES",
    "SENTINEL_EVENTS",
    "SENTINEL_PERMISSION_READ",
    "TOPIC_CRASH_LOOP_DETECTED",
    "TOPIC_DEADLOCK_DETECTED",
    "TOPIC_HEALTH_CHANGED",
    "TOPIC_RECOVERY_REQUESTED",
    "TOPIC_SUBSYSTEM_FAILED",
    "TOPIC_SUBSYSTEM_RECOVERED",
    "CheckStatus",
    "DeadlockReport",
    "FailureClass",
    "IHeartbeatSource",
    "IntegrityReport",
    "ProbeResult",
    "SentinelConfig",
    "SentinelDiagnosticsReport",
    "SentinelEngine",
    "SentinelHealthReport",
    "SentinelStatus",
    "SentinelStatusReport",
    "StalledOperation",
    "SubsystemHealth",
]
