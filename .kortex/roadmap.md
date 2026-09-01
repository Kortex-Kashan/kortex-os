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

## Application Completion track — M7.3: AI Studio ↔ Connector Engine Integration

**Status**: Completed

- [x] Production connector drivers (`connector-dummy`, `connector-http-rest`) now register automatically at boot (`kortex.api.kernel_bootstrap.register_production_connector_drivers`) — previously driver registration was a public API nothing in production ever called, so the registry was always empty at runtime.
- [x] Two governed AI tools (`connector_read_status`, `connector_send_action`) registered into the AI Engine's `ToolRegistry` at boot (`register_connector_ai_tools`) — the first tools ever registered anywhere in the platform, proving an AI Studio agent can reach the existing Connector Engine through the existing, unmodified `AIToolInvoker` → `KernelToolExecutionPort` → `CapabilityDispatcher` chain, with the mutating tool gated by the existing `DurableAIApprovalPolicy`/Workflow Approval Queue — no second approval mechanism.
- [x] Connector profile lifecycle capabilities (`kortex.connector.profile.register`/`.list`/`.delete`) wrapping the already-existing `ConnectorProfileManager`, tenant-scoped identically to the pre-existing `execute_action`/`get_profile` pattern (M6.3-1) — previously only `profile.get` was exposed, with no authorized way to create, list, or delete a connection.
- [x] Credential provisioning capability (`kortex.security.secret.put`, new `security:secret:write` permission) wrapping the already-existing `SecretStore.put_secret` — previously used only internally for the AI system's own credential — and now firing `SecuritySecretModifiedEvent`, whose class existed since before this milestone but had never been published by any code path.
- [x] Desktop "Connections" tab (`apps/desktop/src/features/connectors/components/ConnectionsTab.tsx`) extending the existing Connectors app — create/list/delete a connection, write-only credential entry never re-displayed after save.
- [x] Canonical vertical-slice tests proving the AI-tool-to-Connector-Engine path end to end (`backend/tests/integration/test_ai_connector_tool_invocation.py`): immediate read dispatch, approval-gated mutation with real resume, rejection, cross-tenant isolation, and duplicate-approval-event idempotency (proves the *pre-existing*, general `AgentOrchestrator` resume-CAS mechanism already prevents a double dispatch — no connector-specific fix was needed).
- [x] Two pre-existing tests fixed as a direct consequence of production driver auto-registration correctly overturning their "registry starts empty" assumption (`test_kernel_bootstrap.py`, `test_connector_api_http.py`).
- See `docs/architecture/m7.3_connector_integration_implementation_report.md` for the full certification report.

## Application Completion track — M7.4: Document Engine ↔ AI Studio Integration

**Status**: Completed

- [x] Tenant-isolation security gate closed on three Document Engine handlers reachable from Kernel dispatch (`execute_profile`, `transition_lifecycle`, `bind_template`) — previously derived tenant scope from caller-supplied data instead of the Kernel-verified `principal.tenant_id`, the same class of gap M6.3-1 fixed in the Connector Engine. Closed before any AI tool was registered, per this milestone's explicit ordering requirement.
- [x] `kortex.document.profile.list` capability wrapping the already-existing, already-tenant-scoped `DocumentOperationProfileManager.list_profiles` — previously no authorized way for a tenant, human or AI, to discover which operation profiles were available.
- [x] Two governed AI tools (`document_list_templates`, `document_generate`) registered into the AI Engine's `ToolRegistry` at boot (`register_document_ai_tools`) — the second engine (after Connector, M7.3) proven to reach the AI tool-invocation chain unmodified, with the mutating tool gated by the existing `DurableAIApprovalPolicy`/Workflow Approval Queue — no second approval mechanism, no Document-specific governance code.
- [x] Independently-discovered, genuinely pre-existing `Enum`-coercion gap in `transition_lifecycle` fixed (`core/dispatch.py`'s M7.2 dict→Pydantic coercion never resolved `Enum`-typed parameters) — found only because this milestone was the first code to exercise `transition_lifecycle` through real Kernel dispatch.
- [x] Content-security review of document output entering AI conversation history — confirmed the existing, generic `ToolResult.to_context_entry()` truncation/secret-scrubbing backstop (pre-existing since before M7.3) already bounds large document payloads; no new truncation code was needed, and a dedicated test proves it engages.
- [x] `AdapterSandbox` execution isolation verified untouched — the AI path reaches the identical sandbox instance through the identical, unmodified adapter-pipeline chain every other Document Engine caller already uses.
- [x] No desktop change made — AI Studio's existing generic tool-call/approval-card rendering already covers the new Document tools with zero UI-side changes, confirmed by a fresh, unchanged desktop test run rather than assumed from the absence of edits.
- [x] Canonical vertical-slice tests proving the AI-tool-to-Document-Engine path end to end (`backend/tests/integration/test_ai_document_tool_invocation.py`): immediate read dispatch, approval-gated generation with real resume, rejection, cross-tenant isolation, duplicate-approval-event idempotency, and large-output truncation.
- [x] Adversarial tenant-isolation coverage (`backend/tests/unit/test_document_tenant_isolation_dispatch.py`): same-tenant success and cross-tenant fail-closed for `execute_profile` and `transition_lifecycle`; RBAC/authentication gates and same-tenant-only results for `list_profiles`.
- See `docs/architecture/m7.4_document_engine_ai_integration_implementation_report.md` for the full certification report.
