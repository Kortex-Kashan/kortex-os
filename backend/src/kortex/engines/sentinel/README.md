# KORTEX Sentinel Engine

## Overview

The **Sentinel Engine** (`Phase 7 — Production Hardening — Sentinel Engine`) is KORTEX OS's core observability and health sentinel. It provides non-invasive health evaluation, architectural invariant verification, heartbeat watchdog tracking, event-loop lag detection, deterministic failure classification, and event-driven recovery request handoff.

Sentinel operates strictly under KORTEX OS Clean Architecture and the AI Engineering Constitution:
- **Observation Only**: Sentinel observes, detects, and emits structured failure events. It **never** executes recovery, terminates processes, or restarts engines.
- **Infrastructure Engine**: As an infrastructure engine, Sentinel contains no business logic.
- **Ephemeral State**: Sentinel uses an in-memory bounded ring buffer (maximum 100 entries) with deterministic FIFO eviction. It introduces **no database migrations** and owns no persistent tables.

---

## Architecture & Responsibilities

```
                                  +-----------------------+
                                  |     KORTEX Kernel     |
                                  +-----------+-----------+
                                              |
                          +-------------------+-------------------+
                          |                                       |
                +---------v-----------+                 +---------v-----------+
                |    SentinelEngine   |                 |     EventEngine     |
                +---------+-----------+                 +---------^-----------+
                          |                                       |
    +---------------------+---------------------+                 |
    |                     |                     |                 |
+---v-------------+ +-----v-----------+ +-------v---------+       |
| HeartbeatManager| |DeadlockDetector | |IntegrityVerifier|       |
| (IHeartbeatSrc) | |(Lag & Starve)   | |(Kernel & DB)    |       |
+---+-------------+ +-----+-----------+ +-------+---------+       |
    |                     |                     |                 |
    +---------------------+---------------------+                 |
                          |                                       |
                +---------v-----------+                           |
                | Sentinel Diagnostics|                           |
                |   & Incident Store  |                           |
                | (Circuit Breaker)   |                           |
                +---------+-----------+                           |
                          |                                       |
                          +--- Emits Canonical Events ------------+
```

### Key Components

1. **`SentinelEngine` (`engine.py`)**:
   Main engine subclassing `BaseEngine` and implementing `IEngineDiagnostics`. Manages lifecycle, background monitoring loop, subsystem polling with self-exclusion, and capability handlers.

2. **`HeartbeatManager` (`heartbeats.py`)**:
   Manages explicit component heartbeats via `IHeartbeatSource`. Tracks liveness using monotonic time (`time.monotonic()`), deterministic warning ($2\times$) and failure ($3\times$) thresholds, and startup/shutdown immunity.

3. **`DeadlockDetector` & `OperationTracker` (`deadlock.py`)**:
   Measures event-loop lag via cooperative scheduling yields (`await asyncio.sleep(0)`). Tracks active long-running operations. Distinguishes `EVENT_LOOP_STARVATION` from `DEADLOCK_SUSPECTED` without private attribute reflection.

4. **`IntegrityVerifier` (`integrity.py`)**:
   Verifies system invariants including Kernel runtime state, engine dependencies, capability descriptors, Event Engine availability, and database connectivity via non-mutating session ping.

5. **`IncidentStore` & Circuit Breaker (`incident.py`)**:
   Maintains a thread-safe bounded ring buffer of recent diagnostic incidents. Tracks crash-loop episodes ($\ge 3$ failures within $600\text{s}$) and provides the Recovery Request Emission Circuit Breaker to prevent event storms during cooldown.

6. **`SentinelEventPublisher` (`events.py`)**:
   Constructs and publishes the 6 canonical Sentinel events onto the Kernel Event Engine with UUIDv4 event IDs, correlation IDs, UTC timestamps, and deterministic idempotency keys.

---

## Health Classification Model

Sentinel introduces `SentinelStatus` as an aggregate operational health classification layered on top of, and distinct from, `EngineState`:

| `SentinelStatus` | Description |
| :--- | :--- |
| `STARTING` | Subsystem or Kernel is within its startup grace window or initializing. |
| `HEALTHY` | Subsystem is operational and all required health probes pass. |
| `DEGRADED` | Subsystem is operational but experiencing non-fatal probe warnings or starvation. |
| `FAILED` | Subsystem has encountered a fatal health failure or unexpected stoppage. |
| `UNKNOWN` | Diagnostic evidence is unavailable, stale, or indeterminate. |
| `STOPPING` | Subsystem or Kernel is executing graceful shutdown. |
| `DISABLED` | Subsystem is intentionally stopped, uninstalled, or disabled by configuration. |

### `EngineState.STOPPED` Mapping Policy

- If Kernel is `RUNNING` and an engine enters `EngineState.STOPPED`: mapped to `SentinelStatus.FAILED` (unexpected stoppage).
- If Kernel is `SHUTTING_DOWN` or `STOPPED`: mapped to `SentinelStatus.DISABLED`.

---

## Canonical Capabilities

Sentinel registers exactly three read-only, authenticated, internal capabilities:

| Capability Name | Security | Permissions | Description |
| :--- | :--- | :--- | :--- |
| `kortex.sentinel.health.get` | `INTERNAL` | `system:sentinel:read` | Returns `SentinelHealthReport` covering aggregate and subsystem health. |
| `kortex.sentinel.status.get` | `INTERNAL` | `system:sentinel:read` | Returns `SentinelStatusReport` with uptime, active tasks, and status summaries. |
| `kortex.sentinel.diagnostics.get` | `INTERNAL` | `system:sentinel:read` | Returns `SentinelDiagnosticsReport` with metrics, config, and recent incidents. |

---

## Canonical Event Family

Sentinel emits exactly six standardized event topics:

| Event Topic | Priority | Description |
| :--- | :--- | :--- |
| `kortex.sentinel.health.changed` | `HIGH` | Emitted when system aggregate health transitions between statuses. |
| `kortex.sentinel.subsystem.failed` | `CRITICAL` | Emitted when a subsystem enters `FAILED` status. |
| `kortex.sentinel.subsystem.recovered` | `NORMAL` | Emitted when a previously failed/degraded subsystem returns to `HEALTHY`. |
| `kortex.sentinel.deadlock.detected` | `CRITICAL` | Emitted when event loop lag combined with multiple stalled operations suggests deadlock. |
| `kortex.sentinel.crash_loop.detected` | `CRITICAL` | Emitted when a subsystem fails $\ge 3$ times within the observation window. |
| `kortex.sentinel.recovery.requested` | `HIGH` | Emitted to hand off a recovery request to external Recovery Engine, carrying a deterministic `idempotency_key`. |

---

## Configuration

Sentinel configuration is encapsulated by `SentinelConfig`:

```python
from kortex.engines.sentinel.models import SentinelConfig

config = SentinelConfig(
    enabled=True,
    monitor_interval_seconds=30.0,
    heartbeat_warning_multiplier=2.0,
    heartbeat_failure_multiplier=3.0,
    loop_lag_threshold_ms=1000.0,
    operation_timeout_seconds=60.0,
    startup_grace_seconds=30.0,
    crash_loop_threshold=3,
    crash_loop_window_seconds=600.0,
    recovery_cooldown_seconds=60.0,
    ring_buffer_size=100,
    probe_timeout_seconds=5.0,
    enable_background_monitor=True,
)
```
