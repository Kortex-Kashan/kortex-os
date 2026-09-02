# KORTEX OS — Development Roadmap

## Phase 1: Core Microkernel & Runtime Foundation

**Status**: Completed

- [x] Project structure and scaffolding tool
- [x] Configuration files (pyproject.toml, .editorconfig, pre-commit)
- [x] Documentation skeleton
- [x] Kernel core implementation
- [x] Boot Engine
- [x] Configuration Engine
- [x] Registry Engine
- [x] Event Engine (in-memory async pub/sub)

## Phase 2: Business Foundation Layer

**Status**: In Progress

- [x] Storage Engine (`kortex.engines.storage` — `IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`)
- [x] Workflow Engine (`kortex.engines.workflow` — Sole runtime state machine and execution engine)
- [x] Recipe Engine (`kortex.engines.recipe` — Declarative parser, validator, pure deterministic compiler, packager, installer, catalog registry)
- [x] Document Engine (`kortex.engines.document` — Renderer registry & document lifecycle manager)
- [x] Connector Engine (`kortex.engines.connector` — Driver registry & integration driver host)

## Phase 3: Desktop Container & UI System

**Status**: Planned

- [ ] Tauri v2 application shell
- [ ] React + TypeScript + TailwindCSS setup
- [ ] IPC bridge (Tauri ↔ FastAPI)
- [ ] Design system (Dark mode, glassmorphism, responsive)

## Phase 4: AI Native Engine & Knowledge Layer

**Status**: Planned

- [ ] AI Engine (Ollama integration, streaming, structured output)
- [ ] Tool Engine (Capability → LLM tool schema)
- [x] Knowledge Engine (directed graph, versioned lineage & trust promotion, annotations, source ingestion, multi-modal search, knowledge pack loader — `KnowledgeEngine`, `kortex.engines.knowledge`; no vector store/RAG — see `docs/architecture/ARCHITECTURE_VERSION_1.0.md` §17)
- [ ] Document Intelligence Engine — **IMPLEMENTED, AWAITING REVIEW** (local PDF parsing via `pdfplumber`, local OCR via `rapidocr-onnxruntime`/ONNXRuntime — `DocumentIntelligenceEngine`, `kortex.engines.document_intelligence`; the previously-confirmed platform-level tenant-identity-confusion gap is now closed — see "Platform Security: Capability Identity Propagation" below — and proven closed by a real (no longer `xfail`) adversarial regression test; not checked off `[x]` until Chief Architect review and explicit commit/push authorization)

### Platform Security: Capability Identity Propagation — **IMPLEMENTED, AWAITING REVIEW**

Cross-cutting fix, not itself a numbered roadmap phase item: `CapabilityDispatcher` now constructs an immutable `CapabilityExecutionContext` (dispatcher-authenticated principal + authoritative tenant) and injects it into any capability handler that declares `requires_execution_context=True`, via unconditional, registration-time-validated binding — never a caller-suppliable value. Closes a confirmed, externally-reachable identity-confusion vulnerability found in 6 handler sites (Workflow: `decide_approval_request`, `delegate_approval_role`, `create_schedule`, `execute_external_operation`; Document Intelligence: `handle_pdf_parse`, `handle_ocr_extract`), plus an adjacent Workflow approval-impersonation defect (`approval.py::submit_decision`). See `backend/tests/unit/test_capability_identity_propagation_architecture.py` for the repo-wide static guard against recurrence.

## Phase 5: Advanced Business Engines & Approvals

**Status**: Planned

- [ ] Human-in-the-loop approval queues & notification schedules
- [ ] Process Intelligence Engine (telemetry & process mining)
- [ ] Security Engine (RBAC, encryption, audit)
- [ ] License Engine

## Phase 6: Pilot Business Modules

**Status**: Planned

- [ ] Module base contract
- [ ] Finance Module (Invoices, POs, Salary Sheets)
- [ ] HR & Payroll Module (Attendance, Leave, Payroll)
- [ ] Operations Module (Vehicle Tracking, Incidents)

## Phase 7: Production Hardening

**Status**: Planned

- [ ] Sentinel (Health monitoring, integrity)
- [ ] Monitoring Engine (Metrics, dashboards)
- [ ] Backup Engine
- [ ] Recovery Engine
- [ ] Update Engine
- [ ] Docker production builds
- [ ] Desktop installers (Tauri .msi / .exe / .dmg)
