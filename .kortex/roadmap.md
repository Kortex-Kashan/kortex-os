# KORTEX OS — Development Roadmap

## Phase 1: Core Microkernel & Runtime Foundation

**Status**: In Progress

- [x] Project structure and scaffolding tool
- [x] Configuration files (pyproject.toml, .editorconfig, pre-commit)
- [x] Documentation skeleton
- [ ] Kernel core implementation
- [ ] Boot Engine
- [ ] Configuration Engine
- [ ] Registry Engine
- [ ] Event Engine (in-memory async pub/sub)

## Phase 2: Security, Identity & Data Layer

**Status**: Planned

- [ ] PostgreSQL + SQLAlchemy 2.0 async engine
- [ ] Alembic migration infrastructure
- [ ] Identity Engine (Users, Workspaces, Tenants)
- [ ] Security Engine (RBAC, encryption, audit)
- [ ] License Engine

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

## Phase 5: Recipe Engine & Human Approval

**Status**: Planned

- [ ] Workflow Engine (State machine, persistence)
- [ ] Recipe schema (JSON/YAML declarative format)
- [ ] Human-in-the-loop approval queues
- [ ] Process Intelligence Engine (telemetry)

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
