# KORTEX OS — Event Bus Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/event_bus.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)

---

## 1. Purpose

This document defines the canonical **Event Bus Architecture** for KORTEX OS.

The Event Bus (managed by Event Engine `kortex.engines.event`) provides the decoupled, asynchronous event publishing, subscription, filtering, ordering, replay, and dead-letter handling infrastructure for KORTEX OS.

---

## 2. Architecture Overview

In accordance with Article 16 of the KORTEX OS Engineering Constitution, all system components communicate through immutable events whenever practical:

```
┌─────────────────────────┐                               ┌─────────────────────────┐
│     Event Publisher     │                               │    Event Subscriber     │
│ (Engine or Business Mod)│                               │ (Engine or Business Mod)│
└────────────┬────────────┘                               └────────────▲────────────┘
             │                                                         │
             ▼                                                         │
┌──────────────────────────────────────────────────────────────────────┴──┐
│                             Event Bus Engine                            │
│  (Topic Router -> Filter -> Priority Queue -> Delivery Worker Pool)    │
└────────────┬────────────────────────────────────────────────────────────┘
             │
             ▼
┌─────────────────────────┐
│     Storage Engine      │
│ (IDataStore Event Log)  │
└─────────────────────────┘
```

---

## 3. Event Topics

Events are published to structured, hierarchical topic channels following canonical format:

$$\text{kortex}.\text{event}.<\text{domain}>.<\text{entity}>.<\text{action}>$$

### Examples:
- `kortex.event.hr.employee.created`
- `kortex.event.payroll.process.completed`
- `kortex.event.document.render.completed`
- `kortex.event.inventory.stock.low`

---

## 4. Subscriptions

Modules and engines register event subscribers during boot (`on_subscribe_events()`). Subscribers support exact topic matching, wildcard pattern matching (e.g. `kortex.event.hr.*`), and tenant-specific filtering.

---

## 5. Event Metadata (`KortexEvent`)

Every event published to the Event Bus is an immutable object inheriting from `UniversalMetadata`:

- `event_id`: Unique UUID string.
- `topic`: Canonical topic string (`kortex.event.<domain>.<entity>.<action>`).
- `tenant_id`: Multi-tenant organization identifier.
- `correlation_id`: Distributed trace correlation UUID string.
- `publisher_id`: Identity string of publishing engine or module.
- `payload`: Dictionary of event-specific domain data.
- `timestamp_utc`: ISO 8601 UTC timestamp string of event generation.

---

## 6. Correlation IDs

All published events MUST carry the `correlation_id` of the parent capability request or workflow instance that triggered the event, enabling end-to-end distributed tracing across modules.

---

## 7. Retry Mechanism

If a subscriber handler encounters a transient failure:
1. The Event Bus intercepts the exception without affecting sibling subscribers.
2. Re-queues event for retry using exponential backoff (initial delay 500ms, multiplier 2.0, max retries 3).

---

## 8. Dead Letter Queue (DLQ)

Events exceeding maximum retry attempts are transferred to the Dead Letter Queue in `IDataStore`. Operators inspect and re-trigger DLQ events manually via administrative capabilities.

---

## 9. Event Ordering

Events published within the same aggregate scope (`tenant_id` + `entity_id`) are guaranteed strictly ordered delivery using partition sequence counters.

---

## 10. Priority Queuing

The Event Bus maintains three priority queues:
- **HIGH**: System security alerts, kernel lifecycle events.
- **NORMAL**: Business commands, document rendering completion, workflow state transitions.
- **LOW**: Telemetry indexing, background search updates.

---

## 11. Filtering

Supports server-side filtering by topic patterns, payload attributes, tenant ID, and event severity levels before delivering payloads to subscriber callbacks.

---

## 12. Broadcast

Supports fan-out broadcast delivery where a single event (e.g. `kortex.event.system.shutdown`) is delivered simultaneously to all active subscriber queues.

---

## 13. Replay

Event Engine maintains an append-only event log in `IDataStore`. Systems can replay historical events from a target timestamp to rebuild state projections.

---

## 14. Snapshots

Read projections periodically create point-in-time state snapshots in `ICacheStore` to accelerate event replay performance.

---

## 15. Persistence

Every event payload is persisted synchronously to `IDataStore` (relational event log) before subscriber delivery to guarantee zero event loss during power failures.

---

## 16. Performance

- In-memory dispatch latency $\le$ 5ms per subscriber.
- Asynchronous worker pool processing up to 10,000 events/second locally.
- Non-blocking `asyncio` execution primitives.

---

## 17. Acceptance Criteria

- ✓ **Decoupled Architecture**: Publishers and subscribers have zero code dependencies.
- ✓ **Zero Event Loss**: Persistent event log in `IDataStore` before delivery.
- ✓ **Fault Isolation**: Subscriber failures do not crash publishers or sibling subscribers.
- ✓ **DLQ & Retry**: Automated retries and Dead Letter Queue handling specified.
