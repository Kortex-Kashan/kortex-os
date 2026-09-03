"""KORTEX Sentinel Engine — Constants.

Defines canonical capability names, event topics, security permissions,
and operational defaults for Phase 7 Sentinel Engine.
"""

from __future__ import annotations

# Capabilities
CAPABILITY_HEALTH_GET = "kortex.sentinel.health.get"
CAPABILITY_STATUS_GET = "kortex.sentinel.status.get"
CAPABILITY_DIAGNOSTICS_GET = "kortex.sentinel.diagnostics.get"

SENTINEL_CAPABILITIES = (
    CAPABILITY_HEALTH_GET,
    CAPABILITY_STATUS_GET,
    CAPABILITY_DIAGNOSTICS_GET,
)

# Event Topics
TOPIC_HEALTH_CHANGED = "kortex.sentinel.health.changed"
TOPIC_SUBSYSTEM_FAILED = "kortex.sentinel.subsystem.failed"
TOPIC_SUBSYSTEM_RECOVERED = "kortex.sentinel.subsystem.recovered"
TOPIC_DEADLOCK_DETECTED = "kortex.sentinel.deadlock.detected"
TOPIC_CRASH_LOOP_DETECTED = "kortex.sentinel.crash_loop.detected"
TOPIC_RECOVERY_REQUESTED = "kortex.sentinel.recovery.requested"

SENTINEL_EVENTS = (
    TOPIC_HEALTH_CHANGED,
    TOPIC_SUBSYSTEM_FAILED,
    TOPIC_SUBSYSTEM_RECOVERED,
    TOPIC_DEADLOCK_DETECTED,
    TOPIC_CRASH_LOOP_DETECTED,
    TOPIC_RECOVERY_REQUESTED,
)

# Security & Permissions
SENTINEL_PERMISSION_READ = "system:sentinel:read"
SENTINEL_SYSTEM_PRINCIPAL_ID = "kortex-sentinel-system"
SENTINEL_SYSTEM_ROLE = "system_sentinel"
SENTINEL_SECURITY_CLASSIFICATION = "INTERNAL"

# Engine Identity
ENGINE_NAME = "sentinel"
ENGINE_VERSION = "1.0.0"

# Defaults
DEFAULT_MONITOR_INTERVAL_SECONDS = 30.0
DEFAULT_HEARTBEAT_WARNING_MULTIPLIER = 2.0
DEFAULT_HEARTBEAT_FAILURE_MULTIPLIER = 3.0
DEFAULT_LOOP_LAG_THRESHOLD_MS = 1000.0
DEFAULT_OPERATION_TIMEOUT_SECONDS = 60.0
DEFAULT_STARTUP_GRACE_SECONDS = 30.0
DEFAULT_CRASH_LOOP_THRESHOLD = 3
DEFAULT_CRASH_LOOP_WINDOW_SECONDS = 600.0
DEFAULT_RECOVERY_COOLDOWN_SECONDS = 60.0
DEFAULT_RING_BUFFER_SIZE = 100
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0
