# Final Implementation Plan — Phase 7: Production Hardening — Monitoring Engine

**Item**: Phase 7 — Production Hardening — Monitoring Engine (Metrics, Dashboards, and Operational Telemetry)  
**Phase**: Phase 7 (Production Hardening)  
**Status**: PLANNED (Awaiting Owner Authorization for Implementation)  
**Document**: `docs/architecture/monitoring_engine_implementation_plan.md` (and canonical workspace copy `implementation_plan.md`)  
**Architectural Authority**: KORTEX Architecture v1.0.0, ADR-0015 (proposed)  
**Reconciliation Control**: `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §5.3  

---

## 1. Executive Summary

The Monitoring Engine is the centralized operational telemetry, metric aggregation, time-series retention, and dashboard query layer for KORTEX OS. It is the observational and numerical peer to the Sentinel Engine:
- **Sentinel Engine** answers: *"Is something unhealthy or broken?"* (Discrete health states, invariant verification, failure classification, crash-loop circuit breaker, recovery requests).
- **Monitoring Engine** answers: *"What is happening, how often, how fast, how much resource is being consumed, and what operational state should the operator see?"* (Continuous telemetry, counters, gauges, latency percentiles, throughput rates, rolling time-series buffers, and consolidated dashboard views).

The engine operates under strict local-first architectural discipline: 100% in-memory bounded state, zero database migrations, zero external network dependencies, strictly non-invasive polling with cooperative timeouts, and fail-closed RBAC access control.

---

## 2. Mission

### 2.1 What Monitoring Engine MUST Do
1. **Collect Continuous Metrics**: Periodically sweep registered system engines and business modules implementing `IEngineDiagnostics`, extracting operational throughput, error counters, and latencies.
2. **Standardize Metric Primitives**: Provide thread-safe `Counter`, `Gauge`, `Histogram` (with approximate p50, p90, p95, p99 percentile estimations via sliding-window reservoir), and `Timer` primitives.
3. **Retain Bounded Time-Series**: Maintain rolling in-memory circular buffers (last 60 minutes at 10-second resolution, max 360 samples per series) for UI charting.
4. **Collect System Telemetry**: Measure process RSS memory, CPU usage, active thread counts, and active asyncio tasks using pure Python standard library capabilities.
5. **Aggregate Operational Dashboard**: Serve consolidated runtime dashboard payloads combining Sentinel health, engine throughput, resource consumption, and active alerts using direct internal component aggregation (no recursive capability dispatch).
6. **Evaluate Operational Thresholds**: Evaluate metrics against warning and critical boundaries with 10% hysteresis and 60-second cooldown to emit clean alert events without flapping.
7. **Enforce Security Boundaries**: Guard all capabilities with strict authentication, execution context validation, `INTERNAL` classification, and `system:monitoring:read` permission for operator callers, while using `kortex-monitoring-system` solely for internal background operations.
8. **Own Lifecycle Cleanly**: Follow `BaseEngine` lifecycle contracts, starting and stopping background tasks cleanly without leaking coroutines or polling recursively.

### 2.2 What Monitoring Engine MUST NOT Do
1. **MUST NOT Execute Recovery**: Monitoring never restarts engines, kills workers, terminates processes, or executes recovery logic (strict BaseEngine boundary).
2. **MUST NOT Duplicate Sentinel Authority**: Monitoring does not evaluate subsystem health states or crash-loop circuit breakers. Sentinel owns health classification; Monitoring queries Sentinel and displays health in dashboard views.
3. **MUST NOT Persist to Database**: Zero database tables, zero Alembic migrations. All state is strictly ephemeral and in-memory.
4. **MUST NOT Export to External SaaS**: No Prometheus pushgateways, Datadog agents, or cloud OpenTelemetry exporters in the local-first runtime.
5. **MUST NOT Cause Alert Storms**: No periodic snapshot broadcast spam; threshold events use hysteresis and cooldown.
6. **MUST NOT Cause Memory Leaks**: Hard cardinality caps (max 200 metric names, max 500 series) and circular ring buffers prevent unbounded memory growth.
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
5. **Dependencies**: `psutil` is NOT in `pyproject.toml`. Process metrics must use Python standard library (`ctypes`, `resource`, `os`, `asyncio`, `threading`) with optional `psutil` fallback if present.

---

## 5. Graphify Findings

- **Knowledge Graph State**: 15,546 nodes, 36,260 edges, 556 communities.
- **Freshness**: `built_at_commit == 2bb9db4a` (synchronized with HEAD).
- **Hub Analysis**: `BaseEngine`, `Kernel`, `IEngineDiagnostics`, and `SentinelEngine` form the primary structural hubs connecting runtime lifecycle, health evaluation, and operational monitoring.
- **Isolation Verification**: No reverse imports from business modules or future engines exist into the monitoring namespace.

---

## 6. Dependency Graph & Sentinel Interaction

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
             | (reads via public interface / event cache)
   +---------+---------+
   |  SentinelEngine   |
   | (Health Authority)|
   +-------------------+
```

### 6.1 Architectural Resolution: Monitoring ↔ Sentinel Dependency
1. **How Monitoring Obtains Sentinel State**:
   - Monitoring subscribes to Sentinel's public event `kortex.sentinel.health.changed` upon startup, maintaining an in-memory cached health summary (`_last_known_sentinel_health`).
   - For on-demand dashboard composition, Monitoring looks up Sentinel via the public Kernel registry: `sentinel = self._kernel.get_all_engines().get("sentinel")`.
   - If present, it queries the public `IEngineDiagnostics` contract: `sentinel.health()`.
   - Monitoring **NEVER** imports `SentinelEngine` private implementation classes, private state, or internal detectors.
2. **Why This Is Architecturally Safe**:
   - **Zero Circular Dependency**: Sentinel evaluates engine lifecycle health via standard `BaseEngine.health_check()` across all engines (skipping `"sentinel"`). Monitoring collects operational metrics via `IEngineDiagnostics.metrics()` across all engines (skipping `"monitoring"`).
   - Neither engine's polling loop invokes the other's polling loop.
3. **Behavior When Sentinel Is Unavailable**:
   - If Sentinel is not registered, not booted, or disabled, Monitoring functions 100% normally:
     - Dashboard health block gracefully defaults to:
       ```json
       {
         "status": "UNKNOWN",
         "source": "sentinel_absent",
         "message": "Sentinel Engine is not registered or not booted."
       }
       ```
     - System resource metrics, engine throughput metrics, and time-series buffers continue operating without interruption.
4. **Independent Operation**:
   - Sentinel runs independently without Monitoring.
   - Monitoring runs independently without Sentinel.

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
├── normalizer.py        # DiagnosticsNormalizer mapping IEngineDiagnostics to canonical metrics
├── thresholds.py        # ThresholdEvaluator with hysteresis and alert state machine
├── events.py            # MonitoringEventPublisher
├── engine.py            # MonitoringEngine(BaseEngine, IEngineDiagnostics)
├── diagnostics.py       # MonitoringDiagnostics adapter conforming to IEngineDiagnostics
└── README.md            # Architectural documentation
```

---

## 8. Metric Model & Primitives

### 8.1 Metric Types
1. **`Counter`**:
   - **Semantics**: Monotonically increasing numerical value representing cumulative occurrences.
   - **Update**: `inc(amount: float = 1.0)`. Amount must be strictly non-negative (`amount >= 0.0`); negative values raise `ValueError`.
   - **Reset**: Resets to 0 only on explicit engine reset.
   - **Thread Safety**: Protected by `threading.Lock`.
2. **`Gauge`**:
   - **Semantics**: Instantaneous numerical value representing current state (can increase, decrease, or remain constant).
   - **Update**: `set(value: float)`, `inc(amount: float = 1.0)`, `dec(amount: float = 1.0)`. Value must be finite (`math.isfinite(value)`).
   - **Thread Safety**: Protected by `threading.Lock`.
3. **`Histogram`**:
   - **Semantics**: Statistical distribution of numeric samples over a sliding window.
   - **Reservoir Model**: Per-series circular buffer of the last $N = 1,000$ samples (`collections.deque(maxlen=1000)`).
   - **Statistical Meaning**: Percentiles are **approximate statistical estimations** representing the retained 1,000-sample population, NOT exact lifetime percentiles.
   - **Calculation Method**: Linear rank interpolation over sorted reservoir:
     - Rank: $R = \frac{P}{100} \times (n - 1)$
     - Let $k = \lfloor R \rfloor$, $d = R - k$.
     - Percentile: $V = s_k + d \times (s_{k+1} - s_k)$ (if $k+1 < n$, else $s_k$).
   - **Edge Cases**:
     - $n = 0$: `count = 0`, `sum = 0.0`, `min = None`, `max = None`, `avg = 0.0`, all percentiles return `None`.
     - $n = 1$: `count = 1`, `sum = s_0`, `min = s_0`, `max = s_0`, `avg = s_0`, all percentiles return `s_0`.
     - $1 < n < 100$: Rank interpolation is mathematically valid; $p99$ evaluates near maximum.
   - **Concurrency**: `record(value: float)` is protected by `threading.Lock`. Snapshot generation copies samples under lock and sorts outside the lock to minimize lock contention.
4. **`Timer`**:
   - **Semantics**: Measures execution durations in milliseconds.
   - **Usage**: Context manager `with timer.time(): ...` updating underlying Histogram and Counter.

---

## 9. Normalization Contract (`IEngineDiagnostics`)

Existing engines return heterogeneous data in `metrics()` and `health()`. The `DiagnosticsNormalizer` component enforces a deterministic translation without requiring any changes to existing source engines:

### 9.1 Three Distinct Data Categories
1. **Canonical Metric**: Extracted scalar numeric measurements (`int`, `float`) conforming to:
   - Dotted lowercase naming: `[a-z][a-z0-9_]*(\.[a-z0-9_]+)*`
   - Source prefix: Automatically prepended if missing (e.g. `connector.executions_total`).
   - Finite numeric validation: Checked via `math.isfinite()`. `NaN`, `+Infinity`, and `-Infinity` are **strictly rejected** (dropped with warning and increment of `normalization_rejections_total`).
2. **Diagnostic Metadata**: Engine state strings, semantic version strings, capability lists, and timestamps:
   - Stored in engine metadata dictionary; **NEVER** inserted into numerical time-series buffers.
3. **Arbitrary Diagnostic Payload**: Complex, nested, or non-numeric structures:
   - **Simple String-to-Number Mappings** (e.g. `{"per_driver_executions": {"postgres": 10, "http": 5}}`):
     Extracted into canonical metric series with labels if key matches permitted label keys:
     `metric_name="connector.driver_executions_total"`, `labels={"driver": "postgres"}`, `value=10.0`.
   - **Booleans**: Normalized to `1.0` (`True`) or `0.0` (`False`) as Gauges.
   - **`None` Values**: Discarded from the current cycle (no data point recorded; **NEVER silently converted to 0.0**).
   - **Strings, Lists, Complex Objects**: Preserved in raw diagnostics snapshot; omitted from numeric series.
   - **Metric Collisions**: If an engine emits duplicate metric keys, the first valid numeric key takes precedence, and a collision warning is recorded.

---

## 10. Cardinality & Label Limits

### 10.1 Limits & Collision Prevention
- **Series Identity**: A series is uniquely identified by `series_id = f"{metric_name}{{{','.join(f'{k}={v}' for k, v in sorted(labels.items()))}}}"`.
- **Label Value Validation**:
  - `MAX_LABEL_VALUE_LENGTH = 64`.
  - **NO TRUNCATION**: Blind truncation can collapse distinct series (e.g. `tenant_long_name_alpha` and `tenant_long_name_beta`) into identical strings, creating false series collisions.
  - If a label value exceeds 64 characters or contains invalid characters (control characters, whitespace), the metric series is **rejected deterministically** with an error log and an increment of `cardinality_rejections_total`.
- **Allowed Label Keys Whitelist**: `{"subsystem", "driver", "status", "error_type", "action_type", "severity", "entity_type"}`. Any unknown label key causes the label to be omitted.
- **Maximum Labels per Series**: 5.
- **Maximum Metric Names**: 200.
- **Maximum Active Series**: 500 across all labels.
  - When the 501st series is registered, it is rejected and `cardinality_limit_exceeded` is incremented.

### 10.2 Calculable Memory Upper Bound
- Max active series: 500.
- Max points per series deque: 360 points.
- Max points in memory: $500 \times 360 = 180,000$ points.
- Point tuple `(timestamp_float, value_float)`: ~72 bytes overhead in Python.
- $180,000 \times 72\text{ bytes} \approx 12.96\text{ MB}$.
- Deque and series metadata: ~500 KB.
- Histogram reservoirs (max 20 histograms $\times$ 1000 floats): ~160 KB.
- **Absolute Maximum Upper Bound**: **$< 15.0\text{ MB}$** under 100% capacity saturation.
- **Typical Operational Footprint**: **$< 2.0\text{ MB}$** (50 series $\times$ 360 points).

---

## 11. Time-Series Retention Model

- **Sampling Interval**: 10.0 seconds.
- **Retention Duration**: 3,600 seconds (60 minutes).
- **Buffer Capacity**: $3,600 / 10 = 360$ points per series.
- **Data Structure**: `collections.deque(maxlen=360)` storing `(timestamp_utc_iso: str, value: float)`.
- **Pacing Mechanism**: Collector loop uses `time.monotonic()` for interval calculation to prevent drift. Point timestamps use UTC ISO-8601 strings.
- **Eviction**: Automatic FIFO eviction by deque when the 361st point arrives.

---

## 12. System Resource Telemetry

To preserve local-first portability without introducing heavy third-party dependencies, system telemetry uses 100% Python standard library mechanisms:

| Metric Name | Mandatory? | Unit | Collection Method (Pure Standard Library) | Fallback Behavior |
|---|---|---|---|---|
| `system.memory_rss_bytes` | Mandatory | Bytes | Windows: `ctypes.windll.kernel32.K32GetProcessMemoryInfo`<br>Linux/POSIX: `resource.getrusage(RUSAGE_SELF).ru_maxrss * 1024` | `tracemalloc.get_traced_memory()[0]` if OS call fails |
| `system.cpu_percent` | Mandatory | % | Measured delta over sampling interval using `os.times()`: $\frac{\Delta (\text{user}+\text{sys})}{\Delta \text{wall}} \times 100.0$ | Returns `0.0` on first cycle |
| `system.asyncio_tasks_active` | Mandatory | Count | `len(asyncio.all_tasks())` | Pure Python stdlib; never fails |
| `system.threads_active` | Mandatory | Count | `threading.active_count()` | Pure Python stdlib; never fails |
| `system.event_loop_lag_ms` | Optional | ms | Read from `SentinelEngine.last_deadlock_report.loop_lag_ms` if present; fallback to `asyncio.sleep(0)` delta | Returns `None` if loop measurement unavailable |

- **Third-Party Dependency Decision**: `psutil` is **NOT REQUIRED**. All mandatory metrics are collected using standard library `ctypes` (Windows), `resource` (POSIX), and `os`/`asyncio`/`threading`. If `psutil` happens to be installed in the environment, Monitoring may optionally use it as an optimization, but never requires it.

---

## 13. Security & Dual Identity Context

Security is strictly separated into two distinct operational contexts:

### 13.1 Context A: Internal Background Execution
- **Identity**: `kortex-monitoring-system` (`principal_type = PrincipalType.SYSTEM`, `clearance_level = "INTERNAL"`).
- **Usage**: Used exclusively by MonitoringEngine for internal background operations:
  - Polling background engines.
  - Publishing threshold alert events (`sender="monitoring"`).
  - Internal audit logging.
- **Isolation**: External callers can NEVER assume or impersonate `kortex-monitoring-system`.

### 13.2 Context B: Human / Operator API Queries
- **Identity**: Calling user's authenticated principal (e.g. `principal_id = "admin-1"`).
- **Enforcement**:
  - `requires_authentication=True`: Rejects unauthenticated calls with `AuthenticationError`.
  - `requires_execution_context=True`: Execution context is supplied by SecurityEngine.
  - `security_classification="INTERNAL"`: Requires internal clearance level.
  - `required_permissions=["system:monitoring:read"]`: Evaluated against caller's roles. Rejects unauthorized calls with `AuthorizationDeniedError`.
- **Tenancy**: Metrics are system-wide infrastructure statistics without tenant PII. No caller-controlled tenant parameters exist.

---

## 14. Capabilities & Internal Dashboard Composition

Exactly FOUR capabilities are implemented:

| Capability Name | Purpose | Request Parameters | Max Result Bound |
|---|---|---|---|
| `kortex.monitoring.metrics.get` | Granular real-time metric query across engines | `subsystem: Optional[str]`, `metric_names: Optional[list[str]]` | Max 200 metrics, $< 10\text{ KB}$ |
| `kortex.monitoring.timeseries.get` | Time-series historical data points for UI charts | `metric_name: str`, `duration_seconds: Optional[int] = 3600` | Max 360 points, $< 15\text{ KB}$ |
| `kortex.monitoring.dashboard.get` | High-level unified operational summary | `{}` | Max 10 top metrics, 20 engines, $< 20\text{ KB}$ |
| `kortex.monitoring.diagnostics.get` | Standard `IEngineDiagnostics` conformance for Monitoring | `{}` | Standard diagnostics schema |

### 14.1 Dashboard Internal Composition (No Capability Dispatch)
- `handle_dashboard_get` **NEVER** dispatches internal requests to `metrics.get` or `timeseries.get` via `Kernel.invoke_capability`.
- It directly accesses internal Python memory objects:
  - System telemetry from `self._collector.last_system_telemetry`
  - Top throughput metrics from `self._registry.get_top_metrics(limit=10)`
  - Active alerts from `self._threshold_evaluator.get_active_alerts()`
  - Sentinel health summary from `self._get_sentinel_health_summary()`
- Direct, fast, in-memory aggregation (< 5ms response time) with deterministic alphabetical ordering.

---

## 15. Threshold Authority & State Machine

### 15.1 Strict Separation of Authority
- **Monitoring Engine**:
  - `OBSERVE` $\rightarrow$ `MEASURE` $\rightarrow$ `EVALUATE THRESHOLD` $\rightarrow$ `EMIT OPERATIONAL ALERT`.
  - Concerns: High CPU, high memory, high latency, queue depth.
  - Monitoring does NOT declare an engine `FAILED` and does NOT request recovery.
- **Sentinel Engine**:
  - `ASSESS HEALTH` $\rightarrow$ `VERIFY INVARIANTS` $\rightarrow$ `CLASSIFY FAILURE` $\rightarrow$ `REQUEST RECOVERY`.
  - Concerns: Crashed engines, broken database connection, deadlocks, crash-loops.
- **Recovery Engine (Future)**:
  - `EXECUTE RECOVERY` (restarts, rollbacks, fallbacks).

### 15.2 Alert State Machine & Hysteresis
- **States**: `NORMAL`, `WARNING`, `CRITICAL`.
- **Consecutive Cycle Verification**: Value must exceed threshold for **2 consecutive cycles (20s)** before triggering an alert. Transient single-cycle spikes are ignored.
- **10% Hysteresis Justification**: If memory threshold is 1,024 MB, a value oscillating between 1,023 MB and 1,025 MB would cause alert flapping every 10s. With 10% hysteresis, recovery requires dropping below $1,024 \times (1 - 0.10) = 921.6\text{ MB}$.
- **60-Second Cooldown**: Once an alert is emitted, repeated events are suppressed for at least 60 seconds while the state remains unchanged.
- **Recovery Event**: When the value recovers below the hysteresis threshold for 2 consecutive cycles, a single `kortex.monitoring.threshold.recovered` event is emitted.

---

## 16. Event Model

Exactly TWO event topics are implemented:

| Event Topic | Trigger | Payload Attributes | Priority |
|---|---|---|---|
| `kortex.monitoring.threshold.exceeded` | Metric exceeds threshold for 2 consecutive cycles | `metric_name`, `current_value`, `threshold`, `severity` (`WARNING`/`CRITICAL`), `subsystem`, `timestamp` | HIGH |
| `kortex.monitoring.threshold.recovered`| Metric drops below hysteresis threshold for 2 consecutive cycles | `metric_name`, `current_value`, `threshold`, `subsystem`, `timestamp` | NORMAL |

- **Removal of Snapshot Events**: `kortex.monitoring.snapshot.emitted` was critically evaluated and **permanently eliminated**. Polling via `dashboard.get` provides on-demand visibility; emitting periodic 60-second broadcast events pollutes the event bus.
- **Event Properties**: UUIDv4 `event_id`, UTC ISO-8601 `timestamp`, preserved `correlation_id`, strictly bounded payload (< 1 KB), no secrets.

---

## 17. Persistence Decision

### 17.1 NO MIGRATION
- **Verdict**: Strictly zero database tables, zero Alembic revisions.
- **Rationale**: High-frequency operational metrics (sampled every 10s) written to SQLite would cause continuous disk I/O, database write locks, WAL file growth, and vacuum contention. Bounded circular in-memory deques provide instantaneous query response, zero disk overhead, and automatic FIFO eviction.
- **State Ephemerality**: Operational metrics reset on application restart by design.

---

## 18. Configuration Model (`MonitoringConfig`)

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

## 19. Lifecycle & Bootstrap

### 19.1 Bootstrap Sequence
In `backend/src/kortex/api/kernel_bootstrap.py`:
```python
# Phase 7 — Production Hardening
kernel.register_engine(SentinelEngine())
kernel.register_engine(MonitoringEngine())
await kernel.boot()
```

### 19.2 Lifecycle Implementation
- `initialize()`: Sets up registry, allocates deques, registers capabilities with Kernel.
- `start()`: Spawns `_collector_loop` in `_background_tasks`.
- `stop()`: Cancels `_collector_loop`, awaits completion with timeout (3.0s), marks state `STOPPED`.
- `reset()`: Clears in-memory buffers and counters.

---

## 20. Failure Modes & Mitigations Matrix

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

## 21. Self-Observability of Monitoring

To prevent recursive monitoring, Monitoring Engine exposes its own metrics directly through its `IEngineDiagnostics` implementation:
- `collector_cycles_total`: Cumulative collection sweeps.
- `collector_duration_last_ms`: Duration of the last sweep.
- `collector_duration_avg_ms`: Rolling average collection duration.
- `collection_failures_total`: Count of failed engine metric extractions.
- `tracked_metrics_count`: Number of distinct metrics registered.
- `buffer_points_total`: Total data points held across all deques.
- `active_alerts_count`: Number of active threshold breaches.

---

## 22. Testing Plan

### 22.1 Unit Tests
1. `backend/tests/unit/test_monitoring_primitives.py`:
   - `Counter`: Monotonic increment, non-negative enforcement, reset.
   - `Gauge`: Set, inc, dec, bounds.
   - `Histogram`: Sliding-window reservoir sampling (1,000 max), linear rank interpolation for p50/p90/p95/p99, edge cases (0 samples, 1 sample, small sample), thread safety.
   - `Timer`: Context manager duration measurement.
2. `backend/tests/unit/test_monitoring_registry.py`:
   - Registration, name formatting validation, duplicate handling.
   - Cardinality enforcement: rejecting series beyond limit (500).
   - Label validation: whitelist enforcement, rejecting values > 64 chars without truncation collisions.
3. `backend/tests/unit/test_monitoring_normalizer.py`:
   - Normalizing scalar numbers, booleans to 1.0/0.0, simple nested dict breakdowns.
   - Rejecting `NaN`, `+Inf`, `-Inf`, and invalid strings.
   - `None` handling (skipping point without converting to 0.0).
4. `backend/tests/unit/test_monitoring_timeseries.py`:
   - Deque bounded FIFO eviction (never exceeds 360 points).
   - Time-range queries (`duration_seconds`), ordering, empty buffers.
5. `backend/tests/unit/test_monitoring_thresholds.py`:
   - Warning and critical threshold crossings (2-cycle requirement).
   - 10% hysteresis recovery logic.
   - 60s cooldown and duplicate suppression.
6. `backend/tests/unit/test_monitoring_collector.py`:
   - Polling `IEngineDiagnostics` across mock engines.
   - Self-exclusion (verifying `"monitoring"` is never polled).
   - Per-engine timeout isolation (1.0s).
   - Pure stdlib system resource collection (RSS, CPU, tasks, threads).
7. `backend/tests/unit/test_monitoring_engine.py`:
   - Lifecycle transitions (`UNINITIALIZED` → `READY` → `RUNNING` → `STOPPED`).
   - Capability registration and handlers.
   - Diagnostics protocol conformance (`IEngineDiagnostics`).
   - Sentinel dependency decoupling (graceful `UNKNOWN` when Sentinel absent).

### 22.2 Integration Tests
1. `backend/tests/integration/test_monitoring_integration.py`:
   - Full Kernel boot with `MonitoringEngine` registered.
   - Capability dispatch for all 4 capabilities via `kernel.invoke_capability()`.
   - Security verification:
     - Unauthenticated requests raise `AuthenticationError`.
     - Unauthorized requests (missing `system:monitoring:read`) raise `AuthorizationDeniedError`.
     - Operator caller identity preserved in execution context.
   - Direct internal dashboard composition verification (no nested capability dispatch).
   - Live dashboard verification combining Sentinel health and engine metrics.
   - Clean shutdown verification (no dangling background tasks).

### 22.3 Regression Invariant
- Execute full backend test suite:
  `NEW FAILURE NODE IDs CAUSED BY MONITORING = 0` against the accepted baseline of 19 pre-existing failures.

---

## 23. File-Level Implementation Map

| File Path | Action | Expected Classes / Functions | Dependencies | Covering Tests |
|---|---|---|---|---|
| `backend/src/kortex/engines/monitoring/__init__.py` | Modify | Public exports: `MonitoringEngine`, models, constants | `engine.py`, `models.py` | Unit & Integration |
| `backend/src/kortex/engines/monitoring/constants.py` | Create | Capability names, event topics, default thresholds, limits | None | `test_monitoring_engine.py` |
| `backend/src/kortex/engines/monitoring/interfaces.py` | Create | Protocols: `IMonitoringEngine`, `IMetricRegistry`, `ITimeSeriesBuffer` | `models.py` | `test_monitoring_primitives.py` |
| `backend/src/kortex/engines/monitoring/models.py` | Create | Pydantic models: `MetricType`, `MetricValue`, `MetricSnapshot`, `DashboardData`, `MonitoringConfig` | `pydantic` | `test_monitoring_primitives.py` |
| `backend/src/kortex/engines/monitoring/metrics.py` | Create | Thread-safe primitives: `Counter`, `Gauge`, `Histogram`, `Timer` | `models.py` | `test_monitoring_primitives.py` |
| `backend/src/kortex/engines/monitoring/registry.py` | Create | `MetricRegistry` with cardinality caps (max 500 series) | `metrics.py`, `constants.py` | `test_monitoring_registry.py` |
| `backend/src/kortex/engines/monitoring/normalizer.py`| Create | `DiagnosticsNormalizer` converting heterogeneous diagnostics to canonical metrics | `models.py` | `test_monitoring_normalizer.py` |
| `backend/src/kortex/engines/monitoring/timeseries.py` | Create | `TimeSeriesBuffer` managing bounded deques (`maxlen=360`) | `models.py`, `constants.py` | `test_monitoring_timeseries.py` |
| `backend/src/kortex/engines/monitoring/collector.py` | Create | `MetricsCollector` sweeping `IEngineDiagnostics` with 1.0s timeout & system telemetry | `registry.py`, `normalizer.py` | `test_monitoring_collector.py` |
| `backend/src/kortex/engines/monitoring/thresholds.py` | Create | `ThresholdEvaluator` with 10% hysteresis and 60s cooldown | `models.py`, `events.py` | `test_monitoring_thresholds.py` |
| `backend/src/kortex/engines/monitoring/events.py` | Create | `MonitoringEventPublisher` for threshold events | `core.events` | `test_monitoring_thresholds.py` |
| `backend/src/kortex/engines/monitoring/diagnostics.py` | Create | `MonitoringDiagnostics(IEngineDiagnostics)` adapter | `storage.interfaces` | `test_monitoring_engine.py` |
| `backend/src/kortex/engines/monitoring/engine.py` | Create | `MonitoringEngine(BaseEngine, IEngineDiagnostics)` | `collector.py`, `thresholds.py` | `test_monitoring_engine.py`, integration |
| `backend/src/kortex/engines/monitoring/README.md` | Create | Engine architecture, capabilities, and events documentation | None | Documentation |
| `backend/src/kortex/api/kernel_bootstrap.py` | Modify | Register `MonitoringEngine` right after `SentinelEngine` | `monitoring.engine` | `test_monitoring_integration.py` |
| `docs/adr/ADR-0015-phase7-monitoring-engine.md` | Create | ADR for Phase 7 Monitoring Engine | None | Architectural documentation |

---

## 24. Sequencing / Order of Implementation

1. **Phase A: Core Models & Primitives** (`constants.py`, `models.py`, `interfaces.py`, `metrics.py`, `test_monitoring_primitives.py`).
2. **Phase B: Normalization, Registry & Buffer** (`normalizer.py`, `registry.py`, `timeseries.py`, `test_monitoring_normalizer.py`, `test_monitoring_registry.py`, `test_monitoring_timeseries.py`).
3. **Phase C: Collector & System Telemetry** (`collector.py`, `test_monitoring_collector.py`).
4. **Phase D: Thresholds & Alert Events** (`thresholds.py`, `events.py`, `test_monitoring_thresholds.py`).
5. **Phase E: Engine & Diagnostics Protocol** (`engine.py`, `diagnostics.py`, `test_monitoring_engine.py`).
6. **Phase F: Bootstrap & Integration** (`kernel_bootstrap.py`, `test_monitoring_integration.py`).
7. **Phase G: Verification & Documentation** (`README.md`, ADR-0015, Ruff, Mypy, full-suite regression, Graphify).

---

## 25. Risks & Mitigations

1. **Risk**: Slow or hanging engine `metrics()` call delays entire collection cycle.  
   **Mitigation**: Enforce strict `asyncio.wait_for(timeout=1.0)` per engine.
2. **Risk**: High-frequency metric ingestion causes memory growth.  
   **Mitigation**: Bounded deques (`maxlen=360`), max 500 series limit, max 1,000 samples per histogram reservoir (hard ceiling $< 15\text{ MB}$).
3. **Risk**: Rapid metric fluctuations cause alert storms.  
   **Mitigation**: 2-cycle trigger requirement, 10% hysteresis recovery threshold, and 60-second cooldown per metric alert.
4. **Risk**: OS portability differences in system resource measurements.  
   **Mitigation**: Standard library platform branches (Windows ctypes, POSIX resource) with pure Python fallbacks.

---

## 26. Open Decisions & Owner Visibility

### 26.1 Resolved by Architecture
- **Sentinel Coupling**: Resolved via public `IEngineDiagnostics.health()` and event subscription with graceful `UNKNOWN` fallback. Zero private imports.
- **Normalization**: Resolved via `DiagnosticsNormalizer` handling scalars, booleans, simple dict breakdowns, and finite validation without changing existing engines.
- **Cardinality Limits**: Resolved with deterministic rejection (no truncation collisions) and hard caps (200 names, 500 series, $< 15\text{ MB}$ RAM).
- **Histogram Semantics**: Resolved as approximate percentiles over sliding-window reservoir ($N = 1000$) via linear rank interpolation.
- **System Telemetry**: Resolved using pure standard library (`ctypes`, `resource`, `os`, `asyncio`, `threading`) without requiring `psutil`.
- **Security Contexts**: Resolved by separating internal background identity (`kortex-monitoring-system`) from operator query execution context (`system:monitoring:read`).
- **Dashboard Composition**: Resolved via direct internal component queries without nested capability dispatch.
- **Threshold Authority**: Resolved via clean boundary: Monitoring measures and emits alerts; Sentinel assesses health and requests recovery; Recovery executes recovery.
- **Persistence**: Confirmed NO MIGRATION (100% ephemeral in-memory state).

### 26.2 Implementation Detail
- Default collection interval: 10.0 seconds.
- Default time-series retention: 360 points (60 minutes).
- Reservoir sample size: 1,000 samples.
- Whitelisted label keys: `subsystem`, `driver`, `status`, `error_type`, `action_type`, `severity`, `entity_type`.

### 26.3 Owner Decisions Required
**OWNER DECISIONS REQUIRED: NONE**  
All architectural ambiguities identified during review have been resolved strictly within existing KORTEX Architecture v1.0.0 contracts. The plan is complete and ready for implementation authorization.
