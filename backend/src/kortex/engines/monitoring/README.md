# KORTEX Monitoring Engine

Phase 7 — Production Hardening — Monitoring Engine

The Monitoring Engine is a core infrastructure subsystem responsible for observing, collecting, normalizing, aggregating, retaining, querying, and presenting real-time operational telemetry across KORTEX OS.

---

## Mission

```text
OBSERVE → COLLECT → NORMALIZE → AGGREGATE → RETAIN → QUERY → PRESENT OPERATIONAL STATE
```

Monitoring provides continuous operational visibility into host system resources (memory, CPU, threads, asyncio tasks, event loop lag) and all registered KORTEX System Engines via the canonical `IEngineDiagnostics` protocol.

---

## Architectural Boundaries

### Hard Boundary
- Monitoring **observes and reports**.
- Monitoring **never recovers, restarts, or terminates** processes or engines.
- Monitoring maintains **ephemeral in-memory state only**.
- Zero database models or Alembic migrations exist.

### Sentinel Boundary
- Sentinel is responsible for subsystem health classification and failure detection.
- Monitoring integrates with Sentinel exclusively through:
  1. Consuming the public event `kortex.sentinel.health.changed` into a local cached representation.
  2. Querying Sentinel on-demand via the canonical `IEngineDiagnostics.health()` contract.
- Monitoring never imports Sentinel private modules, classes, or collections.
- Monitoring operates gracefully when Sentinel is absent.

---

## Components

| Component | Responsibility |
|-----------|----------------|
| `MetricRegistry` | Container for metric primitives enforcing cardinality limits (200 names, 500 active series). |
| `TimeSeriesBuffer` | Rolling in-memory ring buffers (360 points at 10s intervals = 60 minutes retention). |
| `DiagnosticsNormalizer` | 3-tier normalization of `IEngineDiagnostics` payloads. |
| `MetricsCollector` | Periodically sweeps host resources and registered engines with 1.0s timeout per engine. |
| `ThresholdEvaluator` | Evaluates operational metrics with 2 consecutive cycles, 10% hysteresis, and 60s cooldown. |
| `MonitoringEventPublisher` | Emits `kortex.monitoring.threshold.exceeded` and `kortex.monitoring.threshold.recovered`. |
| `MonitoringDiagnostics` | Exposes self-observability conforming to `IEngineDiagnostics`. |
| `MonitoringEngine` | `BaseEngine` implementation coordinating lifecycle, background collection, and capabilities. |

---

## Metric Cardinality Limits

- **Maximum Metric Names**: 200
- **Maximum Active Series**: 500
- **Maximum Labels per Series**: 5
- **Maximum Label Value Length**: 64 characters
- **Permitted Label Keys**: `subsystem`, `driver`, `status`, `error_type`, `action_type`, `severity`, `entity_type`
- **Metric Name Grammar**: `[a-z][a-z0-9_]*(\.[a-z0-9_]+)*`

---

## Capabilities

All capabilities require authentication, `system:monitoring:read` permission, and `INTERNAL` security clearance:

| Capability | Description | Handler |
|------------|-------------|---------|
| `kortex.monitoring.metrics.get` | Query real-time metric snapshots | `handle_metrics_get` |
| `kortex.monitoring.timeseries.get` | Query historical time-series points | `handle_timeseries_get` |
| `kortex.monitoring.dashboard.get` | Consolidated operational dashboard state | `handle_dashboard_get` |
| `kortex.monitoring.diagnostics.get` | Technical self-diagnostics report | `handle_diagnostics_get` |

Dashboard queries are composed directly via internal component aggregation and never make nested capability dispatch calls.
