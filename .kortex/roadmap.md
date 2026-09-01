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

## Application Completion track — M7.1: Local Runtime Completion

**Status**: Completed

**Numbering note (not yet reconciled into this file's own Phase 6/7 sequence above)**: this work was commissioned and tracked as "Phase 7 — KORTEX Running / Application Completion," milestone M7.1, in a separate planning/implementation session — a distinct numbering track from this roadmap's own Phase 6/Phase 7 above, exactly as the M5.1–M6.4 workflow/AI-governance track already is (see `CHANGELOG.md`'s `[Unreleased]` entry). That external "Phase 7" is *not* the same thing as this file's "Phase 7: Production Hardening" — its remaining milestones (AI Studio Conversational Completion, Connector/Marketplace/Document write-paths, the first pilot business module) map more closely to this file's Phase 4-6 in intent. Reconciling this file's phase numbering with that external track is a documentation decision for the project owner, not made unilaterally here — recorded factually below in the meantime.

- [x] Sidecar process supervision actually spawns and monitors the backend (`apps/desktop/src-tauri/src/backend_process.rs`, `sidecar.rs`) — previously `SidecarSupervision::Disabled` unconditionally; a normal desktop launch no longer requires a human to start the backend by hand first.
- [x] Persistent master/signing key material (`apps/desktop/src-tauri/src/secure_keys.rs`, OS keyring-backed) — previously ephemeral per-process, invalidating every session/secret on each restart.
- [x] Bounded backend-startup readiness polling with a clear, recoverable failure state (`apps/desktop/src/auth/backendReadiness.ts`, `BackendUnavailableScreen.tsx`) — previously a single connection attempt with no retry.
- [x] First-run tenant/administrator bootstrap, fail-closed after first use, concurrency-safe (`kortex.security.bootstrap.create_admin` — `backend/src/kortex/engines/security/{auth,engine}.py`; `apps/desktop/src/auth/BootstrapScreen.tsx`) — previously no path existed for a user to create the first account on an empty install.
- [x] Cold-start acceptance test: fresh database → backend boot → first-run detection → bootstrap → authenticate → restart → persisted state remains valid (`backend/tests/e2e/test_m71_cold_start.py`).
- See `docs/architecture/m7.1_implementation_report.md` for the full certification report.

## Application Completion track — M7.2: AI Studio Conversational Completion

**Status**: Completed

- [x] Platform-wide dispatch fix: capability handlers declaring a Pydantic-model parameter (e.g. `LLMRequest`, `AgentTask`, `ResumeToken`) now actually work over the real dict-based HTTP/IPC path (`backend/src/kortex/core/dispatch.py`) — previously every such capability crashed with `AttributeError` outside a same-process test.
- [x] `kortex.ai.conversation.history.get` — the smallest new capability needed to read durable conversation history, wrapping the existing `AIMemoryManager` (`backend/src/kortex/engines/ai/engine.py`) — no new persistence subsystem.
- [x] Agent-orchestrated conversation turns (`kortex.ai.agent.orchestrate`/`resume`, including the automatic server-side approval-decided resume) are now recorded into the same durable history `kortex.ai.response.generate` already used, closing the gap where a chat surface built on agent orchestration could not recover its transcript after a restart.
- [x] AI Studio "Chat" tab (`apps/desktop/src/features/ai-studio/components/ChatPanel.tsx`) — a real conversational surface: sends every message through the existing agent-orchestration capability, rehydrates its transcript from durable backend history on load, and surfaces a governed tool-use approval as a lightweight card that deep-links into the existing Workflow Approval Queue (Option B) rather than duplicating any decision authority — the desktop never calls `kortex.ai.agent.resume` itself.
- [x] New `Textarea` design-system primitive (`design-system/components/textarea.tsx`).
- [x] Conversation-recovery-after-restart acceptance test (`backend/tests/e2e/test_m72_conversational_recovery.py`), plus the approval/rejection flows proven end-to-end through the real event-driven auto-resume chain (`backend/tests/integration/test_ai_durable_approval_vertical_slice.py`).
- See `docs/architecture/m7.2_implementation_report.md` for the full certification report.
