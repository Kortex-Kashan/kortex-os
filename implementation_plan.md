# Final Implementation Plan — Phase 7: Production Hardening — Monitoring Engine

**Item**: Phase 7 — Production Hardening — Monitoring Engine (Metrics, Dashboards, and Operational Telemetry)  
**Phase**: Phase 7 (Production Hardening)  
**Status**: PLANNED (Awaiting Owner Authorization for Implementation)  
**Document**: `implementation_plan.md` (and canonical architecture document `docs/architecture/monitoring_engine_implementation_plan.md`)  
**Architectural Authority**: KORTEX Architecture v1.0.0, ADR-0015 (proposed)  
**Reconciliation Control**: `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §5.3  

---

## 1. Executive Summary

The Monitoring Engine is the centralized operational telemetry, metric aggregation, time-series retention, and dashboard query layer for KORTEX OS. It complements the Sentinel Engine:
- **Sentinel Engine** answers: *"Is something unhealthy or broken?"* (Discrete states, invariant verification, failure classification, crash-loop circuit breaker, recovery requests).
- **Monitoring Engine** answers: *"What is happening, how often, how fast, how much resource is being consumed, and what operational state should the operator see?"* (Continuous telemetry, counters, gauges, latency percentiles, throughput rates, rolling time-series buffers, and consolidated dashboard views).

The engine operates under strict local-first architectural discipline: 100% in-memory bounded state, zero database migrations, zero external network dependencies, strictly non-invasive polling with cooperative timeouts, and fail-closed RBAC access control.

---

## 2. Mission

### 2.1 What Monitoring Engine MUST Do
1. **Collect Continuous Metrics**: Periodically sweep registered system engines and business modules that implement `IEngineDiagnostics`, extracting operational throughput, error counters, and latencies.
2. **Standardize Metric Primitives**: Provide thread-safe `Counter`, `Gauge`, `Histogram` (with p50, p90, p95, p99 percentile estimations), and `Timer` primitives.
3. **Retain Bounded Time-Series**: Maintain rolling in-memory circular buffers (last 60 minutes at 10-second resolution, max 360 samples per series) for UI charting.
4. **Collect System Telemetry**: Measure process RSS memory, CPU usage, active thread counts, and active asyncio tasks using pure Python standard library capabilities.
5. **Aggregate Operational Dashboard**: Serve consolidated runtime dashboard payloads combining Sentinel health, engine throughput, resource consumption, and active alerts.
6. **Evaluate Operational Thresholds**: Evaluate metrics against warning and critical boundaries with hysteresis to emit clean alert events without storming.
7. **Enforce Security Boundaries**: Guard all capabilities with strict authentication, execution context validation, `INTERNAL` classification, and `system:monitoring:read` permission.
8. **Own Lifecycle Cleanly**: Follow `BaseEngine` lifecycle contracts, starting and stopping background tasks cleanly without leaking coroutines or polling recursively.

### 2.2 What Monitoring Engine MUST NOT Do
1. **MUST NOT Execute Recovery**: Monitoring never restarts engines, kills workers, terminates processes, or executes recovery logic (strict BaseEngine boundary).
2. **MUST NOT Duplicate Sentinel Authority**: Monitoring does not evaluate subsystem health states or crash-loop circuit breakers. Sentinel owns health classification; Monitoring queries Sentinel and displays health in dashboard views.
3. **MUST NOT Persist to Database**: Zero database tables, zero Alembic migrations. All state is strictly ephemeral and in-memory.
4. **MUST NOT Export to External SaaS**: No Prometheus pushgateways, Datadog agents, or cloud OpenTelemetry exporters in the local-first runtime.
5. **MUST NOT Cause Alert Storms**: No periodic snapshot broadcast spam; threshold events use hysteresis and cooldown.
6. **MUST NOT Cause Memory Leaks**: Hard cardinality caps and circular ring buffers prevent unbounded memory growth.
7. **MUST NOT Block Startup or Shutdown**: Collector operations use per-engine timeouts (1.0s) and clean task cancellation.

---

## 3. Non-Goals

- **No Distributed Tracing**: No Jaeger or Zipkin distributed trace collectors.
- **No Long-term Analytics Warehouse**: No DuckDB, ClickHouse, or TimescaleDB integrations; historical analysis belongs to external tooling if ever exported.
- **No Direct Desktop UI Code**: Monitoring Engine provides backend capability API contracts; it contains zero React, Vue, or Tauri UI code.
- **No Business Logic Mutation**: Monitoring cannot trigger workflows, approve documents, or mutate business entities.

---

## 4. Current Repository Findings

1. **Package Stub**: `backend/src/kortex/engines/monitoring/__init__.py` exists as an empty 2-line docstring (`"KORTEX Monitoring Engine — Metrics collection, dashboards, and alerting."`). Zero other source files exist.
2. **Diagnostics Interface**: `IEngineDiagnostics` is formally defined in `backend/src/kortex/engines/storage/interfaces.py` and implemented across all engines:
   - `health() -> dict[str, Any]`
   - `metrics() -> dict[str, Any]`
   - `diagnostics() -> dict[str, Any]`
   - `status() -> str`
   - `version() -> str`
   - `capabilities() -> list[str]`
3. **Existing Real Implementations**:
   - `ConnectorDiagnostics`: Records `total_executions`, `successful_executions`, `failed_executions`, `total_latency_ms`, `average_latency_ms`, `per_driver_executions`, `per_action_type_executions`.
   - `SentinelDiagnostics`: Records `checks_run`, `deadlock_inspections`, `starvation_events`, `integrity_failures`, `recovery_requests_emitted`, `crash_loops_detected`, `tracked_operations`.
   - `WorkflowEngine`: Records instance executions, step durations, and scheduler state.
   - `SecurityEngine`: Records authentication counts, token issuance, and authorization checks.
4. **Kernel Registration**: `Kernel.get_all_engines() -> dict[str, BaseEngine]` provides a clean, public, non-reflective mechanism to discover all running engines.
5. **Dependencies**: `psutil` is NOT in `pyproject.toml`. Process metrics must use Python standard library (`ctypes`, `resource`, `gc`, `os`, `asyncio`, `threading`) with optional `psutil` fallback if present.

---

## 5. Graphify Findings

- **Knowledge Graph State**: 15,516 nodes, 36,230 edges, 502 communities.
- **Freshness**: `built_at_commit == 2df91c5c` (synchronized with HEAD).
- **Hub Analysis**: `BaseEngine`, `Kernel`, `IEngineDiagnostics`, and `SentinelEngine` form the primary structural hubs connecting runtime lifecycle, health evaluation, and operational monitoring.
- **Isolation Verification**: No reverse imports from business modules or future engines exist into the monitoring namespace.

---

## 6. Dependency Graph

```
                                  +-------------------+
                                  |      Kernel       |
                                  +---------+---------+
                                            |
                         +------------------+------------------+
                         |                                     |
                         v                                     v
               +-------------------+                 +-------------------+
               |  SecurityEngine   |                 |    EventEngine    |
               | (Auth & RBAC)     |                 |  (Alert Routing)  |
               +---------+---------+                 +---------+---------+
                         |                                     |
                         v                                     v
+-------------------------------------------------------------------------+
|                            MonitoringEngine                             |
|                                                                         |
|  +-------------------+  +--------------------+  +--------------------+  |
|  |  MetricsCollector |  |   MetricRegistry   |  |  TimeSeriesBuffer  |  |
|  +---------+---------+  +---------+----------+  +---------+----------+  |
|            |                      |                       |             |
|            |                      +-----------+-----------+             |
|            |                                  |                         |
|            v                                  v                         |
|  +-------------------+              +--------------------+              |
|  | ThresholdEvaluator|              | CapabilityHandlers |              |
|  +---------+---------+              +---------+----------+              |
+------------|----------------------------------|-------------------------+
             |                                  |
             v (publishes alerts)               v (serves queries)
    [kortex.monitoring.*]              [Desktop UI / Operator]
             ^
             | (reads health)
   +---------+---------+
   |  SentinelEngine   |
   | (Health Authority)|
   +-------------------+
```

### Dependency Rules:
- **Allowed Inward Dependencies**: `core` (`Kernel`, `BaseEngine`, `Container`), `constants`, `models`, `storage.interfaces.IEngineDiagnostics`, `security` (for RBAC models and exceptions).
- **Forbidden Outward Dependencies**:
  - `kortex.modules.*` (Finance, HR, Operations)
  - `kortex.engines.recovery.*`
  - `kortex.engines.backup.*`
  - `kortex.engines.update.*`
  - `apps.desktop.*`

---

## 7. Architecture & Component Design

The Monitoring Engine is divided into modular, single-responsibility components:

```
backend/src/kortex/engines/monitoring/
├── __init__.py          # Public exports (MonitoringEngine, models, constants)
├── constants.py         # Capability names, event topics, thresholds, limits
├── interfaces.py        # IMonitoringEngine, IMetricRegistry, ITimeSeriesBuffer
├── models.py            # Pydantic models (MetricType, MetricValue, DashboardData, Config)
├── metrics.py           # Metric primitives (Counter, Gauge, Histogram, Timer)
├── registry.py          # MetricRegistry container with cardinality enforcement
├── timeseries.py        # TimeSeriesBuffer with bounded rolling deques
├── collector.py         # MetricsCollector background polling task
├── thresholds.py        # ThresholdEvaluator with hysteresis and cooldown
├── events.py            # MonitoringEventPublisher
├── engine.py            # MonitoringEngine(BaseEngine, IEngineDiagnostics)
├── diagnostics.py       # MonitoringDiagnostics adapter conforming to IEngineDiagnostics
└── README.md            # Architectural documentation
```

### 7.1 Component Roles
1. **`MetricRegistry`**: Thread-safe registry holding instantiated metric primitives (`Counter`, `Gauge`, `Histogram`, `Timer`). Enforces strict cardinality limits (max 200 metric names, max 500 total series).
2. **`TimeSeriesBuffer`**: Manages rolling circular buffers (`collections.deque(maxlen=360)`) per tracked metric. Provides time-slice queries (`get_history(metric_name, window_seconds)`).
3. **`MetricsCollector`**: Background task executing at `collect_interval_seconds` (default: 10s).
   - Iterates through `Kernel.get_all_engines()`.
   - Skips `"monitoring"` (self-exclusion).
   - Invokes `engine.metrics()` with a strict 1.0-second timeout per engine.
   - Collects system-level resource stats (memory, CPU, tasks, threads).
   - Normalizes data and updates `MetricRegistry` and `TimeSeriesBuffer`.
4. **`ThresholdEvaluator`**: Compares current metrics against configured rules (`WARNING`, `CRITICAL`). Implements state tracking, hysteresis, and cooldown to eliminate alert flapping.
5. **`MonitoringEventPublisher`**: Publishes threshold alerts to `Kernel.publish_event` with unique UUIDv4 IDs and UTC timestamps.
6. **`MonitoringEngine`**: Coordinates lifecycle, IoC registration, capability registration, capability execution, and clean background task cancellation.

---

## 8. Metric Model & Primitives

### 8.1 Metric Types
1. **`Counter`**:
   - **Semantics**: Monotonically increasing numerical value.
   - **Update**: `inc(amount: float = 1.0)`. Rejects negative numbers (`ValueError`).
   - **Reset**: Resets to 0 only on explicit engine reset.
   - **Thread Safety**: Backed by `threading.Lock`.
2. **`Gauge`**:
   - **Semantics**: Instantaneous numerical value representing current state.
   - **Update**: `set(value: float)`, `inc(amount: float = 1.0)`, `dec(amount: float = 1.0)`.
   - **Thread Safety**: Backed by `threading.Lock`.
3. **`Histogram`**:
   - **Semantics**: Statistical distribution of numeric samples (latencies, sizes).
   - **Reservoir Model**: Sliding-window reservoir of the last $N = 1,000$ samples.
   - **Percentile Calculation**: Linear interpolation between closest ranks on sorted reservoir:
     - Rank formula: $R = \frac{P}{100} \times (N - 1)$
     - Interpolation: $V = Y_{\lfloor R \rfloor} + (R - \lfloor R \rfloor) \times (Y_{\lceil R \rceil} - Y_{\lfloor R \rfloor})$
   - **Computed Fields**: `count`, `sum`, `min`, `max`, `avg`, `p50`, `p90`, `p95`, `p99`.
   - **Empty Window**: When `count == 0`, percentiles return `None`, min/max return `None`, avg returns `0.0`.
4. **`Timer`**:
   - **Semantics**: Measures execution durations in milliseconds.
   - **Usage**: Context manager `with timer.time(): ...` updating underlying Histogram and Counter.

### 8.2 Cardinality & Label Rules
- **Name Convention**: Lowercase dotted alphanumeric: `[a-z][a-z0-9_]*(\.[a-z0-9_]+)*` (e.g. `connector.executions.total`).
- **Permitted Label Keys**: `subsystem`, `driver`, `status`, `error_type`, `action_type`.
- **Max Label Count**: 5 labels per metric.
- **Max Label Value Length**: 64 characters (values exceeding 64 chars are truncated with `...`).
- **Global Metric Limits**:
  - Max metric names: 200.
  - Max series across all labels: 500.
  - When limit is exceeded, new series are rejected and a `cardinality_limit_exceeded` metric is incremented.

---

## 9. Time-Series Model & Retention

- **Sampling Interval**: 10 seconds.
- **Retention Duration**: 3,600 seconds (60 minutes).
- **Buffer Capacity**: $3,600 / 10 = 360$ points per series.
- **Storage Structure**: `collections.deque(maxlen=360)` storing tuples of `(timestamp_utc_iso: str, value: float)`.
- **Pacing Mechanism**:
  - Background collector uses `time.monotonic()` for interval calculation to prevent drift.
  - Recorded point timestamps use UTC ISO-8601 strings (`datetime.now(timezone.utc).isoformat()`).
- **Memory Footprint**:
  - ~32 bytes per point $\times$ 360 points = ~11.5 KB per metric series.
  - 100 active series = ~1.15 MB total memory.
  - Strict bounded memory invariant guaranteed.

---

## 10. Collection Architecture & Concurrency

### 10.1 Diagnostic Sweeping (`IEngineDiagnostics`)
```python
async def collect_engine_metrics(self) -> None:
    engines = self._kernel.get_all_engines()
    for name, engine in engines.items():
        if name == "monitoring":
            continue  # Explicit public self-exclusion

        if isinstance(engine, IEngineDiagnostics):
            try:
                # 1.0s timeout ensures slow/hanging diagnostics never block monitoring
                raw_metrics = await asyncio.wait_for(
                    asyncio.to_thread(engine.metrics),
                    timeout=1.0,
                )
                self._normalize_and_record(name, raw_metrics)
            except asyncio.TimeoutError:
                self._record_collection_error(name, "timeout")
            except Exception as exc:
                self._record_collection_error(name, f"exception: {exc}")
```

### 10.2 System Resource Telemetry
- **RSS Memory**:
  - Windows: `ctypes.windll.kernel32.GetProcessMemoryInfo` (ProcessMemoryCounters.WorkingSetSize).
  - Linux/POSIX: `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024`.
  - Fallback: `tracemalloc.get_traced_memory()[0]`.
- **CPU Percent**:
  - Calculated over interval using `os.times()` (user + system time delta divided by wall time delta).
- **AsyncIO Tasks**: `len(asyncio.all_tasks())`.
- **Active Threads**: `threading.active_count()`.

### 10.3 Failure Isolation
- A timeout or exception in one engine's `metrics()` method is logged at `WARNING` and increments `collection_failures_total`.
- The collector immediately proceeds to the next engine; collector loop never crashes.

---

## 11. Normalization Layer

To handle heterogeneous payloads from existing and future engines:
1. **Scalar Numbers**: Floats and integers are validated with `math.isfinite()`. `NaN`, `+Inf`, and `-Inf` are dropped.
2. **Booleans**: Normalized to `1.0` (`True`) or `0.0` (`False`) if tracked as gauges.
3. **Nested Dictionaries**: Flattened into tagged series if key matches a permitted label (e.g. `{"per_driver_executions": {"postgres": 5}}` becomes series `connector.executions{driver="postgres"} = 5`).
4. **Strings / Complex Objects**: Retained only in raw diagnostics snapshots; excluded from numeric time-series buffers.
5. **Units**:
   - Durations: Normalized to milliseconds (`ms`).
   - Memory: Normalized to bytes (`bytes`).
   - Counts: Cumulative integers (`total`).

---

## 12. Thresholds & Alert Model

### 12.1 Threshold Rules
- Configured rules evaluate against numeric gauges or histogram percentiles.
- Attributes: `metric_name`, `operator` (`>`, `>=`, `<`, `<=`), `warning_threshold`, `critical_threshold`, `hysteresis_percentage` (default: 10%), `cooldown_seconds` (default: 60s).

### 12.2 Alert State Machine
```
[NORMAL] ──(exceeds critical for 2 cycles)──> [CRITICAL ALERT] (emits threshold.exceeded)
    │                                                │
    │                                                v (drops below hysteresis for 2 cycles)
    └──(recovers)────────────────────────────── [NORMAL] (emits threshold.recovered)
```
- **Hysteresis**: To recover from a warning/critical state, value must return past threshold by at least 10% (e.g., if memory threshold is 1,000 MB, recovery requires dropping below 900 MB).
- **Suppression**: Events are emitted ONCE per state transition. Flapping is suppressed.

---

## 13. Event Model

Exactly TWO event topics are implemented:

| Event Topic | Trigger | Payload Summary | Priority |
|---|---|---|---|
| `kortex.monitoring.threshold.exceeded` | Metric crosses threshold for 2 consecutive evaluations | `metric_name`, `current_value`, `threshold`, `severity` (`WARNING`/`CRITICAL`), `subsystem`, `timestamp` | HIGH |
| `kortex.monitoring.threshold.recovered`| Metric recovers below threshold (with hysteresis) | `metric_name`, `current_value`, `threshold`, `subsystem`, `timestamp` | NORMAL |

- **Periodic Snapshot Event Decision**: `kortex.monitoring.snapshot.emitted` is explicitly **REMOVED**. Dashboard queries are pull-based via capability; emitting periodic 60-second broadcast events creates unnecessary event bus traffic in a local-first system.
- **Event Properties**: Fresh UUIDv4 `event_id`, UTC ISO-8601 `timestamp`, preserved `correlation_id`, bounded payloads (< 1 KB), no secrets.

---

## 14. Capabilities & API Contracts

Exactly FOUR read-only capabilities are registered:

### 14.1 `kortex.monitoring.metrics.get`
- **Purpose**: Retrieve current metric values across all engines or a filtered subsystem.
- **Request**: `{"subsystem": Optional[str], "metric_names": Optional[list[str]]}`
- **Response**:
  ```json
  {
    "timestamp": "2026-09-04T08:00:00Z",
    "subsystems": {
      "connector": {
        "executions_total": 150,
        "failed_executions_total": 2,
        "average_latency_ms": 14.2
      }
    },
    "system": {
      "memory_rss_bytes": 104857600,
      "cpu_percent": 2.4,
      "active_tasks": 12,
      "active_threads": 4
    }
  }
  ```

### 14.2 `kortex.monitoring.timeseries.get`
- **Purpose**: Retrieve historical rolling data points for charting in desktop UI.
- **Request**: `{"metric_name": str, "duration_seconds": Optional[int] = 3600}` (capped at 3,600)
- **Response**:
  ```json
  {
    "metric_name": "system.memory_rss_bytes",
    "duration_seconds": 3600,
    "point_count": 360,
    "points": [
      {"timestamp": "2026-09-04T07:00:00Z", "value": 98566144},
      {"timestamp": "2026-09-04T07:00:10Z", "value": 98697216}
    ]
  }
  ```

### 14.3 `kortex.monitoring.dashboard.get`
- **Purpose**: Consolidated operational view combining Sentinel health, engine throughput, resource metrics, and active alerts.
- **Request**: `{}`
- **Response**:
  ```json
  {
    "timestamp": "2026-09-04T08:00:00Z",
    "uptime_seconds": 3600.0,
    "health": {
      "status": "HEALTHY",
      "failed_subsystems": [],
      "degraded_subsystems": []
    },
    "resources": {
      "memory_rss_bytes": 104857600,
      "cpu_percent": 2.4,
      "active_tasks": 12,
      "active_threads": 4
    },
    "top_metrics": [
      {"subsystem": "connector", "metric": "executions_total", "value": 150},
      {"subsystem": "workflow", "metric": "instances_active", "value": 3}
    ],
    "active_alerts": []
  }
  ```

### 14.4 `kortex.monitoring.diagnostics.get`
- **Purpose**: Conforming `IEngineDiagnostics` output for Monitoring Engine itself.
- **Request**: `{}`
- **Response**: Standard diagnostics contract (`engine`, `version`, `state`, `metrics`, `diagnostics`).

---

## 15. Security & Tenancy

### 15.1 Security Attributes
- **System Principal**: `kortex-monitoring-system`.
- **Classification**: `INTERNAL`.
- **Permission**: `system:monitoring:read`.
- **Access Control**:
  - `requires_authentication=True`
  - `requires_execution_context=True`
  - Unauthenticated requests fail-closed with `AuthenticationError`.
  - Unauthorized callers lacking `system:monitoring:read` fail-closed with `AuthorizationDeniedError`.

### 15.2 Tenancy Model
- Runtime infrastructure metrics (CPU, RAM, engine latencies, error counts) are **system-wide operational telemetry**.
- Metrics do NOT contain tenant identifiers, PII, or customer payload content.
- Capability queries return system-level aggregates. No caller-controlled tenant parameters exist.

---

## 16. Persistence Decision

### 16.1 NO MIGRATION
- **Verdict**: Strictly zero database tables, zero Alembic migrations.
- **Rationale**:
  - High-frequency operational metrics (sampled every 10 seconds) written to SQLite would cause persistent disk I/O, database write locks, WAL file bloat, and vacuum contention.
  - The in-memory circular ring buffer (`collections.deque(maxlen=360)`) provides instantaneous queries with zero disk overhead and automatic FIFO eviction.
  - Ephemeral state across restarts is expected and desirable for live operational metrics.

---

## 17. Configuration Model (`MonitoringConfig`)

Validated via Pydantic settings:
- `KORTEX_MONITORING_COLLECT_INTERVAL_SECONDS`: float = 10.0 (bounds: 1.0 to 60.0)
- `KORTEX_MONITORING_BUFFER_MAX_POINTS`: int = 360 (bounds: 60 to 1440)
- `KORTEX_MONITORING_ENABLED`: bool = True
- `KORTEX_MONITORING_PROBE_TIMEOUT_SECONDS`: float = 1.0 (bounds: 0.2 to 5.0)
- `KORTEX_MONITORING_MEMORY_WARNING_THRESHOLD_MB`: float = 1024.0
- `KORTEX_MONITORING_MEMORY_CRITICAL_THRESHOLD_MB`: float = 2048.0
- `KORTEX_MONITORING_EVENT_LOOP_LAG_WARNING_SECONDS`: float = 0.5
- `KORTEX_MONITORING_EVENT_LOOP_LAG_CRITICAL_SECONDS`: float = 1.5

---

## 18. Lifecycle & Bootstrap

### 18.1 Bootstrap Sequence
In `backend/src/kortex/api/kernel_bootstrap.py`:
```python
# Phase 7 — Production Hardening
kernel.register_engine(SentinelEngine())
kernel.register_engine(MonitoringEngine())
await kernel.boot()
```

### 18.2 Lifecycle Implementation
- `initialize()`: Sets up registry, allocates deques, registers capabilities with Kernel.
- `start()`: Spawns `_collector_loop` in `_background_tasks`.
- `stop()`: Cancels `_collector_loop`, awaits completion with timeout (3.0s), marks state `STOPPED`.
- `reset()`: Clears in-memory buffers and counters.

---

## 19. Failure Modes & Mitigations Matrix

| Failure Mode | Detection | System Response | Isolation / Mitigation |
|---|---|---|---|
| **Engine `metrics()` hangs** | `asyncio.wait_for(timeout=1.0)` | Aborts call, records timeout metric | Collector continues to next engine; monitoring loop never blocks |
| **Engine `metrics()` throws** | `except Exception` in collector | Logs warning, increments `collection_failures_total` | Isolated per-engine; remaining engines collected normally |
| **Malformed metric values (NaN/Inf)** | `math.isfinite(val) is False` | Value rejected, warning logged | Time-series buffer remains clean and numeric |
| **Cardinality explosion** | Series count $\ge 500$ | Rejects new series, increments `cardinality_rejections` | Memory usage remains strictly bounded |
| **Sentinel unavailable / not booted** | `_kernel.get_engine("sentinel")` raises `ResourceNotFoundError` | Dashboard marks health as `UNKNOWN` | Dashboard still serves system resource and engine metrics |
| **EventEngine unavailable** | Event publish raises exception | Logs warning, does not fail collection | Internal metrics collection unaffected by event bus failures |
| **Rapid restart / stop during collection** | `asyncio.CancelledError` received | Flushes current cycle, exits loop immediately | Clean shutdown guaranteed within 3.0 seconds |

---

## 20. Self-Observability of Monitoring

To prevent recursive monitoring, Monitoring Engine exposes its own metrics directly through its `IEngineDiagnostics` implementation:
- `collector_cycles_total`: Cumulative collection sweeps.
- `collector_duration_last_ms`: Duration of the last sweep.
- `collector_duration_avg_ms`: Rolling average collection duration.
- `collection_failures_total`: Count of failed engine metric extractions.
- `tracked_metrics_count`: Number of distinct metrics registered.
- `buffer_points_total`: Total data points held across all deques.
- `active_alerts_count`: Number of active threshold breaches.

---

## 21. Testing Plan

### 21.1 Unit Tests
1. `backend/tests/unit/test_monitoring_primitives.py`:
   - `Counter`: Monotonic increment, non-negative enforcement, reset.
   - `Gauge`: Set, inc, dec, bounds.
   - `Histogram`: Reservoir sampling, p50/p90/p95/p99 percentiles, min/max/avg, empty-window behavior.
   - `Timer`: Context manager duration measurement.
2. `backend/tests/unit/test_monitoring_registry.py`:
   - Registration, name formatting validation, duplicate handling.
   - Cardinality enforcement: rejecting series beyond limit (500).
   - Label truncation (> 64 chars) and max labels (5).
3. `backend/tests/unit/test_monitoring_timeseries.py`:
   - Deque bounded FIFO eviction (never exceeds 360 points).
   - Time-range queries (`duration_seconds`), ordering, empty buffers.
4. `backend/tests/unit/test_monitoring_thresholds.py`:
   - Warning and critical threshold crossings.
   - Hysteresis recovery logic (prevents alert flapping).
   - Cooldown and duplicate suppression.
5. `backend/tests/unit/test_monitoring_collector.py`:
   - Polling `IEngineDiagnostics` across mock engines.
   - Self-exclusion (verifying `"monitoring"` is never polled).
   - Per-engine timeout isolation (1.0s).
   - System resource collection (RSS, CPU, tasks, threads).
6. `backend/tests/unit/test_monitoring_engine.py`:
   - Lifecycle transitions (`UNINITIALIZED` → `READY` → `RUNNING` → `STOPPED`).
   - Capability registration and handlers.
   - Diagnostics protocol conformance (`IEngineDiagnostics`).

### 21.2 Integration Tests
1. `backend/tests/integration/test_monitoring_integration.py`:
   - Full Kernel boot with `MonitoringEngine` registered.
   - Capability dispatch for all 4 capabilities via `kernel.invoke_capability()`.
   - Security verification:
     - Unauthenticated requests raise `AuthenticationError`.
     - Unauthorized requests (missing `system:monitoring:read`) raise `AuthorizationDeniedError`.
   - Live dashboard verification combining Sentinel health and engine metrics.
   - Clean shutdown verification (no dangling background tasks).

### 21.3 Regression Invariant
- Execute full backend test suite:
  `NEW FAILURE NODE IDs CAUSED BY MONITORING = 0` against the accepted baseline of 19 pre-existing failures.

---

## 22. File-Level Implementation Map

| File Path | Action | Description & Key Symbols | Dependencies | Covering Tests |
|---|---|---|---|---|
| `backend/src/kortex/engines/monitoring/__init__.py` | Modify | Public exports: `MonitoringEngine`, models, constants | `engine.py`, `models.py` | Unit & Integration |
| `backend/src/kortex/engines/monitoring/constants.py` | Create | Canonical capability names, event topics, default thresholds, limits | None | `test_monitoring_engine.py` |
| `backend/src/kortex/engines/monitoring/interfaces.py` | Create | Protocols: `IMonitoringEngine`, `IMetricRegistry`, `ITimeSeriesBuffer` | `models.py` | `test_monitoring_primitives.py` |
| `backend/src/kortex/engines/monitoring/models.py` | Create | Pydantic models: `MetricType`, `MetricValue`, `MetricSnapshot`, `DashboardData`, `MonitoringConfig` | `pydantic` | `test_monitoring_primitives.py` |
| `backend/src/kortex/engines/monitoring/metrics.py` | Create | Thread-safe primitives: `Counter`, `Gauge`, `Histogram`, `Timer` | `models.py` | `test_monitoring_primitives.py` |
| `backend/src/kortex/engines/monitoring/registry.py` | Create | `MetricRegistry` with thread-safe storage & cardinality enforcement | `metrics.py`, `constants.py` | `test_monitoring_registry.py` |
| `backend/src/kortex/engines/monitoring/timeseries.py` | Create | `TimeSeriesBuffer` managing bounded deques (`maxlen=360`) | `models.py`, `constants.py` | `test_monitoring_timeseries.py` |
| `backend/src/kortex/engines/monitoring/collector.py` | Create | `MetricsCollector` polling `IEngineDiagnostics` with timeouts & system telemetry | `registry.py`, `timeseries.py` | `test_monitoring_collector.py` |
| `backend/src/kortex/engines/monitoring/thresholds.py` | Create | `ThresholdEvaluator` with hysteresis and alert state machine | `models.py`, `events.py` | `test_monitoring_thresholds.py` |
| `backend/src/kortex/engines/monitoring/events.py` | Create | `MonitoringEventPublisher` for threshold events | `core.events` | `test_monitoring_thresholds.py` |
| `backend/src/kortex/engines/monitoring/diagnostics.py` | Create | `MonitoringDiagnostics(IEngineDiagnostics)` adapter | `storage.interfaces` | `test_monitoring_engine.py` |
| `backend/src/kortex/engines/monitoring/engine.py` | Create | `MonitoringEngine(BaseEngine, IEngineDiagnostics)` | `collector.py`, `thresholds.py` | `test_monitoring_engine.py`, integration |
| `backend/src/kortex/engines/monitoring/README.md` | Create | Engine documentation, capabilities, events, metrics | None | Documentation |
| `backend/src/kortex/api/kernel_bootstrap.py` | Modify | Register `MonitoringEngine` right after `SentinelEngine` | `monitoring.engine` | `test_monitoring_integration.py` |
| `docs/adr/ADR-0015-phase7-monitoring-engine.md` | Create | Architecture Decision Record for Phase 7 Monitoring Engine | None | Architectural documentation |

---

## 23. Sequencing / Order of Implementation

1. **Phase A: Core Models & Primitives** (`constants.py`, `models.py`, `interfaces.py`, `metrics.py`, `test_monitoring_primitives.py`).
2. **Phase B: Registry & Time-Series Buffer** (`registry.py`, `timeseries.py`, `test_monitoring_registry.py`, `test_monitoring_timeseries.py`).
3. **Phase C: Collector & Normalization** (`collector.py`, `test_monitoring_collector.py`).
4. **Phase D: Thresholds & Events** (`thresholds.py`, `events.py`, `test_monitoring_thresholds.py`).
5. **Phase E: Engine & Diagnostics Protocol** (`engine.py`, `diagnostics.py`, `test_monitoring_engine.py`).
6. **Phase F: Bootstrap & Integration** (`kernel_bootstrap.py`, `test_monitoring_integration.py`).
7. **Phase G: Verification & Documentation** (`README.md`, ADR-0015, Ruff, Mypy, full-suite regression, Graphify).

---

## 24. Risks & Mitigations

1. **Risk**: Slow or hanging engine `metrics()` call delays entire collection cycle.  
   **Mitigation**: Enforce strict `asyncio.wait_for(timeout=1.0)` per engine.
2. **Risk**: High-frequency metric ingestion causes memory growth.  
   **Mitigation**: Bounded deques (`maxlen=360`) and strict series cardinality limit (500 series max).
3. **Risk**: Rapid metric fluctuations cause alert storms.  
   **Mitigation**: 10% hysteresis threshold and 60-second cooldown per metric alert.
4. **Risk**: OS portability differences in system resource measurements.  
   **Mitigation**: Dedicated platform branches (Windows ctypes, POSIX resource) with pure Python fallbacks.

---

## 25. Open Decisions & Owner Visibility

### 25.1 Owner Decisions (Architectural Scope)
- **Decision 1 (In-Memory vs Persistent Metrics)**:  
  *Recommendation*: Retain 100% in-memory bounded rolling buffers (NO MIGRATION). Persistent storage creates unnecessary disk I/O and DB contention in local-first desktop deployments.  
  *Status*: Fully validated and specified in plan; owner approval formalizes implementation.
- **Decision 2 (Alert Event Scope)**:  
  *Recommendation*: Emit only threshold transition events (`threshold.exceeded`, `threshold.recovered`); eliminate periodic snapshot broadcast events.  
  *Status*: Fully validated and specified in plan.

### 25.2 Implementation Details (Technical Discretion)
- Collection interval default: 10.0 seconds.
- Buffer retention window: 60 minutes (360 points).
- Permitted label keys: `subsystem`, `driver`, `status`, `error_type`.
- Percentile calculation: linear interpolation between closest ranks.
