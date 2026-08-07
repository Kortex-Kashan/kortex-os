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
- [ ] Document Engine (`kortex.engines.document` — Renderer registry & document lifecycle manager — Pending)
- [ ] Connector Engine (`kortex.engines.connector` — Driver registry & integration driver host — Pending)

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
- [ ] Knowledge Engine (RAG, vector store, chunking)
- [ ] Document Intelligence Engine (PDF parsing, OCR)

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
