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

**Status**: Completed

- [x] AI Engine (Ollama integration, streaming, structured output — `kortex.engines.ai`, M1–M13 closed; real `OllamaProvider` wired into the production boot path as part of the Phase 5 hardening below)
- [x] Tool Engine (Capability → LLM tool schema — AI Engine M6, Tool Invocation Engine)
- [x] Knowledge Engine (directed graph, versioned lineage & trust promotion, annotations, source ingestion, multi-modal search, knowledge pack loader — `KnowledgeEngine`, `kortex.engines.knowledge`; no vector store/RAG — see `docs/architecture/ARCHITECTURE_VERSION_1.0.md` §17)
- [x] Document Intelligence Engine — **ACCEPTED** (local PDF parsing via `pdfplumber`, local OCR via `rapidocr-onnxruntime`/ONNXRuntime — `DocumentIntelligenceEngine`, `kortex.engines.document_intelligence`; the previously-confirmed platform-level tenant-identity-confusion gap is now closed — see "Platform Security: Capability Identity Propagation" below — and proven closed by a real (no longer `xfail`) adversarial regression test). Formally accepted in the Final RC Ambiguity Resolution & Baseline Freeze pass; see the Phase 7 status-synchronization note below for the governance basis of this check-off.

### Platform Security: Capability Identity Propagation — **ACCEPTED**

Cross-cutting fix, not itself a numbered roadmap phase item: `CapabilityDispatcher` now constructs an immutable `CapabilityExecutionContext` (dispatcher-authenticated principal + authoritative tenant) and injects it into any capability handler that declares `requires_execution_context=True`, via unconditional, registration-time-validated binding — never a caller-suppliable value. Closes a confirmed, externally-reachable identity-confusion vulnerability found in 6 handler sites (Workflow: `decide_approval_request`, `delegate_approval_role`, `create_schedule`, `execute_external_operation`; Document Intelligence: `handle_pdf_parse`, `handle_ocr_extract`), plus an adjacent Workflow approval-impersonation defect (`approval.py::submit_decision`). See `backend/tests/unit/test_capability_identity_propagation_architecture.py` for the repo-wide static guard against recurrence.

## Phase 5: Advanced Business Engines & Approvals

**Status**: Completed

- [x] Human-in-the-loop approval queues & notification schedules (`DurableApprovalManager` — durable ticket lifecycle, expiry sweep daemon, cross-engine resume/cancel for both human and AI-originated requests; delivered as the M5.1–M6.4 workflow-governance hardening track, distinct from this roadmap's own "Phase 6" numbering below — see `git log --grep="M6\."` for that track's own milestone sequence)
- [x] Process Intelligence Engine — **ACCEPTED** (DFG process mining, trace variant extraction, bottleneck diagnostics, throughput KPIs — `kortex.engines.process_intelligence`; bounded $\le 100$ nodes, $\le 500$ edges; structural tenant isolation via `TenantScopedProcessAnalyticsRepository`)
- [x] License Engine (M5.7) — **ACCEPTED** (offline-first cryptographic licensing, Ed25519 token verification via `LocalCrypto`, KORTEX constrained canonicalization profile, `TenantScopedLicenseRepository` with concurrency-safe `active_tenant_id` unique constraint, `ILicenseProvider` protocol implementation, clock-tamper/rollback detection with Canonical Community fallback, Alembic migration `b4e89f123c5a`)

## Phase 6: Pilot Business Modules

**Status**: Complete

- [x] Module base contract (`kortex.core.base_module.BaseModule` — minimal lifecycle contract, a sibling to `BaseEngine`, proven in production by `FinanceModule` below; the full platform-scale contract in `docs/architecture/business_module_architecture.md` — packaging, DAG dependency resolution, IoC container, dynamic discovery, upgrade/rollback — remains deferred, not required for a pilot module to function)
- [x] Finance Module — first pilot business module complete (`kortex.finance.invoice.create`/`.get`: real Kernel capability dispatch, `IDataStore` persistence, principal-derived tenant isolation, RBAC, production boot registration — see `docs/architecture/finance_module_pilot_implementation_report.md`). Purchase Orders and Salary Sheets are future Finance-domain expansion, not required for this pilot's completion, and remain unauthorized pending separate planning.
- [x] HR & Payroll Module — second pilot business module complete (Employee Management, Attendance & Overtime, Leave Balances & Requests, Monthly Payroll Runs, Payslip retrieval, 6 relational tables, Alembic migration, CapabilityExecutionContext tenant isolation, event kortex.event.payroll.run_finalized — see docs/architecture/hr_payroll_module_pilot_implementation_report.md)
- [x] Operations Module (Vehicle Tracking, Incident Management) — third pilot business module complete (Fleet Vehicle Master, (tenant_id, plate) and (tenant_id, vin) uniqueness, driver assignment invariants, monotonic odometer tracking logs & history, 14 capabilities, incident reporting & sequential numbering with concurrency retry, intermediate status transitions, investigation resolution & terminal closure immutability, 3 relational tables, Alembic migration `4c99c2ff7376` with explicit parent `c7d8e9f1a2b3`, `CapabilityExecutionContext` tenant isolation, domain events `vehicle.status_changed`, `incident.reported`, `incident.closed` — see `docs/architecture/operations_module_pilot_implementation_report.md`)

## Phase 7: Production Hardening

**Status**: Completed

- [x] Sentinel (Health monitoring, integrity) — **ACCEPTED** (`kortex.engines.sentinel`; 7-state health model, heartbeat manager, deadlock/event-loop-starvation detection, integrity verifier, bounded incident store with crash-loop detection; zero database tables. Reconciliation §5.2)
- [x] Monitoring Engine (Metrics, dashboards) — **ACCEPTED** (`kortex.engines.monitoring`; metric registry with cardinality limits, rolling time-series buffers, diagnostics normalizer, threshold evaluator with hysteresis/cooldown, 4 capabilities; zero database tables. Reconciliation §5.3)
- [x] Backup Engine — **ACCEPTED** (`kortex.engines.backup`; AES-256-GCM authenticated encryption failing closed on missing/invalid key, native SQLite online backup, archive verification with traversal/ZIP-bomb defenses, retention with an inviolable last-valid-backup invariant. Reconciliation §5.4)
- [x] Recovery Engine — **ACCEPTED** (`kortex.engines.recovery`; durable filesystem journal, mandatory pre-recovery checkpoint, staged-only forward migration, 4-tier verification with automated rollback and fail-closed operator halt. Reconciliation §5.5)
- [x] Update Engine — **ACCEPTED** (`kortex.engines.update`; Ed25519-signed manifest verification, staged archive extraction with full path/ZIP-bomb/symlink defenses, mandatory backup checkpoint, 3-layer rollback authority, journal-driven crash recovery, explicit filesystem-swap-≠-runtime-activation semantics. Reconciliation §5.6)
- [x] Docker production builds — **ACCEPTED** (`docker/Dockerfile.backend` + `entrypoint.sh` + compose files; multi-stage build, non-root runtime, fail-closed secret preflight, Alembic migration ahead of serving traffic, `/health` smoke test, image-layer secret/VCS scan — all exercised by the Backend CI `docker` job on every push. Reconciliation §5.7)
- [x] Desktop installers (Tauri .msi / .exe / .dmg) — **ACCEPTED FOR THE WINDOWS RC SCOPE**: `.msi` (WiX) and `.exe` (NSIS) are both built, installed, and lifecycle-verified in CI and locally, bundling the frozen Python backend. **`.dmg` (macOS) is NOT delivered** and is explicitly deferred as future platform expansion (OD-DI-3) — this bullet is checked off against the accepted Windows RC distribution scope only, not against macOS. Artifacts are **unsigned**; signing is a public-distribution requirement, not a technical-RC one. Reconciliation §5.8.

**Phase 7 status-synchronization note (governance basis for the check-offs above)**: this file's own established convention — stated verbatim on its previously-unchecked Phase 4/5 items — is that a work item is "not checked off `[x]` until Chief Architect review and explicit acceptance." That acceptance has now been given for every Phase 7 work package, and for the previously-pending Phase 4 (Document Intelligence, Capability Identity Propagation) and Phase 5 (Process Intelligence, License Engine) items, in the Final RC Ambiguity Resolution & Baseline Freeze pass. These check-offs therefore follow this file's own rule rather than overriding it; they were not applied unilaterally by any implementation pass. Full per-package evidence lives in `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`; the consolidated release view lives in `docs/release/RELEASE_CANDIDATE_READINESS.md`.

**Supporting work accepted alongside Phase 7, deliberately NOT added as roadmap checklist items** (they are **REPOSITORY-DERIVED**, not roadmap line items, and inventing roadmap requirements is forbidden by the reconciliation document §1): Database Migration Wiring (Alembic foundation), CI/CD (`backend-ci.yml`/`desktop-ci.yml`), and Production Secret Storage / Native Windows Keyring (activating the real Windows Credential Manager backend). All three are accepted and evidenced in the reconciliation document (§5.1, §5.9, §5.11).

**Not part of the Phase 7 RC scope, explicitly deferred**: bare/server (non-Docker, non-desktop) fresh-machine validation; macOS/Linux desktop distribution; installer footprint optimization; Docker OS-credential-store parity (OD-2). See the reconciliation document and RC readiness document for each item's definitive status.

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

## Application Completion track — M7.5: Knowledge Engine ↔ AI Studio Integration

**Status**: Completed

- [x] Tenant-isolation security gate closed on all five Knowledge Engine capabilities (`search`, `traverse_graph`, `list_nodes`, `index_source`, `load_pack`) — previously none of the five handlers accepted a Kernel-verified `principal` at all, a live gap already reachable via the existing desktop Knowledge UI, independent of any AI exposure. Closed before the AI tool was registered, per the established M7.3/M7.4 ordering discipline.
- [x] One governed, deliberately read-only AI tool (`knowledge_search`) registered into the AI Engine's `ToolRegistry` at boot (`register_knowledge_ai_tools`) — the third engine (after Connector, M7.3, and Document, M7.4) proven to reach the AI tool-invocation chain unmodified. No mutation-class Knowledge tool was added; left as an explicit open question pending product evidence, not silently included or foreclosed.
- [x] `KnowledgeQuery.tenant_id` given a `"default"` fallback value (mirroring `document.models.BindingContext.tenant_id`'s identical precedent) so the AI tool's schema can omit `tenant_id` entirely, as required — tenant identity comes exclusively from the verified principal.
- [x] Independently-discovered diagnostics bug fixed: `KnowledgeEngine._REGISTERED_CAPABILITIES` was missing `kortex.knowledge.graph.list` despite it being a real, registered, dispatchable capability.
- [x] Verified (not re-fixed) that M7.2's dispatch dict→Pydantic coercion already resolved a historical, documented `KnowledgeQuery`-over-real-IPC defect — closing the master implementation prompt's required Enum/request-coercion audit with evidence rather than assumption.
- [x] AI-tool-registration hygiene: the copy-pasted idempotency guard across `register_connector_ai_tools`/`register_document_ai_tools`/`register_knowledge_ai_tools` was extracted into a shared `_register_tool_if_absent` helper, closing a coherence gap the M7.5 planning report's own AI-tool-surface investigation identified.
- [x] Content-security review of knowledge search results entering AI conversation history — confirmed the existing, generic `ToolResult.to_context_entry()` truncation/secret-scrubbing backstop already bounds a large, many-node search result; no new truncation code was needed, proven by a dedicated test.
- [x] No desktop change made — AI Studio's existing generic tool-call rendering already covers `knowledge_search` with zero UI-side changes; the existing desktop Knowledge UI's calls are unaffected in shape by the tenant-isolation fix, confirmed by a fresh, unchanged desktop test run.
- [x] Canonical AI vertical-slice tests (`backend/tests/integration/test_ai_knowledge_tool_invocation.py`): immediate read dispatch with conversation-history recording, cross-tenant isolation proven through the real AI path, and large-result truncation.
- [x] Adversarial tenant-isolation coverage (`backend/tests/unit/test_knowledge_tenant_isolation_dispatch.py`): same-tenant success and cross-tenant fail-closed for all five hardened capabilities.
- See `docs/architecture/m7.5_knowledge_engine_ai_integration_implementation_report.md` for the full certification report.

## Application Completion track — M7.6: AI Execution Control Plane Hardening

**Status**: Completed

- [x] Closed a tenant-concurrency-control gap on the AI approval-resume path: `AIOrchestrationEngine._on_approval_decided` — the only path that resumes an approved AI-originated mutation in production — now acquires the same `TenantConcurrencyThrottler` agent slot the synchronous `orchestrate_agent`/`resume_agent` entry points already enforce, closing an asymmetry that let every mutating AI tool's approval-resume traffic (Connector's `connector_send_action`, Document's `document_generate`) bypass the per-tenant concurrent-agent-workflow cap.
- [x] A saturated tenant's approval-resume now defers safely (task stays `PAUSED_FOR_APPROVAL`, the already-durable approval decision is never lost, a later redelivery can still resume it) rather than silently bypassing the cap — proven by 8 new adversarial tests covering acquisition, release-on-success, release-on-failure, exactly-once acquisition, saturated-tenant deferral, cross-tenant independence, zero acquisition on rejection, and duplicate-event idempotency.
- [x] Closed the AI-tool-registration test-coverage gap the M7.5 planning report flagged: Document and Knowledge AI tools now have the same boot-time registration and idempotency test coverage Connector tools already had.
- [x] Closed a telemetry asymmetry: a successful AI tool invocation now publishes a domain event (`AIToolCompletedEvent`) and increments an exporter counter, matching `emit_tool_failed`/`emit_tool_denied`'s existing behavior — previously a successful completion's already-computed latency was recorded only into internal diagnostics, invisible to telemetry subscribers/exporters.
- [x] No new throttling mechanism, no AI tool consolidation, no desktop change, no Marketplace/business-module/RecipeEngine work — the existing `TenantConcurrencyThrottler` remains the sole, unmodified, authoritative tenant-concurrency mechanism, now applied uniformly.
- See `docs/architecture/m7.6_ai_execution_control_plane_hardening_implementation_report.md` for the full certification report.

## Integration Hub track — M1: MCP Foundation + Capability Projection Bridge

**Status**: M1 — ACCEPTED / COMPLETE (PRE-EXISTING CI DEFECT RESOLVED)

- [x] Streamable HTTP MCP Client Transport (`kortex.engines.connector.drivers.mcp_driver.McpConnectorDriver`) — official Python MCP SDK integration (`mcp>=1.2.0`), tenant-scoped connection lifecycle and connection pool, persistent streamable HTTP transport sessions, Bearer token credential resolution via `SecretStore` without credential leakage across tenants.
- [x] Dynamic Capability Registration & Schema Mapping — maps MCP `tools/list` response to KORTEX `CapabilityDescriptor` records (`kortex.mcp.<profile_id>.<tool_name>`), registered dynamically into `RegistryEngine`.
- [x] Tenant-Scoped Capability Projection Bridge — `CapabilityProjection` filters dynamically registered MCP capabilities strictly by owning `tenant_id`, ensuring tenant isolation across discovery and invocation.
- [x] Desktop Connections UI Integration — "Add MCP Server" modal form (`McpConnectionForm.tsx`) in Desktop Connectors tab, supporting Streamable HTTP endpoint configuration and write-only credential entry.
- [x] Visual Workflow Canvas Palette Integration — `CapabilityPalette.tsx` updated to allow dynamically discovered `kortex.mcp.*` capabilities for visual workflow authoring.
- [x] Acceptance & CI Reconciliation:
  - **M1 implementation**: COMPLETE (`da93c64`, `79040e9`)
  - **M1 acceptance**: ACCEPTED by Chief Architect
  - **Desktop CI**: GREEN (`pnpm test`, `cargo check`, Vitest all passing)
  - **M1-specific tests**: PASS (32/32 tests: 24 unit in `test_mcp_driver.py`, 8 integration in `test_mcp_integration_slice.py`, plus frontend component tests)
  - **Backend full suite (at M1 acceptance time)**: 1 pre-existing failure (`test_restart_recovery_ready_and_approved_workflows`)
  - **Overall repository CI (at M1 acceptance time)**: NOT GREEN due to pre-existing workflow durability defect (`DEFECT-002`, optimistic-lock race condition on durability restart recovery). Classified as PRE-EXISTING BASELINE DEFECT, reproduced on baseline `63460cb`. **RESOLVED** by commit `6cc223b` (`fix(storage): serialize concurrent SQLite sessions`) — root cause traced to `DatabaseEngineManager` lacking application-level SQLite writer serialization, not the Workflow Engine itself. See `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §5.12 and `docs/release/rc-testing/KNOWN_FINDINGS.md` DEFECT-002.

## Integration Hub track — M2: GitHub OAuth Connector

**Status**: M2 — ACCEPTED / COMPLETE (2 PRE-EXISTING BASELINE DEFECTS RESOLVED)

Second Integration Hub milestone, following M1's dynamic per-profile capability pattern
(`kortex.mcp.<profile_id>.<tool_name>`) rather than F5's boot-time-fixed one — a connected
GitHub profile registers/unregisters its own capabilities on connect/disconnect, exactly like
an MCP profile does, with `owner_id = profile_id`. See
`docs/architecture/integration_hub_m2_github_oauth_connector_implementation_report.md` for the
full report, including the three rounds of architecture correction this milestone went through
before implementation (capability identity, engine ownership, credential binding/state
security/disconnect authority, and GitHub token-lifecycle handling).

- [x] `IntegrationOAuthManager` (`kortex.engines.security.integration_oauth_manager`) —
  SecurityEngine-owned OAuth begin/complete/status/disconnect-credential, GitHub token
  refresh with mandatory rotation and per-`(tenant_id, profile_id)` synchronization,
  `REAUTHORIZATION_REQUIRED` transition on an invalid/expired refresh token.
- [x] `OAuthIntegrationCredentialRecord`/`OAuthStateNonceRecord` (`kortex.engines.security.models`)
  — opaque `SecretStore` handle keyed by `(tenant_id, profile_id)` (never the reverse), atomic
  single-use OAuth `state` consumption (`UPDATE ... WHERE consumed_at IS NULL`).
- [x] `GitHubIntegrationOAuthProvider` (`kortex.engines.security.oauth.github_provider`) —
  Authorization Code flow with `offline_access`, explicit non-expiring/no-refresh-token
  compatibility.
- [x] `kortex.security.integration_oauth.begin/complete/status` capabilities — hard
  tenant/principal binding on complete, mirroring `oauth_link_complete_capability`'s existing
  precedent over the identical desktop deep-link transport.
- [x] GitHub curated actions (`kortex.engines.connector.github_actions`) — static descriptor
  catalog (`user_get`, `repo_get`, `issues_list`, `issue_create`) reusing `actions.py`'s
  `make_action_handler()` unmodified; dynamic per-profile registration/unregistration directly
  on `RegistryEngine`, wired into `ConnectorEngine.register_profile()`/`delete_profile()`.
- [x] `kortex.connector.integration.disconnect` — single, backend-authoritative, idempotent
  capability: unregisters capabilities first, unconditionally, before revoking the credential,
  so no frontend two-call sequence can leave a stale, dispatchable capability behind.
- [x] `secret_resolver` grows a third, backward-compatible `profile_id` parameter
  (`ConnectorEngine`/`ConnectorPipeline`) so credential resolution can be bound to the exact
  profile it was issued to — MCP and every other plain-secret connector unaffected.
- [x] Desktop "Connect with GitHub" flow (`GitHubConnectionForm.tsx`, `githubConnectorOAuth.ts`,
  `useConnectorOAuthDeepLink.ts` on a distinct `kortex-connector-auth://` scheme) and a
  provider-aware "Disconnect" action in the Connections tab.
- [x] Tests: 36 new backend tests (unit: `test_integration_oauth_manager.py`,
  `test_integration_oauth_token_lifecycle.py`, `test_github_actions.py`; integration:
  `test_github_connector_oauth_integration.py`) plus 6 new frontend tests
  (`GitHubConnectionForm.test.tsx`), including the concurrency-critical single-use-state and
  refresh-rotation-synchronization guarantees proven with `asyncio.gather`, not merely asserted.
- [x] Acceptance & CI Reconciliation:
  - **M2 acceptance**: ACCEPTED by Chief Architect
  - **M2-specific tests**: PASS (36/36 new backend tests, 6/6 new frontend tests)
  - **Desktop CI**: `tsc --noEmit` clean; `vitest run` 169/169 passing (163 pre-existing + 6 new)
  - **Backend full suite (at M2 implementation time)**: `pytest backend/tests -q` → 4023 passed, 4 failed, 2 skipped
  - **Overall repository CI (at M2 implementation time)**: NOT GREEN due to two PRE-EXISTING BASELINE DEFECTS, neither an M2
    regression (both verified via `git stash` against unmodified `main` HEAD `f5d57cb`):
    - `DEFECT-002` (carried over from M1, root cause later traced to `DatabaseEngineManager`
      SQLite session serialization, not the Workflow Engine) —
      `test_workflow_durability.py::test_restart_recovery_ready_and_approved_workflows`.
      **RESOLVED** by commit `6cc223b`.
    - `DEFECT-003` (new, Update Engine test-fixture staleness) — 3 tests in
      `test_update_integration.py`, root cause: a hardcoded `expires_at` fixture date now in the
      past. **RESOLVED** by commit `283cf87`. See `docs/release/rc-testing/KNOWN_FINDINGS.md`.
  - **CI hygiene**: commit `bdb9461` fixed a pre-existing `mypy` failure on `mcp.*` imports
    (missing type stubs) that was aborting the CI lint/type-check step before tests could run.
    Repository-derived supporting work, not M2 engineering scope.
  - **Current reconciled state**: with both defects and the mypy gate resolved, Backend CI is
    green at HEAD `283cf87` (full pytest suite, lint, and type-check), and the Docker build/smoke
    test also passes at this HEAD. See `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`
    §5.13 for the consolidated record.

## Python + Desktop Automation

**Status**: Implementation complete — awaiting Chief Architect review and acceptance

Closes the gap `docs/architecture/phase5_locked_architecture_spec.md` §2 explicitly deferred as
"Phase 6 Non-Goals" for the Phase 5 identity/mTLS milestone: a capability-transport protocol over
the existing Agent Gateway mTLS session, and the native Windows UI automation execution layer
behind it. Reuses the existing capability architecture (`CapabilityDispatcher`, `SecurityEngine`,
Agent Gateway, .NET Desktop Agent) unmodified in its enforcement semantics; introduces no second
authorization authority, no second workflow engine, and no generic execution/script channel.

- [x] `agent.proto` — typed `DesktopLaunch/Click/Type/ReadTextCommand` + `DesktopCommandResult`
  messages over the existing session stream. Application launch resolves an operator-managed
  allow-list key only, never a caller-supplied path or command line; UI targeting is
  AutomationId/Name/ControlType only (no screen coordinates); zero or ambiguous matches fail
  closed. Verified as a closed set by an exact field-list contract test.
- [x] Desktop-command transport (`kortex.engines.agent_gateway.engine`) — correlated
  request/response queue over the existing per-session stream, gated on a new
  `AgentSession.identity_confirmed` flag so a session may push/receive desktop commands only
  after passing the machine-installation binding check Phase 5 established (closes a gap found
  during review: that check previously applied only to the `status` heartbeat, not to this new
  privileged traffic on the same stream).
- [x] `kortex.desktop.launch`/`.click`/`.type`/`.read_text` (`kortex.engines.desktop_automation`)
  — registered through the unmodified `Kernel.invoke_capability` → `CapabilityDispatcher` →
  `SecurityEngine` enforcement path; wired into the production boot path
  (`kernel_bootstrap.build_and_boot_kernel`) with graceful degradation (capabilities registered,
  gateway not listening) when Desktop Agent PKI has not been provisioned.
- [x] `DesktopAutomationHandler`/`ApplicationAllowList` (`apps/desktop-agent`) — FlaUI/UIA3
  execution: allow-listed launch with fail-closed argument restriction, deterministic UI-element
  resolution, a single dedicated worker thread serializing all UIA/COM calls (not safe for
  concurrent access from the per-command background tasks the agent's session loop dispatches),
  and a bounded verify-after-set retry closing a real type-then-click timing race found during
  local E2E verification.
- [x] Genuine Windows E2E (`DesktopAutomationHandlerE2ETests`) — a minimal, purpose-built WinForms
  fixture (`KortexAutomationTestApp`) proving the full launch → type → click → read_text round
  trip against a real, freshly-computed result (7 × 8 = 56), not an echoed input; plus adversarial
  cases (disallowed application, missing window, missing/ambiguous element, killed process,
  launch failure) against the same real fixture.
- [x] Desktop Agent CI (`desktop-ci.yml`, `desktop-agent` job) — builds and runs the full .NET test
  suite, including the real FlaUI E2E, on a `windows-latest` runner (the same real-desktop-session
  property the pre-existing `windows-installer` job already relies on). Verified GREEN on real
  GitHub Actions (PR #2, run `35426973160`, commit `bb05069`) after three closeout-phase fixes to
  genuine defects the job's first real run surfaced (it had only ever been validated locally
  before): (1) `dotnet build` invoked with two project arguments in one call — MSBuild only accepts
  one (`MSB1008`); (2) no `.gitattributes`, so the runner's default `core.autocrlf=true` rewrote
  LF-committed `.cs` files to CRLF at checkout, violating the repo's own `.editorconfig`
  (`end_of_line = lf`) before `dotnet format --verify-no-changes` ran; (3) a real, pre-existing
  `xUnit2013` analyzer violation in `NonExportableKeyTests.cs` that `dotnet format` had been
  flagging as a warning throughout this milestone but was never fixed. See commits `179e82c`,
  `ba75e52`, `bb05069`.
- **Acceptance & CI Reconciliation**:
  - **Implementation commit**: `c79d57e` (`feat(desktop-automation): implement native Windows UI
    automation (Phase 6)`)
  - **Backend full suite**: `pytest` → 4,261 passed, 0 failed, 4 skipped (environment-gated: no
    local Ollama instance; Windows-only branches)
  - **Desktop Agent .NET suite (local, unelevated token)**: `dotnet test` → 19 passed, 0 failed, 3
    skipped (require an elevated/SYSTEM token for machine-scoped CNG key creation; see
    `NonExportableKeyTests.cs`'s own `[RequiresElevationFact]`)
  - **Desktop Agent .NET suite (real GitHub Actions `windows-latest` runner, PR #2 run
    `35426973160`)**: `dotnet test` → 22 passed, 0 failed, 0 skipped — the runner's elevated token
    exercises the CNG key tests that skip locally, so this is strictly more coverage than the local
    run, not different behavior.
  - **mypy**: clean on both `--platform win32` and `--platform linux`
  - **ruff**: clean (lint and format)
  - **dotnet format**: clean
  - **Security review**: two findings fixed — Windows command-line argument quoting
    (`ProcessStartInfo.ArgumentList` replacing a hand-rolled escaper that mishandled trailing
    backslashes) and local-path disclosure in `LAUNCH_FAILED` error messages (agent-internal
    exception text no longer reaches the wire-facing result).
  - **Known, documented limitations** (not blocking, not this milestone's scope to close):
    single-agent-per-tenant is the practical operating model beyond explicit `agent_id`
    disambiguation; `ApplicationAllowList` requires an agent restart to pick up changes (no
    hot-reload); the allow-list has no tenant/principal scoping. The persisted Graphify knowledge
    graph has been re-indexed against this work's architectural changes (21,747→21,906 nodes,
    51,754→52,069 edges); the Python↔C# gRPC boundary is verified as two separately-confirmed
    endpoints rather than one unified graph path, since static AST extraction cannot see across
    that language boundary.
  - **Acceptance**: PENDING — not yet reviewed by Chief Architect. Following this file's own
    established convention (stated verbatim on this document's Phase 4/5 items), this entry is not
    checked off as accepted until that review occurs.

