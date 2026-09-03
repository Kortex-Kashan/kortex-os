# ADR-0014: Phase 7 — Production Hardening — Sentinel Engine

- **Status**: ACCEPTED
- **Date**: 2026-09-03
- **Deciders**: Chief Architect (KASHAN), Antigravity (Implementation Engineer)
- **Target Component**: Sentinel Engine (`kortex.engines.sentinel`)

---

## Context and Problem Statement

KORTEX OS is an AI-powered local-first business operating system requiring enterprise-grade production reliability. While Phase 1 through Phase 6 established foundational microkernel architecture, storage, security, connectors, workflows, licensing, document intelligence, and pilot business modules, the system required unified architectural invariant verification, heartbeat watchdog management, deadlock and event loop lag detection, and a deterministic failure handoff mechanism.

The challenge was to design and implement the Sentinel Engine without violating the frozen Architecture Version 1.0.0 and the AI Engineering Constitution (`AGENTS.md`):
1. Sentinel must observe and report, never perform recovery.
2. Sentinel must avoid circular health evaluation.
3. Sentinel must not snoop on private attributes of other engines.
4. Sentinel must introduce no new database migrations.

---

## Decision Drivers

1. **Constitutional Invariant**: "Engines are infrastructure. They never contain business rules." (AGENTS.md)
2. **Separation of Concerns**: Sentinel observes, detects, and emits failure handoff events; the Recovery Engine handles recovery strategies.
3. **Local-First & Resource Efficiency**: Lightweight async non-blocking monitoring, bounded in-memory diagnostic state.
4. **Security Hardening**: Read-only internal capabilities with strict RBAC (`system:sentinel:read`) and execution context validation.

---

## Considered Options

- **Option 1**: Combine Sentinel and Recovery Engine into a single monolithic supervisory agent.
- **Option 2**: Re-use existing `EngineState` for dynamic operational health reporting.
- **Option 3 (Ratified)**: Implement `SentinelEngine` as a clean `BaseEngine` and `IEngineDiagnostics` infrastructure component, layering `SentinelStatus` over `EngineState`, using explicit `IHeartbeatSource` abstractions, bounded in-memory incident storage, and event-driven recovery handoff.

---

## Decision Outcome

Chosen Option: **Option 3**.

### Architectural Details

1. **`SentinelStatus` 7-State Model**:
   - `STARTING`, `HEALTHY`, `DEGRADED`, `FAILED`, `UNKNOWN`, `STOPPING`, `DISABLED`.
   - Clear deterministic mapping from `EngineState`, including unexpected stoppage mapping to `FAILED`.

2. **Non-Invasive Observation**:
   - Self-exclusion when polling registered engines via `health_check()`.
   - Event-loop scheduling latency probe via `await asyncio.sleep(0)`.
   - Distinction between `EVENT_LOOP_STARVATION` and `DEADLOCK_SUSPECTED`.

3. **Explicit Heartbeat Contract**:
   - `IHeartbeatSource` protocol.
   - Monotonic clock age calculations.
   - Warning ($2\times$) and failure ($3\times$) threshold multipliers.
   - Startup grace and shutdown immunity.

4. **Failure Classification & Circuit Breaking**:
   - `TRANSIENT`, `REPEATED`, `PERSISTENT`, `CRASH_LOOP`, `EVENT_LOOP_STARVATION`, `STALLED_OPERATION`, `DEADLOCK_SUSPECTED`.
   - Recovery Request Emission Circuit Breaker with ephemeral cooldown to prevent event storms.

5. **Canonical Capabilities & Events**:
   - Capabilities: `kortex.sentinel.health.get`, `kortex.sentinel.status.get`, `kortex.sentinel.diagnostics.get`.
   - Events: `kortex.sentinel.health.changed`, `kortex.sentinel.subsystem.failed`, `kortex.sentinel.subsystem.recovered`, `kortex.sentinel.deadlock.detected`, `kortex.sentinel.crash_loop.detected`, `kortex.sentinel.recovery.requested`.

6. **Storage Boundary**:
   - No database migrations or persistent tables.
   - Ephemeral in-memory ring buffer (`maxlen=100`) with deterministic FIFO eviction.

---

## Consequences

### Positive
- Fully decoupled, non-blocking health and liveness observation.
- Deterministic fault detection and isolated event delivery.
- Clean integration into Kernel boot and health check rollup.
- Full compliance with KORTEX Constitution and Architecture v1.0.0.

### Negative / Trade-offs
- Diagnostic incidents and crash-loop counters are process-local and do not persist across hard restarts.
