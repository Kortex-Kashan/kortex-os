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

**Status**: Completed

- [x] Storage Engine (`kortex.engines.storage` — `IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`)
- [x] Workflow Engine (`kortex.engines.workflow` — Sole runtime state machine and execution engine)
- [x] Recipe Engine (`kortex.engines.recipe` — Declarative parser, validator, pure deterministic compiler, packager, installer, catalog registry)
- [x] Document Engine (`kortex.engines.document` — Renderer registry & document lifecycle manager)
- [x] Connector Engine (`kortex.engines.connector` — Driver registry & integration driver host)

## Phase 3: Desktop Container & UI System

**Status**: Completed

- [x] Tauri v2 application shell
- [x] React + TypeScript + TailwindCSS setup
- [x] IPC bridge (Tauri ↔ FastAPI)
- [x] Design system (Dark mode, glassmorphism, responsive)

## Phase 4: AI Native Engine & Knowledge Layer

**Status**: In Progress

- [x] AI Engine (Ollama integration, streaming, structured output — `kortex.engines.ai`, M1–M13 closed; real `OllamaProvider` wired into the production boot path as part of the Phase 5 hardening below)
- [x] Tool Engine (Capability → LLM tool schema — AI Engine M6, Tool Invocation Engine)
- [x] Knowledge Engine (directed graph, versioned lineage & trust promotion, annotations, source ingestion, multi-modal search, knowledge pack loader — `KnowledgeEngine`, `kortex.engines.knowledge`; no vector store/RAG — see `docs/architecture/ARCHITECTURE_VERSION_1.0.md` §17)
- [ ] Document Intelligence Engine (PDF parsing, OCR — adapter interfaces and OCR/PDF type definitions exist in `kortex.engines.document`; no concrete processor implementation confirmed)

## Phase 5: Advanced Business Engines & Approvals

**Status**: In Progress

- [x] Human-in-the-loop approval queues & notification schedules (`DurableApprovalManager` — durable ticket lifecycle, expiry sweep daemon, cross-engine resume/cancel for both human and AI-originated requests; delivered as the M5.1–M6.4 workflow-governance hardening track, distinct from this roadmap's own "Phase 6" numbering below — see `git log --grep="M6\."` for that track's own milestone sequence)
- [ ] Process Intelligence Engine (telemetry & process mining)
- [x] Security Engine (RBAC, encryption, audit — `kortex.engines.security`, milestones 1–6 and 8 complete; tenant-isolation hardening extended further by the M5.1–M6.4 track above)
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
