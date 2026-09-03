# KORTEX OS — Architecture Decision Records (ADR Log)

All major architectural decisions for KORTEX OS are logged in this document in chronological order.

> **Note (ADR-0004, 2026-08-26): this file is retained as a historical record.**
> As of ADR-0004, new Architecture Decision Records are created in
> `docs/adr/` (which has its own lifecycle, template, and index —
> see `docs/adr/README.md`), not appended here. The entry below predates
> that process and is kept exactly as originally recorded — it is not
> deleted, renumbered, or migrated. See `docs/adr/ADR-0004-canonical-decision-log-location.md`
> for the full rationale.

---

## ADR #001: Local-First Architecture Foundation

- **Status**: Approved
- **Date**: 2026-08-06
- **Context**: KORTEX OS is defined as a Local-First AI Business Operating System. Business operations, organizational data, business recipes, and core AI processing must run securely on local user infrastructure without mandatory cloud connectivity or third-party SaaS dependencies.

### Decision
KORTEX OS adopts a **Local-First Architecture** as a fundamental platform principle:
1. **Data Sovereignty & Security**: All business data, database persistence (PostgreSQL / SQLite), vector embeddings, and organizational knowledge remain under local ownership and on-premise control.
2. **Offline Resilience**: The system operates fully without internet connectivity. Business recipes, workflows, module operations, and UI interactions never block on external network calls.
3. **Local AI Native**: Primary LLM execution relies on local inference engines (Ollama). Cloud AI providers are strictly optional secondary adapters.
4. **Cloud Enhanced**: Cloud capabilities (remote backup sync, multi-office federation, optional model offloading) serve exclusively as optional enhancements, never as hard operational dependencies.

### Consequences
- **Positive**: Zero latency dependence on SaaS APIs, complete data privacy compliance, uninterrupted business operation during network outages, predictable operational costs.
- **Negative / Trade-offs**: Higher hardware requirements on host machine (RAM/GPU for local LLMs), need for local sync/replication mechanisms, client-side database management complexity.

---

## ADR-014: Phase 7 — Production Hardening — Sentinel Engine

- **Status**: Approved
- **Date**: 2026-09-03
- **Context**: KORTEX OS requires production hardening through an unyielding, non-invasive health observation and invariant verification sentinel. Sentinel observes, classifies, and hands off failure episodes without performing destructive actions or process recovery.

### Decision
Implement `Phase 7 — Production Hardening — Sentinel Engine` under Clean Architecture and BaseEngine lifecycle:
1. **Separation of Health from Lifecycle**: Layer `SentinelStatus` (7 states: `STARTING`, `HEALTHY`, `DEGRADED`, `FAILED`, `UNKNOWN`, `STOPPING`, `DISABLED`) over existing `EngineState`.
2. **Explicit Observation Only**: Sentinel evaluates core probes (engine lifecycle, health checks, DB ping, registry descriptors, dependency graph, EventEngine availability, event-loop lag) and optional probes (tracked operations, heartbeats). Sentinel NEVER executes restarts, terminates processes, or performs recovery.
3. **Explicit Heartbeat Contract**: Define `IHeartbeatSource` protocol with monotonic clock, deterministic replacement, $2\times$ warning and $3\times$ failure multipliers, and startup/shutdown immunity.
4. **Deterministic Failure Classification & Circuit Breaker**: Classify failures into `TRANSIENT`, `REPEATED`, `PERSISTENT`, `CRASH_LOOP`, `EVENT_LOOP_STARVATION`, `STALLED_OPERATION`, and `DEADLOCK_SUSPECTED`. Implement Recovery Request Emission Circuit Breaker with ephemeral cooldown to prevent recovery-request event storms.
5. **Canonical Capabilities & Events**: Register 3 read-only, authenticated capabilities (`kortex.sentinel.health.get`, `kortex.sentinel.status.get`, `kortex.sentinel.diagnostics.get`) requiring `system:sentinel:read`. Standardize 6 canonical events with UUIDv4 IDs, UTC timestamps, and deterministic idempotency keys.
6. **Ephemeral Bounded Storage**: No database migrations. Diagnostic retention uses an in-memory bounded ring buffer (`maxlen=100`) with deterministic FIFO eviction.

### Consequences
- **Positive**: Complete observability across runtime components, deterministic fault isolation, non-blocking asynchronous monitoring, zero persistent schema overhead.
- **Negative / Trade-offs**: Diagnostic incident history is ephemeral and does not survive runtime process restarts.
