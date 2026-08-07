# KORTEX OS — System Diagnostics Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/system_diagnostics.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)

---

## 1. Common Diagnostics Interface (`IEngineDiagnostics`)

Every System Engine in KORTEX OS MUST implement the standardized `IEngineDiagnostics` interface defined in Section 2.1 of `phase2_design.md`:

```python
# Standardized Diagnostics Interface exposed by all KORTEX System Engines
class IEngineDiagnostics(Protocol):
    def health(self) -> Dict[str, Any]: ...
    def metrics(self) -> Dict[str, Any]: ...
    def diagnostics(self) -> Dict[str, Any]: ...
    def status(self) -> str: ...
    def version(self) -> str: ...
    def capabilities(self) -> List[str]: ...
```

---

## 2. Health Monitoring (`health()`)

Provides operational status, component readiness checks, and error counters for each engine (e.g. `{"status": "READY", "db_connected": true, "error_count": 0}`).

---

## 3. Performance Metrics (`metrics()`)

Exposes runtime operational counters:
- Total throughput (requests/sec).
- Average execution latency (ms).
- Active background tasks & queue depth.
- Cache hit/miss ratios.

---

## 4. Distributed Tracing (`trace_id`)

All requests carry a `correlation_id` / `trace_id` UUID string propagated across capability calls, events, and log entries for end-to-end trace reconstruction.

---

## 5. Structured Logging (`structlog`)

- **Format**: JSON-structured log format (`timestamp`, `level`, `engine`, `capability`, `correlation_id`, `message`).
- **No `print()` Statements**: Raw `print()` statements are forbidden by Article 23 of Constitution.
- **No Secret Leakage**: Passwords, secrets, and private payload data are scrubbed before logging.

---

## 6. Audit Logging (`UniversalAuditEntry`)

Mutative operations record immutable `UniversalAuditEntry` objects in `IDataStore` capturing actor ID, action, timestamp, previous state hash, and new state hash.

---

## 7. Performance Profiling

Monitors CPU usage, RAM consumption, database query duration, and event queue latencies.

---

## 8. Diagnostic Alerts

System threshold breaches (e.g. queue depth > 1000, error rate > 5%, disk space low) trigger high-priority `SystemAlertEvent` events published to Event Engine.

---

## 9. Failure Recovery

Integrates with Workflow Engine recovery providers (`IDocumentRecoveryProvider`) to handle automated retries, checkpoints, and compensation stack execution.

---

## 10. System Telemetry

Aggregates diagnostic telemetry locally without sending data to external cloud servers, preserving 100% offline privacy.

---

## 11. Acceptance Criteria

- ✓ **Standard Interface**: 100% of engines implement `IEngineDiagnostics`.
- ✓ **Structured Logging**: Log entries formatted as scrubbed JSON with `correlation_id`.
- ✓ **Audit Preserved**: Mutative actions recorded as immutable `UniversalAuditEntry` logs.
- ✓ **Offline Telemetry**: Telemetry aggregated locally without cloud telemetry dependencies.
