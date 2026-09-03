# Implementation Plan — Phase 7: Production Hardening — Monitoring Engine

**Item**: Monitoring Engine (Metrics, Dashboards, and Operational Telemetry)  
**Phase**: Phase 7 — Production Hardening  
**Status**: PLANNED (Awaiting Owner Authorization for Implementation)  
**Document**: `implementation_plan.md` (canonical copy at `docs/architecture/monitoring_engine_implementation_plan.md`)  

---

## 1. Executive Definition

### 1.1 Item Name & Phase
- **Name**: Phase 7 — Production Hardening — Monitoring Engine
- **Phase**: Phase 7 (Production Hardening)
- **Status**: `PLANNED`

### 1.2 Why It Is Next
1. **Sentinel Milestone Formally Accepted**: With the Sentinel Engine accepted as `DONE` (commit `65676a4c`), observation, invariant integrity checks, subsystem health classification (`SentinelStatus`), and failure detection are fully operational.
2. **Complementary Observability Peer**: Monitoring Engine is Sentinel's direct operational metrics and dashboard aggregation peer. While Sentinel evaluates discrete health states (`HEALTHY`, `DEGRADED`, `FAILED`, etc.) and invariant violations, Monitoring Engine provides continuous numerical telemetry, counters, gauges, latency histograms, throughput rates, and consolidated dashboard aggregation.
3. **Existing Interface Foundation**: Every KORTEX system engine (`StorageEngine`, `WorkflowEngine`, `DocumentEngine`, `ConnectorEngine`, `KnowledgeEngine`, `SecurityEngine`, `AIEngine`, `LicenseEngine`, `SentinelEngine`) already implements `IEngineDiagnostics` (`metrics()`, `health()`, `diagnostics()`). However, zero central collection, aggregation, or dashboard query capability exists today.
4. **Zero Unsatisfied Prerequisites**:
   - `Backup Engine` requires migration stability and dedicated storage retention design.
   - `Recovery Engine` is explicitly `BLOCKED — PENDING OWNER DECISION` (Owner Decision #1).
   - `Update Engine` requires distribution packaging and desktop updater mechanisms.
   - `Docker Production Builds` is blocked on Owner Decision #2 (deployment topology and key management).
   - `Desktop Installers` depends on signed CI/CD release pipelines.
   - In contrast, Monitoring Engine has no hard external dependencies and runs entirely within the local-first runtime.
5. **Reconciliation Alignment**: The proposed Critical Path in `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §6 pairs Sentinel and Monitoring as the core runtime observability foundation.

### 1.3 Architectural Authority
- KORTEX Architecture v1.0.0
- ADR-0015 (Phase 7 Production Hardening — Monitoring Engine, to be drafted upon implementation)
- `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §5.3

### 1.4 Explicit Scope
- **Metrics Collection**: Periodic polling of all registered engines implementing `IEngineDiagnostics` with strict self-exclusion.
- **Metric Primitives**: In-memory thread-safe `Counter`, `Gauge`, `Histogram` (with percentile estimations: p50, p90, p99), and `Timer`.
- **Bounded TimeSeries Buffer**: Rolling in-memory circular buffers (`collections.deque(maxlen=360)`) maintaining historical data points at 10-second intervals for the preceding 60 minutes.
- **Dashboard Aggregation**: Consolidated operational dashboard capability providing uptime, system resource telemetry, engine states, top throughput counters, error rates, and Sentinel health rollup.
- **Threshold Alerting**: Configurable warning/critical thresholds for latency, error rates, and resource consumption emitting informational domain events.
- **Read-Only Capabilities**: Exactly 4 authenticated, RBAC-protected capabilities under `kortex.monitoring.*`.

### 1.5 Explicit Non-Goals
- **No External SaaS Telemetry**: No Prometheus pushgateway, Datadog, or external OpenTelemetry cloud exporter in the base offline build (preserves local-first offline architecture).
- **No Process Supervision**: Does NOT restart engines, terminate processes, or kill workers (strict BaseEngine boundary).
- **No Duplication of Sentinel**: Does NOT compute subsystem health states or crash-loop circuit breakers. Sentinel remains the sole authority for health state; Monitoring queries Sentinel and displays health in dashboard views.
- **No Database Persistence**: No relational database tables or Alembic migrations. All metric data is ephemeral and process-local.
- **No Business Logic Mutation**: Strictly observational and read-only.

---

## 2. Dependency Position

### 2.1 Placement in Canonical Engine Sequence
```
Boot / Config / Registry / Event
→ Storage
→ Workflow
→ Recipe
→ Document
→ Connector
→ Knowledge
→ Security
→ AI Orchestration
→ Business Modules (Finance, HR, Operations)
→ Marketplace
→ Production Hardening (Sentinel [DONE] → Monitoring [PLANNED] → Backup → Recovery → Update)
→ Desktop / Installers
```

### 2.2 Dependency Relationships
- **Upstream Dependencies**:
  - `kortex.core.kernel.Kernel`: Lifecycle management, capability registration, event publishing.
  - `kortex.core.base_engine.BaseEngine`: Canonical engine lifecycle (`UNINITIALIZED` → `READY` → `RUNNING` → `STOPPED`).
  - `kortex.core.events.EventPriority`: Priority assignment for metric alert events.
  - `kortex.engines.storage.interfaces.IEngineDiagnostics`: Target interface for pulling engine metrics.
  - `kortex.engines.sentinel.engine.SentinelEngine`: Health status source for dashboard views.
  - `kortex.engines.security.engine.SecurityEngine`: Authentication, principal verification, and RBAC (`system:monitoring:read`).
- **Downstream Consumers**:
  - Desktop UI / Operator Dashboards (querying `kortex.monitoring.dashboard.get`).
  - Future Backup & Update Engines (querying performance telemetry during maintenance operations).
- **Optional Dependencies**:
  - Standard library `psutil` or `resource` / `asyncio` for system resource stats (CPU, RSS memory, thread counts).
- **Forbidden Dependencies**:
  - Direct import of business modules (`kortex.modules.finance`, `kortex.modules.hr_payroll`, `kortex.modules.operations`).
  - Direct dependency on AI or Workflow engine internal execution states.
  - External network calls or cloud services.
- **Circular Dependency Risks & Mitigation**:
  - Monitoring Engine itself implements `IEngineDiagnostics`. When sweeping registered engines for metrics, the collector MUST explicitly exclude `"monitoring"` (self-exclusion) to prevent recursive aggregation.

---

## 3. Mission

### 3.1 What Monitoring Engine MUST Do
1. Provide a unified, local-first operational telemetry and metric aggregation engine.
2. Maintain thread-safe in-memory metric primitives (Counters, Gauges, Histograms, Timers).
3. Periodically collect metrics from all registered engines implementing `IEngineDiagnostics` without blocking execution.
4. Maintain a bounded rolling time-series buffer (max 360 points per metric, ~60 min window) for local charting.
5. Provide a consolidated dashboard query capability combining Sentinel health, engine operational stats, and system metrics.
6. Emit informational events when metric values exceed configured warning or critical thresholds.
7. Comply with strict KORTEX security: all capabilities require authentication, execution context, and `system:monitoring:read` permission.
8. Cleanly start and stop background collector tasks during Kernel lifecycle transitions.

### 3.2 What Monitoring Engine MUST NOT Do
1. MUST NOT execute recovery, trigger restarts, or kill processes.
2. MUST NOT classify or determine subsystem health (Sentinel owns health assessment).
3. MUST NOT write to the database or require Alembic migrations.
4. MUST NOT make external network requests.
5. MUST NOT allow unbounded memory growth (strict deque limits).
6. MUST NOT bypass security clearance or accept caller-controlled tenant context.

---

## 4. Existing Repository Reality

### 4.1 Existing Interfaces & Contracts
- `IEngineDiagnostics` in `backend/src/kortex/engines/storage/interfaces.py`:
  ```python
  @runtime_checkable
  class IEngineDiagnostics(Protocol):
      def health(self) -> dict[str, Any]: ...
      def metrics(self) -> dict[str, Any]: ...
      def diagnostics(self) -> dict[str, Any]: ...
      def status(self) -> str: ...
      def version(self) -> str: ...
      def capabilities(self) -> list[str]: ...
  ```
- Current implementations: `StorageEngine`, `SecurityEngine`, `RecipeEngine`, `WorkflowEngine`, `KnowledgeEngine`, `LicenseEngine`, `ProcessIntelligenceEngine`, `SentinelEngine`, `FinanceModule`.
- `backend/src/kortex/engines/monitoring/__init__.py`: Currently a 2-line docstring (`"KORTEX Monitoring Engine — Metrics collection, dashboards, and alerting."`), no other files exist.

### 4.2 Sentinel Integration
- Sentinel provides `kortex.sentinel.health.get`, returning `SentinelHealthReport` (`status`, `subsystems`, `invariants`, `incidents`).
- Monitoring Engine consumes Sentinel's overall status to render top-level dashboard health badges without re-evaluating health probes.

---

## 5. Architecture

### 5.1 Package Structure
```
backend/src/kortex/engines/monitoring/
├── __init__.py          # Public exports (MonitoringEngine, models, constants)
├── constants.py         # Capability names, event topics, default thresholds
├── interfaces.py        # IMonitoringEngine, IMetricRegistry protocols
├── models.py            # Pydantic models (MetricValue, DashboardData, AlertRule)
├── registry.py          # MetricRegistry (thread-safe Counters, Gauges, Histograms)
├── buffer.py            # TimeSeriesBuffer (bounded rolling deque)
├── collector.py         # MetricsCollector (periodic background engine polling)
├── engine.py            # MonitoringEngine(BaseEngine, IEngineDiagnostics)
├── events.py            # MonitoringEventPublisher (threshold alerts & snapshots)
├── diagnostics.py       # MonitoringDiagnostics adapter
└── README.md            # Engine documentation
```

### 5.2 Internal Components
1. **`MetricRegistry`**:
   - Manages registered metric instances keyed by `subsystem.metric_name`.
   - Thread-safe increments for `Counter`.
   - Thread-safe sets for `Gauge`.
   - Sliding-window reservoir for `Histogram` calculating percentiles (p50, p90, p99, max, min, avg).
2. **`TimeSeriesBuffer`**:
   - Ring buffer backed by `collections.deque(maxlen=360)`.
   - Stores timestamped `(timestamp_iso, value)` pairs per metric.
   - Provides time-range slices (`get_range(metric_name, start_time, end_time)`).
3. **`MetricsCollector`**:
   - Asynchronous background worker running at configured interval (default: 10.0s).
   - Iterates through `Kernel.get_registered_engines()`.
   - Skips `"monitoring"` (self-exclusion).
   - If engine is an instance of `IEngineDiagnostics`, safely calls `engine.metrics()` inside a try/except block to ensure one faulty engine never breaks collection.
   - Samples system resource telemetry (process RSS memory, CPU percent, active thread count, active asyncio task count).
   - Pushes sampled metrics into `MetricRegistry` and `TimeSeriesBuffer`.
4. **`MonitoringEngine`**:
   - Subclasses `BaseEngine` and implements `IEngineDiagnostics`.
   - Manages lifecycle (`initialize()`, `start()`, `stop()`, `reset()`).
   - Registers capabilities with the Kernel.
   - Houses capability handlers.
   - Tracks its own background collector task in `_background_tasks`.

### 5.3 Concurrency & Lifecycle
- Polling loop uses cooperative `asyncio.sleep(interval)`.
- Thread-safety for metric recording using non-blocking atomic operations / thread lock for reservoir sampling.
- On `stop()`, the collector task is cancelled and awaited with timeout handling (`asyncio.wait_for`).

---

## 6. Security

### 6.1 Identities & Clearance
- **System Principal**: `kortex-monitoring-system`.
- **Classification**: `INTERNAL`.
- **Required Permission**: `system:monitoring:read`.

### 6.2 Execution Context
- All 4 capability descriptors require:
  - `requires_authentication=True`
  - `requires_execution_context=True`
  - `security_classification="INTERNAL"`
  - `required_permissions=["system:monitoring:read"]`
- Unauthenticated requests raise `AuthenticationError`.
- Unauthorized requests raise `AuthorizationDeniedError`.
- Tenant context is not caller-controlled; metrics are system-wide runtime statistics without PII or sensitive business payload data.

---

## 7. Capabilities

| Capability Name | Input Parameters | Output Response | Permission | Security |
|---|---|---|---|---|
| `kortex.monitoring.metrics.get` | `subsystem: Optional[str]`, `metric_names: Optional[list[str]]` | `timestamp`, `subsystems: dict`, `system: dict` | `system:monitoring:read` | Authenticated, INTERNAL |
| `kortex.monitoring.timeseries.get` | `metric_name: str`, `duration_seconds: Optional[int] = 3600` | `metric_name`, `points: list[{"timestamp": str, "value": float}]` | `system:monitoring:read` | Authenticated, INTERNAL |
| `kortex.monitoring.dashboard.get` | `{}` | `timestamp`, `uptime_seconds`, `health_summary`, `subsystem_metrics`, `system_resources`, `active_alerts` | `system:monitoring:read` | Authenticated, INTERNAL |
| `kortex.monitoring.diagnostics.get`| `{}` | Conforming `IEngineDiagnostics` output (`health`, `metrics`, `diagnostics`) | `system:monitoring:read` | Authenticated, INTERNAL |

---

## 8. Events

| Event Topic | Producer | Trigger | Payload | Priority |
|---|---|---|---|---|
| `kortex.monitoring.threshold.exceeded` | MonitoringEngine | Metric exceeds configured warning/critical threshold | `metric_name`, `current_value`, `threshold`, `severity`, `subsystem` | HIGH |
| `kortex.monitoring.threshold.recovered`| MonitoringEngine | Metric returns below threshold after being in alert | `metric_name`, `current_value`, `threshold`, `subsystem` | NORMAL |
| `kortex.monitoring.snapshot.emitted`   | MonitoringEngine | Periodic summary emission (default 60s) | `timestamp`, `uptime_seconds`, `active_engines`, `total_memory_bytes`, `avg_latency_ms` | LOW |

- **Event Invariants**:
  - Every event produces a fresh UUIDv4 `event_id`.
  - UTC ISO-8601 timestamps.
  - `correlation_id` preserved when triggered during specific capability operations.
  - Strictly bounded payloads; zero credentials, tokens, or PII.
  - Informational delivery (no exactly-once claims).

---

## 9. Persistence

### 9.1 Storage Model
- **NO MIGRATION**: Strictly zero database tables, zero Alembic revisions.
- All metric data, histograms, and time-series buffers are process-local and in-memory.
- Maximum buffer size: 360 points per metric. With ~50 metrics, memory footprint is $< 2\text{ MB}$.
- Ephemeral lifecycle: metrics reset on process restart.

---

## 10. Failure & Recovery Boundary

- **Boundary Separation**:
  - Monitoring Engine **detects** metric threshold violations and **notifies** via `kortex.monitoring.threshold.exceeded` events.
  - Monitoring Engine does **NOT** execute recovery, restart engines, or terminate processes.
  - Future recovery engines or operator tools may subscribe to `kortex.monitoring.threshold.exceeded` to take action, but Monitoring itself remains strictly passive and observational.

---

## 11. Configuration

### 11.1 Settings Model (`MonitoringConfig`)
Validated via Pydantic:
- `KORTEX_MONITORING_COLLECT_INTERVAL_SECONDS` (float, default: `10.0`, bounds: `1.0` to `300.0`)
- `KORTEX_MONITORING_BUFFER_MAX_POINTS` (int, default: `360`, bounds: `60` to `1440`)
- `KORTEX_MONITORING_ENABLED` (bool, default: `True`)
- `KORTEX_MONITORING_ALERT_EVAL_INTERVAL_SECONDS` (float, default: `15.0`, bounds: `5.0` to `300.0`)
- `KORTEX_MONITORING_EVENT_LOOP_LAG_THRESHOLD_SECONDS` (float, default: `1.0`, bounds: `0.1` to `10.0`)
- `KORTEX_MONITORING_MEMORY_WARNING_THRESHOLD_MB` (float, default: `1024.0`)

---

## 12. Lifecycle & Bootstrap

### 12.1 Bootstrap Registration Order
In `backend/src/kortex/api/kernel_bootstrap.py`:
```python
# 1. Base Engines
# ...
# 11. LicenseEngine
# 12. SentinelEngine
# 13. MonitoringEngine (PLANNED)
monitoring_engine = MonitoringEngine(kernel=kernel)
kernel.register_engine("monitoring", monitoring_engine)
# 14. Business Modules
# ...
# 15. kernel.boot()
```

### 12.2 Lifecycle Transitions
- `initialize()`: Sets up metric registry, allocates time-series buffers, registers capabilities.
- `start()`: Starts the background collector loop.
- `stop()`: Signals cancellation to background tasks, awaits task completion, marks engine `STOPPED`.
- `reset()`: Flushes time-series buffers and resets counters.

---

## 13. Testing Strategy

### 13.1 Planned Test Suites
1. **Unit Tests**:
   - `backend/tests/unit/test_monitoring_engine.py`:
     - Engine construction, default configuration, custom config validation.
     - Lifecycle transitions (`UNINITIALIZED` → `READY` → `RUNNING` → `STOPPED`).
     - Capability registration and handlers.
     - Diagnostics protocol conformance (`IEngineDiagnostics`).
     - Self-exclusion verification during engine collection sweep.
   - `backend/tests/unit/test_monitoring_registry.py`:
     - Thread-safe `Counter` increments and resets.
     - `Gauge` updates and bounds.
     - `Histogram` reservoir sampling and percentiles (p50, p90, p99, min, max, avg).
     - `Timer` context manager and execution duration recording.
   - `backend/tests/unit/test_monitoring_buffer.py`:
     - TimeSeriesBuffer bounded FIFO eviction (never exceeds `max_points`).
     - Time-window slicing and timestamp ordering.
     - Buffer clearing on engine reset.
   - `backend/tests/unit/test_monitoring_events.py`:
     - Alert threshold trigger and recovery event emissions.
     - Periodic snapshot event generation.
     - Payload schema validation and UUIDv4 event ID checks.
2. **Integration Tests**:
   - `backend/tests/integration/test_monitoring_integration.py`:
     - Full Kernel boot with `MonitoringEngine` registered.
     - Capability dispatch for all 4 capabilities via `kernel.invoke_capability()`.
     - Security tests: unauthenticated callers rejected with `AuthenticationError`.
     - RBAC tests: unauthorized callers without `system:monitoring:read` rejected with `AuthorizationDeniedError`.
     - Dashboard aggregation returning live health from Sentinel and live metrics from registered engines.
     - Clean Kernel shutdown without leaking background collector tasks.
3. **Failure Injection Tests**:
   - `backend/tests/unit/test_monitoring_failure_injection.py`:
     - Handling slow or hanging `engine.metrics()` calls with timeout protection.
     - Handling throwing/broken `engine.metrics()` implementations without crashing the collector.
     - Handling memory pressure / rapid metric ingestion.

---

## 14. Acceptance Gates

Before Monitoring Engine implementation can be considered complete:
1. **Targeted Tests**: 100% of new unit, integration, and failure-injection tests pass.
2. **Cross-Engine Tests**: Existing cross-engine tests (`test_boot_engine.py`, `test_capability_dispatch.py`, `test_production_capability_permissions.py`, `test_alembic_migrations.py`, `test_sentinel_integration.py`) pass.
3. **Full Backend Suite**: Complete backend test suite executed; exact node-ID comparison proves `NEW FAILURE NODE IDs CAUSED BY MONITORING = 0`.
4. **Code Quality Gates**:
   - `ruff check`: 0 errors.
   - `ruff format`: clean formatting.
   - `mypy`: 0 type issues across all new and modified source files.
5. **Migration Integrity**: Zero new Alembic migrations; `test_alembic_migrations.py` passes.
6. **Graphify Freshness**: `built_at_commit == HEAD` verified.
7. **Git State**: Clean working tree; no unintended files committed.
8. **Documentation**: ADR-0015 created; `README.md` created in engine directory; `PRODUCTION_HARDENING_RECONCILIATION.md` updated to `IMPLEMENTED — AWAITING REVIEW`.
