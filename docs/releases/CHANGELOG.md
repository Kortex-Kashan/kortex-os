# KORTEX OS — System Changelog

All notable changes to the KORTEX OS repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

Documentation note: this section records engineering work accepted since `[1.0.0]` that has not yet been assigned a release version or tag — that decision (`OWNER DECISION REQUIRED`) is tracked separately in `docs/release/RELEASE_CANDIDATE_READINESS.md` §14.1 and is out of scope for this entry.

### Added
- **Phase 7 — Production Hardening** (all formally accepted; see `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §5.1–§5.11 and `.kortex/roadmap.md`): Sentinel Engine (health monitoring/integrity), Monitoring Engine (metrics/dashboards), Backup Engine (AES-256-GCM encrypted, fail-closed), Recovery Engine (durable journal, staged restore, multi-tier verification/rollback), Update Engine (Ed25519-signed manifests, staged migration, 3-layer rollback authority), Database Migration Wiring (Alembic foundation), Docker production builds, Desktop installers (Windows MSI + NSIS), CI/CD (`backend-ci.yml`/`desktop-ci.yml`), and Production Secret Storage / Native Windows Keyring.
- **Application Completion track M7.1–M7.6** (see `.kortex/roadmap.md`): Local Runtime Completion (sidecar supervision, persistent keys, first-run bootstrap), AI Studio Conversational Completion, AI Studio ↔ Connector Engine Integration, Document Engine ↔ AI Studio Integration, Knowledge Engine ↔ AI Studio Integration, AI Execution Control Plane Hardening.
- **Integration Hub track M1 — MCP Foundation + Capability Projection Bridge** (`da93c64`, `79040e9`): Streamable HTTP MCP client transport, dynamic per-profile capability registration (`kortex.mcp.<profile_id>.<tool_name>`), tenant-scoped capability projection bridge, desktop Connections UI and Visual Workflow Canvas palette integration.
- **Integration Hub track M2 — GitHub OAuth Connector** (`721d30a`; see `docs/architecture/integration_hub_m2_github_oauth_connector_implementation_report.md`): `IntegrationOAuthManager` (SecurityEngine-owned OAuth lifecycle with mandatory refresh-token rotation), GitHub curated action catalog with dynamic per-profile capability registration, backend-authoritative disconnect capability, desktop "Connect with GitHub" flow.

### Fixed
- **DEFECT-002** — Workflow durability restart-recovery optimistic-lock race condition, root-caused to `DatabaseEngineManager` lacking application-level SQLite writer serialization (`6cc223b`).
- **DEFECT-003** — Hardcoded, now-expired Update Engine manifest test-fixture timestamps causing 3 integration test failures (`283cf87`).
- Pre-existing `mypy` failure on `mcp.*` imports (missing type stubs), which was aborting Backend CI's lint/type-check step before tests could run (`bdb9461`).

## [1.0.0] - 2026-08-08

### Added
- **Architecture Version 1.0.0 Ratification**: Formally declared Architecture Version 1.0.0 frozen, ratified, and immutable (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`).
- **Engineering Constitution**: Ratified 30 Articles governing AI assistants and human developers (`engineering_constitution.md`).
- **Universal Shared Domain Models**: Specified 20 universal, engine-agnostic domain models (`shared_domain_models.md`).
- **Platform Service Contracts**: Specified capability invocation, request/response wrappers, timeouts, retries, idempotency, and correlation tracing (`platform_service_contracts.md`).
- **Universal Asset System**: Specified asset packaging (`.kortex-*`), SemVer, and 6-stage cryptographic verification pipeline (`asset_system.md`).
- **Phase 2 Engine Specifications**: Finalized production specs (v3.0.0) for Recipe Engine, Document Engine, Connector Engine, Knowledge Engine, Security Engine, and AI Orchestration Engine.
- **Business Layer Architecture**: Specified Business Module Architecture, 36 Canonical Business Entities, SDK Development Guide, and Marketplace Architecture.
- **Platform Architecture Specs**: Finalized Platform Runtime, Capability Registry, Event Bus, Storage Strategy, Kernel Boot Sequence, System Diagnostics, Platform Configuration, and Multi-Tenant Architecture specifications.
- **Governance Infrastructure**: Created ADR workflow, code review guides, testing standards, security threat model, quality gates, and release management policies.
