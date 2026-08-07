# KORTEX OS — Platform Runtime Architecture Specification

Status: Approved Architecture
Version: 1.0.0
Authority: KORTEX OS Engineering Constitution & Platform Architecture
Target Release: KORTEX OS Platform Architecture
Target File: `docs/architecture/platform_runtime.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md` & `docs/architecture/engineering_constitution.md`)
- KORTEX OS Platform Principles (`docs/architecture/platform_principles.md`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)

---

## 1. Purpose

This document defines the canonical **Platform Runtime Architecture** for KORTEX OS.

The Platform Runtime coordinates the execution environment, process lifecycle, engine boot order, module initialization, memory management, thread/async task model, background worker pools, health monitoring, crash handling, and graceful shutdown across KORTEX OS.

---

## 2. Runtime Philosophy

1. **Kernel Authority**: The Kernel is the sole orchestration authority controlling runtime lifecycle, IoC dependency injection, engine boot, module activation, and shutdown.
2. **Local-First Asynchronous Runtime**: Built on Python `asyncio` event loops, non-blocking I/O primitives, and thread/process pools for local offline execution.
3. **Fail-Safe & Isolated**: Engines and modules operate inside isolated execution boundaries. An unhandled exception inside a module or subscriber never crashes the Kernel or sibling engines.
4. **Deterministic Boot & Shutdown**: System startup and shutdown sequence follow strict topological order based on dependency graphs.

---

## 3. System Startup

The runtime startup procedure follows a deterministic 4-phase sequence managed by the Kernel Boot Engine (`kortex.engines.boot`):

1. **Phase 0: Environment & Core Boot**: Initializes logging, configuration engine, environment variable parsing, and IoC container setup.
2. **Phase 1: Foundation Engines Startup**: Initializes Storage Engine, Registry Engine, Event Engine, and Security Engine in strict topological order.
3. **Phase 2: Execution & Infrastructure Engines Startup**: Initializes Workflow Engine, Recipe Engine, Document Engine, Connector Engine, Knowledge Engine, and AI Orchestration Engine.
4. **Phase 3: Business Modules & Capability Activation**: Loads installed business modules (`apps/`, `.kortex-module`), validates manifests, registers capabilities, and activates API/event routes.

---

## 4. System Shutdown

System shutdown executes in reverse topological order:
1. **Module Deactivation**: Suspends incoming capability routes and completes pending business commands.
2. **Infrastructure Engine Shutdown**: Terminated in reverse order (AI $\rightarrow$ Knowledge $\rightarrow$ Connector $\rightarrow$ Document $\rightarrow$ Recipe $\rightarrow$ Workflow).
3. **Foundation Engine Shutdown**: Flushes Event Engine queues, closes Storage Engine database sessions/object stores, and unregisters capabilities.
4. **Kernel Termination**: Releases IoC container singletons and flushes log buffers.

---

## 5. Engine Startup Order

Engines MUST start in strict topological dependency order:

$$\text{Boot Engine} \longrightarrow \text{Config Engine} \longrightarrow \text{Storage Engine} \longrightarrow \text{Registry Engine} \longrightarrow \text{Security Engine} \longrightarrow \text{Event Engine} \longrightarrow \text{Workflow Engine} \longrightarrow \text{Recipe Engine} \longrightarrow \text{Document Engine} \longrightarrow \text{Connector Engine} \longrightarrow \text{Knowledge Engine} \longrightarrow \text{AI Engine}$$

---

## 6. Module Startup Order

Modules start after all system engines reach `READY` status:
1. **Core Domain Modules**: HR, Organization, Security setup.
2. **Operational Modules**: Finance, Payroll, Inventory, CRM.
3. **Extension Modules**: Third-party marketplace modules.

---

## 7. Dependency Graph

The runtime maintains a directed acyclic graph (DAG) of all engines, modules, and capabilities. Circular dependencies are strictly prohibited and flagged during boot validation.

---

## 8. Capability Loading

Capabilities (`kortex.<domain>.<resource>.<action>`) are registered into the Kernel Registry during startup. Capability metadata is validated, authorized against RBAC matrices, and mapped to executable handlers.

---

## 9. Asset Loading

Assets (Recipes, Templates, Adapters, Knowledge Packs) are discovered via `AssetSystem`, unpacked in `IFileStore`, verified via SHA256/Ed25519 signatures, and loaded lazily upon first invocation.

---

## 10. Hot Reload

Development mode supports hot-reloading for declarative assets (Recipes, Templates, Profiles, Document Ontologies) without restarting the Kernel. Core Python engine code changes require a Kernel restart.

---

## 11. Plugin Loading

Sandboxed plugins (Document Adapters, Connector Drivers) are loaded dynamically by `PluginLoader` modules using dynamic class inspection (`BaseDocumentAdapter`, `BaseConnectorDriver`).

---

## 12. Memory Management

- **Streaming Payloads**: Large binary payloads (documents, object storage blobs) stream in fixed 64KB chunks to prevent memory spikes.
- **AST & Metadata Caching**: In-memory caches (`ICacheStore`) enforce strict LRU eviction policies and memory thresholds.

---

## 13. Thread Model

- **Main Thread**: Runs the primary `asyncio` event loop handling capability dispatches, event routing, and IoC orchestration.
- **Worker Thread Pool (`ThreadPoolExecutor`)**: Handles CPU-intensive tasks (cryptographic signatures, Jinja2 template rendering, static analysis).
- **Process Worker Pool (`ProcessPoolExecutor`)**: Sandboxed process isolation for adapter macro execution or heavy calculations.

---

## 14. Task Scheduler

The runtime includes a non-blocking task scheduler interfacing with Workflow Engine for cron schedules, delayed event triggers, and recurring background tasks.

---

## 15. Background Workers

Asynchronous background tasks execute via dedicated worker queues, publishing `OperationProgressEvent` updates without blocking the main event loop.

---

## 16. Health Monitoring

Every engine exposes `IEngineDiagnostics` (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`). The Kernel polls health endpoints every 15 seconds.

---

## 17. Recovery

Transient engine or task failures trigger recovery actions (`retry`, `rollback`, `checkpoint`, `resume`) coordinated via Workflow Engine and `IDocumentRecoveryProvider`.

---

## 18. Crash Handling

Unhandled exceptions inside module or capability handlers are caught by Kernel middleware, wrapped in `UniversalError`, recorded in audit logs, and returned as structured `UniversalResult.FAILURE` responses without crashing the process.

---

## 19. Graceful Shutdown

Upon receiving `SIGINT` or `SIGTERM`, the Kernel enters graceful shutdown mode:
1. Stops accepting new capability invocations.
2. Waits up to 30s for active tasks to complete.
3. Flushes Event Engine queues to `IDataStore`.
4. Closes database pools and file handlers cleanly.

---

## 20. Acceptance Criteria

- ✓ **Deterministic Startup**: Engines and modules boot in strict DAG order.
- ✓ **Non-Blocking Execution**: Main thread runs `asyncio` loop; CPU tasks run in thread pools.
- ✓ **Fail-Safe Runtime**: Engine crashes isolated; unhandled exceptions produce `UniversalError`.
- ✓ **Graceful Shutdown**: All I/O queues and database sessions flushed cleanly on exit.
