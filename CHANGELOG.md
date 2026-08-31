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
- Full backend regression suite: 2,308 passed, 2 skipped (documented Ollama-unavailable skips), 2 failed — both independently reproduced and root-caused as pre-existing and unrelated (a missing `tzdata` package in this environment; a timing-sensitive flake in an untouched M5-era test file). Desktop suite: 440 passed, 0 failed.

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
