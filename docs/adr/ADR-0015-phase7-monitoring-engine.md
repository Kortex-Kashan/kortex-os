# ADR-0015: Phase 7 — Production Hardening — Monitoring Engine

- **Status**: IMPLEMENTED — AWAITING REVIEW
- **Date**: 2026-09-04
- **Deciders**: Chief Architect (KASHAN), Antigravity (Implementation Engineer)
- **Target Component**: Monitoring Engine (`kortex.engines.monitoring`)

---

## Context and Problem Statement

KORTEX OS is an AI-powered local-first business operating system requiring enterprise-grade production reliability and real-time operational visibility. While Sentinel Engine (ADR-0014) established subsystem health status evaluation, deadlock detection, and failure classification, KORTEX required a dedicated engine to observe, collect, normalize, aggregate, retain, query, and present operational telemetry and system resource utilization.

The challenge was to design and implement the Monitoring Engine adhering strictly to KORTEX Architecture v1.0.0 and the AI Engineering Constitution (`AGENTS.md`):
1. Monitoring observes, normalizes, retains, and reports operational telemetry; it must NEVER perform recovery, process termination, or engine restarts.
2. Monitoring complements Sentinel without depending on Sentinel internals or creating circular dependencies.
3. Telemetry is in-memory and bounded; zero database migrations or persistent tables.
4. Metric cardinality and label dimensions must have hard upper bounds to prevent unbounded memory growth.
5. All system telemetry must use Python standard library only (no `psutil`) with portable platform abstractions.

---

## Decision Drivers

1. **Constitutional Invariant**: "Engines are infrastructure. They never contain business rules." (AGENTS.md Art. 6)
2. **Sentinel Boundary**: Sentinel classifies failures and evaluates health; Monitoring collects telemetry and presents operational metrics.
3. **Strict Normalization Boundary**: 3-tier normalization of `IEngineDiagnostics` output (finite numeric metrics, metadata preservation, deterministic arbitrary payloads). Non-finite numbers (`NaN`, `+Inf`, `-Inf`) are rejected.
4. **Cardinality Ceilings**: Maximum 200 metric names, 500 active series, 5 labels per series, 64-character maximum label value length. Whitelisted label dimensions only.
5. **Bounded Time-Series Retention**: Maximum 360 points per series (60 minutes at 10-second collection intervals).
6. **Approximate Histograms**: Sliding-window reservoir (1,000 samples) with linear-rank interpolation (p50, p90, p95, p99) computed outside the write lock.
7. **Security**: Read-only internal capabilities with strict RBAC (`system:monitoring:read`) and caller execution context propagation.

---

## Decision Outcome

Chosen Option: Implement `MonitoringEngine` as an in-memory infrastructure engine extending `BaseEngine` and `IEngineDiagnostics`.

### Architectural Details

1. **Mission & Lifecycle**:
   - `OBSERVE → COLLECT → NORMALIZE → AGGREGATE → RETAIN → QUERY → PRESENT OPERATIONAL STATE`.
   - Extends `BaseEngine` lifecycle: `UNINITIALIZED → INITIALIZING → READY → RUNNING → STOPPING → STOPPED`.
   - Owned background collection loop with strict timeout (1.0s) per engine and self-exclusion (`"monitoring"`).

2. **Metric Primitives & Registry**:
   - Thread-safe `Counter`, `Gauge`, `Histogram`, and `Timer`.
   - `MetricRegistry` enforcing collision-safe series keys with sorted labels (`name{k1=v1,k2=v2}`).
   - Strict label whitelist: `subsystem`, `driver`, `status`, `error_type`, `action_type`, `severity`, `entity_type`.
   - Enforces cardinality caps (200 names, 500 active series).

3. **Diagnostics Normalization Boundary (3-Tier)**:
   - **Tier 1 (Canonical Numbers)**: Scalar finite integers and floats conforming to `[a-z][a-z0-9_]*(\.[a-z0-9_]+)*`, source-prefixed by subsystem. Rejects `NaN`, `+Inf`, `-Inf`.
   - **Tier 2 (Metadata)**: `status`, `version`, `capabilities`, `timestamp`, `dependencies` preserved semantically.
   - **Tier 3 (Arbitrary Payload)**: Booleans converted to 1.0/0.0 gauges; `None` skipped; non-whitelisted strings/lists kept in raw payload; first-occurrence-wins duplicate resolution.

4. **System Telemetry (Stdlib-First)**:
   - Memory: Windows `ctypes.WinDLL("psapi.dll").GetProcessMemoryInfo`, POSIX `resource.getrusage`, `tracemalloc` fallback.
   - CPU: `os.times()` deltas. Explicit first-sample behavior returning 0.0%.
   - Async Tasks: `len(asyncio.all_tasks())`.
   - Threads: `threading.active_count()`.
   - Event Loop Lag: Independent non-blocking asyncio sleep probe.

5. **Sentinel Integration**:
   - Primary: Subscribes to public `kortex.sentinel.health.changed` and caches latest health payload.
   - On-demand: Resolves Sentinel via Kernel engine registry using canonical `IEngineDiagnostics.health()`.
   - Gracefully handles Sentinel absence without crashing. Never imports Sentinel private internals.

6. **Operational Threshold Engine**:
   - States: `NORMAL`, `WARNING`, `CRITICAL`.
   - Two consecutive evaluation cycles required to assert an alert.
   - 10% hysteresis on recovery (value must drop below `warning_threshold * 0.90`).
   - 60-second cooldown on alert event emission (bypassed on severity escalation).
   - Emits `kortex.monitoring.threshold.exceeded` and `kortex.monitoring.threshold.recovered`. (No snapshot events).

7. **Capabilities & Security**:
   - Exactly 4 registered capabilities:
     * `kortex.monitoring.metrics.get`
     * `kortex.monitoring.timeseries.get`
     * `kortex.monitoring.dashboard.get`
     * `kortex.monitoring.diagnostics.get`
   - All capabilities: `requires_authentication=True`, `required_permissions=["system:monitoring:read"]`, `security_classification="INTERNAL"`, `requires_execution_context=True`.
   - Background collection runs under dedicated identity `kortex-monitoring-system`.
   - Operator queries preserve caller's authenticated `principal`, `execution_context`, and tenant context.
   - Dashboard: direct internal composition without nested capability dispatch.

8. **Storage Boundary**:
   - 100% ephemeral in-memory storage.
   - Zero database tables, zero Alembic migrations.

---

## Consequences

### Positive
- High-fidelity operational visibility across host resources and all registered engines.
- Strict bounded memory footprint (< 15 MB) guaranteed by cardinality caps and circular buffers.
- Completely decoupled from Sentinel internals and future recovery engines.
- Clean integration into Kernel boot and health check rollup.
- Full compliance with KORTEX AI Engineering Constitution and Architecture v1.0.0.

### Negative / Trade-offs
- Telemetry metrics and time-series history are process-local and do not persist across process restarts.
- Histograms provide approximate percentiles over the sliding reservoir, not exact full-lifetime quantiles.
