# Changelog

All notable changes to KORTEX OS will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Note on this entry

This file was not updated between [0.2.0] (2026-08-07) and this entry — the Document, Connector, Security, and Knowledge Engines, the AI Orchestration Engine (M1–M13), and the entire Tauri desktop track (Phase 3 shell through Slice 4.7) all shipped in that gap without a corresponding changelog record. That gap is not backfilled here; each of those areas has its own spec/certification documents under `docs/architecture/`. This entry covers only the workflow/AI-governance hardening work reconciled and integrated in this change (milestone track M5.1–M6.4, distinct from this roadmap's own "Phase 5/6" numbering — see `.kortex/roadmap.md`).

### Added

- **Durable Workflow Governance (`kortex.engines.workflow`)**: `DurableApprovalManager` — persisted approval tickets with a real expiry sweep daemon (`approval_sweep_enabled`, default on, 30s poll interval), atomic `PENDING`→terminal state transitions under genuine concurrency, and a production-reachable `EXPIRED` event published through the existing `workflow.approval.decided` contract. Durable scheduling runtime with IANA-timezone-aware cron evaluation, atomic schedule claiming, and timeout retry. Governed `ExternalExecutionManager` — real Connector dispatch (no more fabricated success responses), idempotency replay protection, approval resume/cancel with action-fingerprint verification, and boot-time recovery of both stranded `RUNNING` and stranded `WAITING_APPROVAL` executions.
- **Governed Multi-Tenant AI Runtime (`kortex.engines.ai`)**: a real system principal identity for the AI engine, tenant-isolated `generate_response`/`invoke_tool`, live guardrail/quota/tool-policy enforcement on the actual generation path, a durable approval bridge for AI-proposed mutating actions (with self-approval prevention), and a production `OllamaProvider`.
- **Desktop Workflow & AI Governance control center**: approval queue, schedule manager, external-execution inspector, and instance timeline, rebuilt against the real backend contract; surfaces AI-vs-human requester identity on each approval.
- **Security fix**: `/events/stream` was relaying the live, fully-usable `decider_session_token` verbatim to every authenticated same-tenant WebSocket client, not just the approver — a session-token leak to any bystander user in the tenant. Fixed by extending the existing sensitive-key redaction path to the outbound event-stream relay.
- **Tenant-isolation hardening**: closed a cross-tenant gap on 12 previously tenant-blind Workflow Engine capability handlers, and a cross-tenant Connector-profile/secret lookup gap (`ConnectorProfile` gained a real `tenant_id`).
- **Local Runtime Completion (M7.1, tracked as "Phase 7 — KORTEX Running / Application Completion" in a separate planning session — see `.kortex/roadmap.md`'s own numbering note)**: the desktop app now spawns and supervises the real backend process itself (`apps/desktop/src-tauri/src/backend_process.rs`, reusing the previously-unwired `sidecar.rs` machinery) instead of requiring it already running; persistent OS-keyring-backed master/signing keys (`secure_keys.rs`) so sessions and encrypted secrets survive a restart instead of being invalidated by the old per-process ephemeral key; bounded backend-startup readiness polling with a recoverable failure UI (`backendReadiness.ts`, `BackendUnavailableScreen`); and a first-run tenant/administrator bootstrap capability (`kortex.security.bootstrap.create_admin`) that is fail-closed and concurrency-safe (a fixed sentinel principal plus the existing `PrincipalRecord` unique constraint), dynamically grants the new administrator every currently-registered permission, and closes permanently after first use — closing the gap where no path existed anywhere to create the first account on a fresh install. Full cold-start-to-restart acceptance coverage in `backend/tests/e2e/test_m71_cold_start.py`. See `docs/architecture/m7.1_implementation_report.md`.
- Full backend regression suite: 2,308 passed, 2 skipped (documented Ollama-unavailable skips), 2 failed — both independently reproduced and root-caused as pre-existing and unrelated (a missing `tzdata` package in this environment; a timing-sensitive flake in an untouched M5-era test file). Desktop suite: 440 passed, 0 failed.
- M7.1 final certification (fresh full-suite runs, this change): backend **2,328 passed, 2 skipped, 1 failed** (the same pre-existing, unrelated `tzdata` failure — zero regressions, +18 net-new tests); desktop **485 passed, 0 failed** (+45 net-new tests); Tauri Rust crate **45 passed, 0 failed** (+13 net-new tests). See `docs/architecture/m7.1_implementation_report.md`.
- **AI Studio Conversational Completion (M7.2, same external "Phase 7" track as M7.1 — see `.kortex/roadmap.md`)**: a real Chat tab in AI Studio (`apps/desktop/src/features/ai-studio/components/ChatPanel.tsx`) that sends every message through the existing `kortex.ai.agent.orchestrate` capability, rehydrates its transcript from a new, minimal `kortex.ai.conversation.history.get` capability (a thin wrapper over the existing `AIMemoryManager` — no new persistence subsystem) on load, and surfaces a governed tool-use approval as a lightweight card deep-linking into the existing Workflow Approval Queue rather than duplicating any decision authority (the desktop never calls `kortex.ai.agent.resume` itself — resolution is observed only via status polling). Getting there required finding and fixing a platform-wide defect in `core/dispatch.py`: capability handlers declaring a Pydantic-model parameter (`LLMRequest`, `AgentTask`, `ResumeToken`, etc.) were never coerced from the dict shape real JSON/IPC delivers, so every one of these capabilities crashed outside a same-process test — fixed generally, not just for AI capabilities. Also closes a related gap where agent-orchestrated conversation turns (unlike plain-generated ones) were never durably recorded, which would have broken conversation recovery after a restart for any chat message that used tools. New `Textarea` design-system primitive. See `docs/architecture/m7.2_implementation_report.md`.
- M7.2 final certification (fresh full-suite runs, this change): backend **2,352 passed, 2 skipped, 1 failed** (the same pre-existing, unrelated `tzdata` failure — zero regressions, +24 net-new tests); desktop **519 passed, 0 failed** (+34 net-new tests); Tauri Rust crate **45 passed, 0 failed** (unchanged — no Rust file was touched). See `docs/architecture/m7.2_implementation_report.md`.

## [0.2.0] - 2026-08-07

### Added

- **Phase 2 — Recipe Engine (`kortex.engines.recipe`)**:
  - Central `RecipeEngine` facade implementing `BaseEngine` and `IEngineDiagnostics`.
  - Pure deterministic `RecipeCompiler` translating declarative Recipe DSL into `WorkflowDefinition` state machines without execution logic.
  - `RecipeParser` for loading `recipe.yaml`, `manifest.yaml`, `schema.yaml`, and `permissions.yaml`.
  - `RecipeValidator` enforcing schema validation, capability checks, and security rules (banning code files like `.py`, `.js`, `.sql`, `.sh`, `.exe`, `.dll`).
  - `RecipeManifestManager` managing `manifest.yaml` structure and SHA256 checksum calculation.
  - `RecipeRegistry` for cataloging, finding, searching, and listing registered recipes by ID, namespace, or version.
  - `RecipeInstaller` managing recipe installation, upgrade, removal, and rollback via `StorageEngine` (`IFileStore`).
  - `RecipePackager` creating and verifying standalone `.kortex-recipe` ZIP archives with SHA256 payload checksums.
  - `RecipeLoader` reading recipe assets from directories, ZIPs, or `.kortex-recipe` files.
  - `VersionResolver` implementing SemVer 2.0.0 comparison, range matching, and dependency resolution.
  - `PermissionValidator` enforcing least privilege and capability authorization checks.
  - `CompatibilityValidator` checking system engine and Kernel version constraints.
  - Registered 10 canonical capabilities: `kortex.recipe.load`, `kortex.recipe.validate`, `kortex.recipe.compile`, `kortex.recipe.install`, `kortex.recipe.remove`, `kortex.recipe.upgrade`, `kortex.recipe.package`, `kortex.recipe.search`, `kortex.recipe.list`, `kortex.recipe.info`.
  - Comprehensive test suite (131 tests passing, 97% overall coverage across `kortex.engines.recipe`).

## [0.1.0] - 2026-08-06

### Added

- Project scaffolding tool (`tools/create_project.py`)
- Python package configuration (`backend/pyproject.toml`)
- Development tooling (`.editorconfig`, `.pre-commit-config.yaml`, Ruff, mypy)
- Full-stack `.gitignore` (Python, Node.js, Tauri, Docker)
- Project documentation skeleton (`.kortex/`, `docs/`)
- Contribution guidelines (`CONTRIBUTING.md`)
- Phase 1 Kernel Foundation & Boot/Configuration/Registry/Event engines
- Phase 2 Storage Engine & Workflow Engine implementations
