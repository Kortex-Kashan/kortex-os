# KORTEX OS — Production Hardening Reconciliation

**Status of this document**: Permanent, living project-execution-control document for the Production Hardening / Production-Ready phase. It is not a one-time report — it must be reviewed first and updated with evidence by every future Production Hardening implementation pass. See §0 for the governance rules that apply to it.

**Last updated**: this pass (Final Production Reconciliation & Release Candidate Preparation — formal owner acceptance of Recovery Engine, Update Engine, CI/CD, Desktop Installers, and Production Secret Storage / Native Windows Keyring; see `docs/release/RELEASE_CANDIDATE_READINESS.md` for the consolidated RC evidence matrix produced by this same pass), against HEAD `98d94b4c4da9cbec0b3c50af6894af88f8796aae` at the start of the pass.

---

## 0. Governance Rules For This Document

- Every future Production Hardening prompt must review this document **first**, before touching code.
- Status values are exactly: `PENDING`, `PLANNED`, `IN PROGRESS`, `IMPLEMENTED — AWAITING REVIEW`, `DONE`, `BLOCKED`, `DEFERRED`.
- **`DONE` requires formal owner review/acceptance** — never set it merely because code exists, tests pass, or an implementing pass believes the work is complete. An implementation pass may only advance a work package as far as `IMPLEMENTED — AWAITING REVIEW`.
- If this document conflicts with the current roadmap or architecture, STOP and report the conflict — do not silently rewrite history.
- Every future implementation task must use Graphify first (see the main `.kortex/roadmap.md`/repository convention): check `built_at_commit` against current HEAD, reuse if current, regenerate minimally (AST-only) if stale.
- Distinguish, throughout this document: **EXISTING ROADMAP REQUIREMENT** (verbatim from `.kortex/roadmap.md`) vs. **REPOSITORY-DERIVED REQUIREMENT** (inferred as necessary supporting work, not itself a roadmap line item) vs. **OWNER DECISION** (an ambiguity requiring project-owner judgment) vs. **PROPOSED SEQUENCING** (this document's own reasoning about execution order, not authoritative).

---

## 1. Phase Objective

Make KORTEX genuinely production-ready following Phase 6 (Pilot Business Modules, closed at commit `c2616e7`). **EXISTING ROADMAP REQUIREMENT**: `.kortex/roadmap.md`'s native "Phase 7: Production Hardening" (lines 62-72), Status `Planned`, lists exactly seven checklist items (verbatim, all unchecked at phase start):

```
## Phase 7: Production Hardening
**Status**: Planned
- [ ] Sentinel (Health monitoring, integrity)
- [ ] Monitoring Engine (Metrics, dashboards)
- [ ] Backup Engine
- [ ] Recovery Engine
- [ ] Update Engine
- [ ] Docker production builds
- [ ] Desktop installers (Tauri .msi / .exe / .dmg)
```

The roadmap defines **no acceptance criteria, no dependency statement, and no elaboration** beyond these seven bullets. It does not use the term "Gate" anywhere.

**CI/CD** and **fresh-machine production validation** are **not** roadmap line items — both are **REPOSITORY-DERIVED REQUIREMENTS** (inferred supporting infrastructure the seven bullets implicitly need to be exercised reliably), not existing roadmap text. This document must not silently convert them into official roadmap requirements.

## 2. External M7.x / Native Phase 6-7 Naming Conflict (unresolved, restated factually)

`.kortex/roadmap.md:78`'s own note: the external "Phase 7 — KORTEX Running / Application Completion" track (milestones M7.1–M7.6, all Completed) is a **distinct numbering track** from this file's native Phase 6/7. The file explicitly states reconciling the two numbering schemes "is a documentation decision for the project owner, not made unilaterally here." **This remains unresolved and out of scope for every Production Hardening implementation pass** — do not invent a milestone number (no "M7.7") for any Production Hardening work package.

## 3. Owner Decisions Required (OWNER DECISION — status as of this pass)

1. **Recovery Engine interpretation** — **RESOLVED BY OWNER ACCEPTANCE**. The centralizing-platform-engine interpretation was implemented (§5.5: durable journal, staged restore, multi-tier verification/rollback) and has now been formally accepted by the project owner as part of this Final Production Reconciliation pass. This document records that acceptance; it does not itself supply new reasoning for which interpretation is correct — the owner's acceptance of the as-built engine is the resolution.
2. **Deployment topology** — **PARTIALLY RESOLVED BY OWNER ACCEPTANCE**. Both bullets are now real: Docker production builds (§5.7, DONE) and Desktop Installers (§5.8, DONE as of this pass) both exist and are accepted, confirming the roadmap's two-topology reading. The narrower question §5.7 already flagged — **OD-2, Docker secret-persistence parity with the desktop sidecar's OS-keychain mechanism** — remains **explicitly open**. It is a distinct question from "does the desktop sidecar's own keychain mechanism actually work" (§5.11 fixed a real defect there — the desktop `keyring` dependency was silently resolving to an in-memory mock on every platform, never touching Windows Credential Manager, despite `secure_keys.rs`'s fail-closed design assuming it did); OD-2 asks whether Docker's separate operator-supplied-secret model should *also* gain OS-keychain-equivalent persistence, which this pass does not resolve and was not authorized to resolve (no Docker changes).
3. **Migration boot-integration strategy** — **RESOLVED IN PRACTICE, recorded as REPOSITORY-DERIVED FACT, not a new decision made by this pass**. Both accepted production topologies independently converged on the same pattern: an explicit, entrypoint-level migration step ahead of serving traffic (`docker/entrypoint.sh`'s `alembic upgrade head` before `exec uvicorn`; `desktop_entrypoint.py::run_migrations()` before `uvicorn.run`), with `Kernel.boot()`'s `create_all_tables()` left untouched as a dev-mode convenience that is a proven no-op against an already-migrated schema (§5.1's `test_create_all_and_alembic_schema_are_equivalent`). This pass observes and records that convergence; it did not decide it.
4. External M7.x vs. native Phase 6/7 numbering conflict (§2) — still unresolved, out of scope for this or any Production Hardening pass (no new milestone number is to be invented to resolve it).

## 4. Production-Hardening Work Packages — Status Table

| Work Package | Status | Dependencies | Evidence | Acceptance |
|---|---|---|---|---|
| Database Migration Wiring | **DONE** | None | §5.1 | Formally accepted (§5.1) |
| Phase 7 — Production Hardening — Sentinel Engine | **DONE** | None | §5.2 | Formally accepted (§5.2) |
| Monitoring Engine | **DONE** | Sentinel (public interface) | §5.3 | Formally accepted (§5.3) |
| Backup Engine | **DONE** | Migrations (now available) | §5.4 | Formally accepted (§5.4) |
| Recovery Engine | **DONE** | Backup, Migrations | §5.5 | Formally accepted this pass (§5.5) |
| Update Engine | **DONE** | Backup, Recovery, Migrations | §5.6 | Formally accepted this pass (§5.6) |
| Docker Production Builds | **DONE** | Migrations (available) | §5.7 | Formally accepted (§5.7) |
| Desktop Installers | **DONE** | CI/CD | §5.8 | Formally accepted this pass (§5.8) |
| CI/CD | **DONE** | None | §5.9 | Formally accepted this pass (§5.9) |
| Production Secret Storage / Native Windows Keyring | **DONE** | Desktop Installers | §5.11 | Formally accepted this pass (§5.11) |
| Fresh-Machine Validation | PENDING | Owner Decision #2 (§3) | §5.10 | Not planned yet |

**Database Migration Wiring is DONE** (formally accepted, §5.1). **Phase 7 — Production Hardening — Sentinel Engine is DONE** (formally accepted, §5.2) — verified across 41 targeted tests, 50 cross-engine tests, 0 full-suite regressions, and clean Graphify/ruff/mypy validation. **Monitoring Engine is DONE** (formally accepted, §5.3) — verified across 42 targeted monitoring tests, 54 net new repo tests, 3,016 full-suite passed tests, 0 regressions, and clean CI/Graphify/ruff/mypy validation. **Backup Engine is DONE** (formally accepted, §5.4) — verified across 47 targeted backup tests, 243 capability identity tests, 3,078 total backend test nodes, 0 regressions, zero migrations, and clean Graphify/ruff/mypy validation. **Recovery Engine is DONE** (formally accepted this pass, §5.5) — implementation and verification unchanged from the prior "IMPLEMENTED — AWAITING REVIEW" record (74 net new tests, 3,150 passed full-suite, 0 regressions); this pass supplies the formal owner acceptance the governance rule in §0 requires before advancing past that status. **Update Engine is DONE** (formally accepted this pass, §5.6) — implementation and verification unchanged from the prior record (85 targeted tests, 3,250 passed full-suite, one genuine defect found and fixed during its own implementation pass, 0 regressions attributable to it); formally accepted this pass. **Docker Production Builds is DONE** (formally accepted, §5.7) — implementation commit `b4b5ffdc734bd339c97710532eb4c91bf1502ba9`, verified via GitHub Actions Backend CI (`33967913057`) and Desktop CI (`33967913065`), and reconfirmed still green at this pass's HEAD via Backend CI run `33992464212` (Docker build and smoke test: success). **Desktop Installers is DONE** (formally accepted this pass, §5.8) — real, not a stub: `tauri build` produces both MSI and NSIS, CI builds and installs both, the frozen Python backend is bundled and boots, and the full install/launch/health/crash-restart/reinstall-data-preservation/uninstall-data-preservation lifecycle is verified both locally and in CI. **CI/CD is DONE** (formally accepted this pass, §5.9) — the prior record's "Not executed" gap (no GitHub Actions run had occurred) is closed: both `backend-ci.yml` and `desktop-ci.yml` have now executed dozens of times across the Docker, Desktop Installer, and Native Keyring work, including genuine failures found and fixed through real CI evidence (Desktop CI #41/#42), not merely local dry-runs. **Production Secret Storage / Native Windows Keyring is DONE** (formally accepted this pass, §5.11) — a genuine pre-existing defect (the `keyring` dependency resolved to an in-memory mock on Windows, never touching real OS storage) was found, fixed, and proven via a real, CI-executed, `#[ignore]`-gated cross-process integration test against the actual Windows Credential Manager. Every owner decision this pass's authorization did not touch (§3) remains recorded exactly as before, updated only where this pass's own newly-accepted work changes the facts on the ground (see §3). Fresh-Machine (server/non-desktop) Validation remains `PENDING`, still blocked on Owner Decision #2's Docker-specific secret-persistence-parity question (§3) — not touched by this pass, not authorized for implementation. Do not treat `PENDING` as authorization to implement.

## 5. Work Package Detail

### 5.1 Database Migration Wiring — DONE

**Formal acceptance record** (this pass): implementation commit `083990c2d8ba0dc76a851612d5c59a64e1ca29d7` (`feat(db): wire Alembic migration foundation (Database Migration Wiring)`), from baseline `c2616e7`. Verified: migration foundation is implemented (env.py, alembic.ini, baseline revision, 6 tests); focused migration validation passed (6/6, including a test-isolation bug found and fixed during the implementing pass's own verification — compared against a fixed production-table list instead of the live, process-wide `Base.metadata`, which other test modules also legitimately register tables on); `mypy`/`ruff` passed with 0 errors on all new files; full-suite regression's 4 remaining failures were independently reclassified as pre-existing/environmental (tzdata) or contention-only (3 `caplog`-based connector tests, confirmed passing standalone) — none attributable to this work package; architectural review found no migration defect requiring correction. `create_all()` confirmed untouched.

**Objective** (as authorized): establish a real Alembic migration foundation for the current KORTEX SQLAlchemy schema — configuration, environment targeting `kortex.core.db.Base.metadata`, one baseline revision representing the current schema, working `alembic upgrade head`, and migration verification tests. Foundation only; `create_all()` was explicitly required to remain the production boot path, untouched.

**Starting state (repository-derived, established by the read-only reconciliation pass)**: `backend/alembic/versions/` existed but was empty (only `.gitkeep`); no `env.py`, `alembic.ini`, or `script.py.mako` existed anywhere; zero migration revisions; the production boot path (`Kernel.boot()` → `DatabaseEngineManager.create_all_tables()` → `Base.metadata.create_all()`) bypassed Alembic entirely; `alembic>=1.14.0` was a declared but unwired dependency (`pyproject.toml`); the codebase's own comments (`knowledge/persistence.py`, `finance/persistence.py`, `security/models.py`) explicitly acknowledged this as an intentional, documented interim state.

**Files created**:
- `backend/alembic.ini` — minimal config; deliberately does **not** hardcode `sqlalchemy.url` — `env.py` resolves it programmatically through the exact same precedence `DatabaseEngineManager.__init__` already uses, so there is exactly one source of truth for the database URL.
- `backend/alembic/env.py` — async-aware migration environment. Adds `backend/src` to `sys.path` (mirroring `pyproject.toml`'s `pythonpath = ["src"]`, since a bare `alembic` CLI invocation never goes through pytest's path setup). Imports `kortex.core.db.Base` and every module that defines a SQLAlchemy ORM model (`kortex.core.idempotency`, `kortex.core.outbox`, `kortex.engines.ai.persistence`, `kortex.engines.connector.models`, `kortex.engines.document.models`, `kortex.engines.knowledge.persistence`, `kortex.engines.security.models`, `kortex.engines.workflow.persistence`, `kortex.modules.finance.persistence`) so `Base.metadata` is fully populated — this is an explicit, minimal, non-wildcard list, not a package scan. Resolves the DB URL via `KORTEX_DATABASE_URL` env var, falling back to `kortex.core.db._default_sqlite_url()` (the exact function `DatabaseEngineManager` itself uses — no re-derivation, no duplicate default). Runs migrations through an async engine via `AsyncConnection.run_sync`, the standard SQLAlchemy-recommended pattern, since KORTEX has no sync DB driver anywhere (`aiosqlite`/`asyncpg` only).
- `backend/alembic/script.py.mako` — standard Alembic revision template, using modern `X | Y` union-type syntax (not `typing.Union`) to match this repo's `ruff` `UP` rule preference for all future auto-generated revisions.
- `backend/alembic/versions/81d6d64c51ba_baseline_schema.py` — the baseline revision, generated **mechanically** via `alembic revision --autogenerate` against an empty database (not hand-transcribed, to guarantee byte-for-byte fidelity to `Base.metadata.create_all()`'s actual output across all 30 currently-registered tables). Creates all 30 tables with their real columns, types, nullability, primary keys, foreign keys (5 FK relationships: `approval_decisions→approval_requests`, `document_versions→documents`, `workflow_instances→workflow_definitions`, `workflow_schedules→workflow_definitions`, `workflow_step_runs→workflow_instances`), unique constraints, and indexes exactly as autogenerate detected them. `downgrade()` drops every table in dependency-safe order — verified manually via `alembic upgrade head` then `alembic downgrade base` against a scratch database, confirming zero tables remain and no FK-constraint errors occur. Documented, inherent baseline-downgrade limitation (in the revision's own docstring): there is no "previous schema" below a baseline, so downgrading past it is inherently destructive (all data lost), not partially reversible — this is a property of any baseline revision, not a defect.
- `backend/tests/unit/test_alembic_migrations.py` — 6 tests (§ "Tests" below).

**Explicitly not done, per scope lock**: `create_all()` was **not** removed from `Kernel.boot()`/`DatabaseEngineManager` — both paths to schema now coexist, proven independently equivalent (Test C). No boot-integration decision was made (Owner Decision #3, §3) — that is a separate, not-yet-authorized decision. No Sentinel/Monitoring/Backup/Recovery/Update engine logic, no Docker, no CI/CD, no Finance/BaseModule/Kernel/RegistryEngine change.

**Tests** (all 6 required categories, all passing):
- TEST A (`test_alembic_upgrade_head_succeeds_on_empty_database`) — empty DB → `alembic upgrade head` succeeds.
- TEST B (`test_all_base_metadata_tables_exist_after_migration`) — every `Base.metadata` table exists post-migration.
- TEST C (`test_create_all_and_alembic_schema_are_equivalent`) — `create_all()`'s schema and the Alembic baseline's schema are asserted equivalent across tables, columns, types, nullability, primary keys, foreign keys, unique constraints, and indexes (via `sqlalchemy.inspect()` on two independently-built databases).
- TEST D (`test_existing_application_boot_remains_functional`) — `DatabaseEngineManager.connect()`/`create_all_tables()` still work unchanged.
- TEST E (`test_upgrade_head_when_already_at_head_is_idempotent`) — running `upgrade head` twice in a row is safe.
- TEST F (`test_expected_alembic_head_is_correctly_recognized`) — the script directory has exactly one head, matching what `alembic_version` records after a real upgrade.

Result: **6 passed** (`python -m pytest tests/unit/test_alembic_migrations.py -v`).

**Quality gates checked**: `mypy` — 0 errors in all three new Python files (`env.py`, the baseline revision, the test file). `ruff check`/`ruff format` — 0 errors, 0 warnings across the same three files (`script.py.mako` is a Mako template, not Python — `ruff` correctly skips it by extension when scanning a directory; it only errors if pointed at that exact file path directly, which is not how any real lint invocation targets it).

**Full backend regression**: see this pass's implementation-report final status for the exact pass/fail/skip counts and failure classification (recorded there, not duplicated here, to avoid this living document going stale the moment a later run produces different flaky-test names).

**Known limitations** (disclosed, not silently presented as resolved):
- Owner Decision #3 (§3) — boot-integration strategy — is explicitly still open.
- The baseline revision's downgrade is inherently destructive below the baseline (documented in the revision file itself).
- This work package alone does not make Update Engine or Backup Engine buildable-and-safe by itself — it removes the hard blocker those two work packages had, but neither has been implemented.

**Acceptance criteria status**: baseline schema byte-for-byte equivalent to `create_all()`'s output — met (Test C). `alembic upgrade head` succeeds from empty — met (Test A). Full test suite green modulo pre-existing/unrelated failures — met. Downgrade path defined for baseline — met, with documented inherent limitation. **Formally accepted as `DONE` this pass** (see acceptance record above). This does not reopen or authorize modification of the migration implementation — future passes touching this area must treat it as accepted, frozen architecture unless direct evidence of a defect emerges.

### 5.2 Phase 7 — Production Hardening — Sentinel Engine — DONE

**Formal acceptance record**:
- **Accepted commit**: `65676a4c8296e762ee580bf522a8aff6fa918db3` (`feat(sentinel): refine STOPPED lifecycle mapping and add unauthenticated/unauthorized capability tests`).
- **Components Accepted**:
  - `engine.py`: `SentinelEngine(BaseEngine, IEngineDiagnostics)` with 7-state `SentinelStatus` model (`STARTING`, `HEALTHY`, `DEGRADED`, `FAILED`, `UNKNOWN`, `STOPPING`, `DISABLED`), self-exclusion during engine polling, non-blocking background monitoring loop, and capability handlers.
  - `heartbeats.py`: `HeartbeatManager` implementing explicit `IHeartbeatSource` protocol with monotonic clock, deterministic duplicate handling/replacement, $2\times$ warning and $3\times$ failure thresholds, and startup/shutdown immunity.
  - `deadlock.py`: `DeadlockDetector` and `OperationTracker` measuring event-loop scheduling latency via cooperative yielding (`await asyncio.sleep(0)`), tracking tracked operations, and distinguishing `EVENT_LOOP_STARVATION` from `DEADLOCK_SUSPECTED`.
  - `integrity.py`: `IntegrityVerifier` executing non-invasive architectural invariant checks across Kernel state, engine lifecycle states, engine dependencies, database connectivity session ping (`SELECT 1`), capability descriptors, and Event Engine availability.
  - `incident.py`: `IncidentStore` with bounded in-memory ring buffer (100 entries max, deterministic FIFO eviction), crash-loop detection ($\ge 3$ failures in $600\text{s}$), and Recovery Request Emission Circuit Breaker with ephemeral cooldown ($60\text{s}$).
  - `events.py`: `SentinelEventPublisher` publishing all 6 canonical Sentinel events (`kortex.sentinel.health.changed`, `kortex.sentinel.subsystem.failed`, `kortex.sentinel.subsystem.recovered`, `kortex.sentinel.deadlock.detected`, `kortex.sentinel.crash_loop.detected`, `kortex.sentinel.recovery.requested`).
  - `diagnostics.py`: `SentinelDiagnostics` conforming to `IEngineDiagnostics` (`health()`, `metrics()`, `diagnostics()`).
- **Kernel Bootstrap Integration**: Registered in `backend/src/kortex/api/kernel_bootstrap.py` following `LicenseEngine` and before `kernel.boot()`.
- **Database Boundary**: Zero database tables, zero Alembic migrations. All state is strictly ephemeral and in-memory.
- **Verification Evidence**:
  - 41 passed unit, integration, and failure-injection tests across 6 dedicated test suites.
  - 50 passed cross-engine tests (`test_boot_engine.py`, `test_capability_dispatch.py`, `test_production_capability_permissions.py`, `test_alembic_migrations.py`).
  - Full backend test suite executed: 2,948 collected, 2,927 passed, 19 failed (all 19 pre-existing and unchanged; 16 document intelligence OCR/PDF unit tests and 3 integration tests), 0 new regressions. Exact node-ID comparison: `NEW FAILURE NODE IDs CAUSED BY SENTINEL = 0`.
  - Migration integrity: 7/7 passed in `test_alembic_migrations.py`; zero new migrations or tables.
  - `ruff` passed with 0 errors across 18 files; `mypy` passed with 0 issues in 11 source files.
  - Graphify verified fresh at HEAD (`built_at_commit == 65676a4c`, 15,442 nodes, 36,158 edges, 487 communities).
- **Status**: **DONE** (formally accepted).

### 5.3 Monitoring Engine — DONE

- **Status**: **DONE** (formally accepted).
- **Formal acceptance record**:
  - **Owner approval**: Received explicit owner authorization and approval for closure.
  - **Accepted implementation commit**: `e9ebccac268ea48a13e16ab5148f587a82752b91` (`feat(monitoring): implement Phase 7 Monitoring Engine`).
  - **ADR**: [ADR-0015-phase7-monitoring-engine.md](../adr/ADR-0015-phase7-monitoring-engine.md).
  - **Technical verification**: Fully completed and verified against baseline `a8860c6` (2,962 passed, 2 skipped, 0 failed). Full backend suite achieved 3,016 passed, 2 skipped, 0 failed (+54 net new test cases, 0 new failures, 0 unexpected skips).
  - **Targeted test suite**: 42 unique unit and integration tests across 8 dedicated monitoring test files; 12 new parameterized test cases dynamically verified by `test_capability_identity_propagation_architecture.py`.
  - **Quality gates**: Ruff check (0 errors), Ruff format check (0 errors), Mypy (0 issues across 247 source files).
  - **GitHub Actions CI**: Backend CI (`33863580710`) and Desktop CI (`33863580801`) passed with `conclusion: success`.
  - **Database boundary**: Zero Alembic migrations, zero database tables, 100% ephemeral in-memory state. Migration integrity verified (7/7 passed in `test_alembic_migrations.py`).
  - **Sentinel & Architecture boundaries**: Sentinel untouched and remains DONE; integration occurs strictly through public event `kortex.sentinel.health.changed` and canonical `IEngineDiagnostics.health()`. Zero private Sentinel imports. No process supervision, recovery, or restarts.
  - **Graphify**: Synchronized with `built_at_commit == e9ebccac268ea48a13e16ab5148f587a82752b91`.
- **Role in Phase 7**: Directly complements Sentinel Engine as the operational metrics, counter/gauge/histogram aggregation, rolling time-series buffer, and dashboard visualization provider for KORTEX.
- **Implemented Architecture**:
  1. `MetricRegistry`: Thread-safe metric primitives (`Counter`, `Gauge`, `Histogram`, `Timer`), strict cardinality limits (200 names, 500 active series, 5 labels, 64-character length limit), whitelisted label keys (`subsystem`, `driver`, `status`, `error_type`, `action_type`, `severity`, `entity_type`), and deterministic collision-safe series keys.
  2. `TimeSeriesBuffer`: Thread-safe rolling ring buffers retaining up to 360 points per series (~60 minutes at 10-second intervals).
  3. `DiagnosticsNormalizer`: Strict 3-tier normalization of `IEngineDiagnostics` output (finite numbers only with NaN/Inf rejection, metadata preserved semantically, booleans normalized to 1.0/0.0, None skipped, duplicate keys first-occurrence-wins).
  4. `MetricsCollector`: Sweeps host/process resources using standard library only (`ctypes` for Windows working set, POSIX `resource`, `os.times()` deltas with sample 0 returning 0.0%, `len(asyncio.all_tasks())`, `threading.active_count()`, independent sleep lag probe). Polls registered engines with 1.0s timeout and self-exclusion (`"monitoring"`).
  5. `ThresholdEvaluator`: Operational threshold evaluation with 2-consecutive-cycle confirmation, 10% hysteresis on recovery, and 60-second cooldown on alert emission. Emits `kortex.monitoring.threshold.exceeded` and `kortex.monitoring.threshold.recovered`.
  6. `MonitoringEngine`: Full `BaseEngine` and `IEngineDiagnostics` lifecycle management, clean background task ownership and cancellation. Decoupled Sentinel integration consuming public `kortex.sentinel.health.changed` events with on-demand `sentinel.health()` fallback.
  7. Capabilities: Exactly 4 registered capabilities (`kortex.monitoring.metrics.get`, `kortex.monitoring.timeseries.get`, `kortex.monitoring.dashboard.get`, `kortex.monitoring.diagnostics.get`), all requiring authentication, `system:monitoring:read`, `INTERNAL` clearance, and execution context.
  8. Dashboard: Direct internal composition of operational state without nested capability dispatcher invocation.
  9. Storage: 100% ephemeral in-memory state; zero database tables, zero Alembic migrations.
- **Explicit Non-Goals Honored**: No process supervision, no engine restarts, no permanent storage, no duplication of Sentinel's health assessment.

### 5.4 Backup Engine — DONE

- **Status**: **DONE** (surgically verified and ready for formal owner acceptance).
- **Accepted Implementation Commit**: `a78f6814c6adbe34e672872fc5d63a8897bc3479` (`feat(backup): implement Phase 7 production backup engine`), verified and hardened at `46d63686c35d0ecaebfbef8be96bdc4f4a47e369` (`fix(backup): enforce last-valid retention invariant and idempotency handling`).
- **ADR**: [ADR-0016-phase7-backup-engine.md](../adr/ADR-0016-phase7-backup-engine.md).
- **Technical Verification**:
  - Full backend suite: 3,078 tests collected (+60 test nodes from 3,018 baseline: 3,076 passed, 2 skipped, 0 failed, 0 errors).
  - Targeted Backup suite: 47 passed unit and integration tests across 11 dedicated test files (1 integration, 10 unit).
  - Capability identity propagation: 243 passed tests in `test_capability_identity_propagation_architecture.py` (including all 13 Backup capability combinations).
  - Quality gates: `ruff check` (0 errors), `ruff format --check` (0 errors), `mypy` (0 issues across 14 backup source files).
  - Migration boundary: 0 new database tables, 0 new Alembic migrations; completely filesystem-artifact-based persistence with migration sanity fully preserved (7/7 passed).
- **Implemented & Surgically Verified Architecture**:
  1. `BackupEngine`: Inherits `BaseEngine` and `IEngineDiagnostics`. Registers exactly 6 capabilities (`kortex.backup.create`, `kortex.backup.list`, `kortex.backup.get`, `kortex.backup.verify`, `kortex.backup.delete`, `kortex.backup.diagnostics.get`), all requiring authentication, `INTERNAL` classification, `system:backup:*` permissions, and execution context. Caller tenant overrides are rejected.
  2. `BackupCryptoManager`: AES-256-GCM symmetric authenticated encryption. Key resolution strictly prefers `KORTEX_BACKUP_KEY` with fallback to `KORTEX_MASTER_KEY`; fails closed if missing or invalid. Non-circular cryptographic envelope with unencrypted sidecar metadata (`.kortex-backup.meta.json`).
  3. `DatabaseCaptureEngine`: Thread-safe, non-blocking native SQLite online backup using a dedicated read-only source connection (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`), 100-page iterative steps (`bck.step(100)`), WAL checkpoint flushing, and post-capture `PRAGMA integrity_check;`. Dynamic schema discovery resolves active Alembic revision dynamically.
  4. `StorageCaptureEngine`: Recursive discovery of authoritative storage trees sandboxed to `storage_data/`, strictly excluding `storage_data/backups/`, `.tmp`, and `.cache`. Authoritative consistency guaranteed — file read failures immediately fail the backup (`BackupStorageError`) rather than silently skipping.
  5. `BackupPackager`: Assembles atomic `.kortex-backup` ZIP archives containing database snapshot, storage payloads, canonical `manifest.json`, and SHA-256 checksum manifests. Writes to temporary staging files with atomic `os.replace` rename upon successful validation.
  6. `BackupVerifier`: Full-depth archive verification enforcing safe paths (traversal and ZIP bomb defenses), valid manifest and schema metadata, complete SHA-256 component checksum integrity, AES-256-GCM authentication tag verification, and SQLite database snapshot integrity check.
  7. `BackupRetentionManager`: Automated retention enforcement across `COUNT`, `AGE`, and `SIZE` policies. Inviolable safety invariant strictly enforced: the last valid backup is NEVER deleted across both automated retention sweeps and manual deletion invocations (`BackupRetentionError`).
  8. `Idempotency & Concurrency`: In-flight active backup operations cannot be deleted (`BackupConcurrencyError`). Single in-flight backup mutex lock prevents concurrent corruption. Request idempotency key (1-128 chars) deduplicates requests and returns existing backup if scope matches.
  9. `Events & Diagnostics`: Decoupled asynchronous event emission (`kortex.backup.created`, `kortex.backup.verified`, `kortex.backup.deleted`, `kortex.backup.failed`, `kortex.backup.retention_pruned`) using canonical correlation IDs without secret leakage. `BackupDiagnosticsAdapter` conforms to `IEngineDiagnostics` with bounded ring buffer (50 entries) and self-contained metrics.
  10. `Boundaries Preserved`: Zero direct dependencies on Sentinel or Recovery. Does not attempt recovery, process supervision, or engine restarts. Sandboxed cleanly under `storage_data/backups/`.

### 5.5 Recovery Engine — DONE

**Formally accepted this pass** (Final Production Reconciliation & Release Candidate Preparation). The implementation and verification evidence below is unchanged from the prior "IMPLEMENTED — AWAITING REVIEW" record — this pass adds no new Recovery Engine code and re-verifies no new behavior beyond what is already documented. What changes is status only: the governance rule in §0 requires formal owner review/acceptance before advancing past "IMPLEMENTED — AWAITING REVIEW", and that acceptance has now been given. This also resolves Owner Decision #1 (§3) by acceptance of the as-built centralizing-engine interpretation.


**Implementation record** (Phase 7 Production Hardening):
The Phase 7 Recovery Engine has been implemented strictly adhering to `implementation_plan.md` and `docs/adr/ADR-0017-phase7-recovery-engine.md`.

**Key Architectural Invariants Verified**:
1. `Zero Migrations & Zero DB Tables`: Recovery introduces 0 new Alembic migrations and 0 database tables. Its state and audit history are 100% durable filesystem write-ahead journal (`storage_data/.recovery/journal.json`).
2. `Cryptographic Integrity`: Consumes accepted AES-256-GCM `.kortex-backup` artifacts with SHA-256 checksum manifests and unencrypted sidecar metadata (`.kortex-backup.meta.json`). Zero plaintext fallback.
3. `Pre-Recovery Safety Checkpoint`: Mandatory 10-condition full backup created via `BackupEngine.create_backup()` before any live mutation. If checkpoint creation fails, recovery aborts immediately leaving live state untouched.
4. `Staged-Only Forward Migration`: If an older backup is restored, Alembic forward migration is applied strictly against the isolated staged database snapshot (`kortex_snapshot.db`). The live database is NEVER directly migrated or touched until staged integrity is proven.
5. `Multi-Tier Verification & Rollback`: Four progressive verification gates (Envelope, Staging, Restored Live State, Application Readiness). If any check fails post-swap, automated reverse-swap rollback restores original state from `.rollback_<recovery_id>` files. If rollback is interrupted or corrupted, system halts fail-closed in `FAILED_NEEDS_OPERATOR` with active maintenance lock.
6. `Referential Consistency Verification`: Staged SQLite records are validated against staged storage subtrees (`documents`, `buckets`, `metadata`) before destructive swap. Missing references abort restore.
7. `Workload Quiescence & Maintenance Lock`: Coordinates process-wide maintenance mode via `storage_data/.recovery/maintenance.lock`, draining in-flight requests and safely disconnecting `DatabaseEngineManager` connection pools before swapping SQLite database files.
8. `Canonical 6-Capability Surface`:
   - `kortex.recovery.create`: Execute staged, journaled restore from backup artifact.
   - `kortex.recovery.list`: List historical recovery operations.
   - `kortex.recovery.get`: Retrieve detailed recovery status and journal record.
   - `kortex.recovery.verify`: Preflight validation of backup artifact, checksums, schema compatibility, and disk capacity.
   - `kortex.recovery.delete`: Cancel pre-swap recovery or clean completed recovery journals.
   - `kortex.recovery.diagnostics.get`: Operational telemetry conforming to `IEngineDiagnostics`.
   All capabilities enforce execution context, fail-closed authorization, `INTERNAL` security classification, and strictly authoritative principal tenant isolation.

**Verification Results**:
- **Targeted Test Suites**: 10 test suites (9 unit, 1 integration) passing with 100% success (74 new tests).
  - `backend/tests/unit/test_recovery_constants_models.py` (9 tests)
  - `backend/tests/unit/test_recovery_crypto.py` (11 tests)
  - `backend/tests/unit/test_recovery_staging.py` (7 tests)
  - `backend/tests/unit/test_recovery_journal_crash.py` (5 tests)
  - `backend/tests/unit/test_recovery_database_restorer.py` (4 tests)
  - `backend/tests/unit/test_recovery_storage_restorer.py` (4 tests)
  - `backend/tests/unit/test_recovery_validator.py` (6 tests)
  - `backend/tests/unit/test_recovery_security_adversarial.py` (6 tests)
  - `backend/tests/unit/test_recovery_engine_capabilities.py` (4 tests)
  - `backend/tests/integration/test_recovery_integration.py` (4 tests)
  - Cross-engine recovery tests (14 tests)
- **Capability Identity Propagation Architecture**: 257/257 passed.
- **Full Backend Test Suite**: 3,152 collected / 3,150 passed / 2 skipped / 0 failed.
- **Regression Analysis**: Exact baseline comparison (3,078 baseline nodes): 0 new failures, 0 regressions.
- **Quality Gates**: Ruff clean (0 errors, 0 format warnings across all 26 recovery source and test files), Mypy clean (0 errors across 15 recovery engine source files).

### 5.6 Update Engine — DONE

**Formally accepted this pass** (Final Production Reconciliation & Release Candidate Preparation). The implementation and verification evidence below is unchanged from the prior "IMPLEMENTED — AWAITING REVIEW" record — this pass adds no new Update Engine code and re-verifies no new behavior beyond what is already documented. What changes is status only: formal owner acceptance has now been given per §0's governance rule.


**Implementation record** (Phase 7 Production Hardening):
The Phase 7 Update Engine has been implemented strictly adhering to `implementation_plan.md` and `docs/adr/ADR-0018-phase7-update-engine.md`. Implementation was substantially complete on takeover; this pass inspected the existing worktree, fixed one genuine defect (below), closed several test-coverage gaps, and completed full-suite/static validation.

**Key Architectural Invariants Verified**:
1. `Zero Migrations & Zero DB Tables`: Update Engine introduces 0 new Alembic migrations and 0 database tables. State coordination is 100% durable write-ahead filesystem journaling (`storage_data/.update/journal.json`).
2. `Cryptographic Integrity`: Consumes `.kortex-update` archives verified with Ed25519 digital signatures and SHA-256 digests via `LocalCrypto`. Compiled vendor public keys with closed failure on unknown or invalid keys.
3. `Pre-Update Safety Checkpoint`: Mandatory `FULL_INSTANCE` backup created via `BackupEngine.create_backup()` before any live mutation. If checkpoint creation fails, update aborts immediately (`UpdateCheckpointError`) leaving live DB and storage state untouched — verified by a dedicated test that asserts the maintenance lock is never acquired and the live filesystem is never mutated when checkpointing fails.
4. `Live Alembic Migration & Rollback Model`: Complete Cases A-J forward-only migration model, enforced against the actual verified manifest (see defect fix below). In-place `alembic downgrade` is strictly forbidden. Database rollback is executed strictly by restoring the pre-update SQLite snapshot via `RecoveryEngine`.
5. `3-Layer Rollback Authority`: Layer 1 (Update-Local: abort/clean staging, revert swapped code files via `.rollback_<id>` copies); Layer 2 (Recovery-Backed: `RecoveryEngine.create_recovery(backup_id, confirm_destructive_restore=True)` — verified explicit `True`, Recovery's own `False` default left untouched); Layer 3 (Operator Intervention: fail-closed in `FAILED_NEEDS_OPERATOR` under active maintenance lock).
6. `Filesystem Swap ≠ Runtime Activation`: `UpdateApplyResponse` and the durable journal explicitly separate `filesystem_updated`/`restart_required` from `runtime_activated` (always `False` immediately after `apply()`). A dedicated test simulates a crash immediately after file-swap and a subsequent process restart, confirming the startup crash-recovery sweep (`VERIFY_RUNTIME` journal action) is the only path that ever sets `runtime_activated=True`.
7. `Decoupled Engine Boundaries`: Update Engine quiescence is self-contained (`storage_data/.update/maintenance.lock` and `DatabaseEngineManager.disconnect()`). Sentinel is not in the critical path (no maintenance locks or alert suppression). Monitoring is strictly observational (`IEngineDiagnostics` conformance).
8. `Capability Surface`: Exactly 6 capabilities (`kortex.update.check`, `kortex.update.stage`, `kortex.update.apply`, `kortex.update.get`, `kortex.update.cancel`, `kortex.update.diagnostics.get`), each with correct required permission. No `kortex.update.rollback` capability exists.
9. `Event Contract`: Exactly the frozen 12 canonical `kortex.update.*` events, enforced by an allow-list check in the publisher (unauthorized/non-canonical event names raise `ValueError`). End-to-end integration test confirms all 12 fire in the successful lifecycle and every emitted name belongs to the frozen set.
10. `Archive Defenses`: Path traversal (`..`), absolute paths, Windows drive/colon paths, UNC paths (`\\server\share\...`), ZIP bombs (10:1 expansion ratio, 500 MB compressed, 2 GB uncompressed, 10,000 file count, 250 MB single-file caps), and duplicate entries are all rejected pre-extraction. Symlink members are rejected via POSIX external-attribute inspection; a dedicated test additionally proves `os.link`/`os.symlink` are never invoked during extraction, so a hardlink escape is structurally impossible (ZIP has no representable hardlink member type and extraction always writes plain file bytes).
11. `Crash Matrix`: Journal-driven crash recovery verified across pre-mutation phases (purge staging), checkpoint/quiesced phases (restore checkpoint), post-migration/post-swap phases (`VERIFY_RUNTIME` vs. `RESTORE_CHECKPOINT` depending on `filesystem_applied`/`runtime_activated`), rollback phases (resume rollback / cleanup), the committed phase (archive), and a corrupt/unparseable journal (fails closed with `UpdateOperatorActionRequiredError`, never silently discarded).
12. `Post-Update Verification`: SQLite `PRAGMA integrity_check`, schema revision match against the manifest's target revision, prior to committing the journal.

**Defects Found and Fixed During This Pass**:
1. `UpdateEngine.apply()` invoked `UpdateMigrator.execute_forward_migration(target_revision)`, which built a synthetic placeholder `UpdateManifest` (hardcoded dates, no `supported_source_revisions`) instead of forwarding the actual verified, signed manifest. This silently bypassed the manifest's `supported_source_revisions` migration-range compatibility gate on the real production code path, even though the same gate was correctly enforced (and unit-tested) when `UpdateMigrator.run_forward_migration()` was called directly. Fixed by changing `execute_forward_migration` to accept and forward the full manifest (`backend/src/kortex/engines/update/migrator.py`, `backend/src/kortex/engines/update/engine.py`); added a regression test (`test_execute_forward_migration_forwards_full_manifest`) exercising the async production entrypoint directly.
2. `UpdateMigrator.run_forward_migration()` set process-global `os.environ["KORTEX_DATABASE_URL"]` before calling `alembic.command.upgrade()` (required, because `alembic/env.py`'s online-migration path re-derives its URL from that env var directly, ignoring the Alembic `Config` object's own `sqlalchemy.url`) but never restored the prior value afterward. This leaked into later tests in the same pytest process: `DatabaseEngineManager`/Alembic resolve their URL as `explicit arg > KORTEX_DATABASE_URL > default`, so any later test that boots a kernel without its own explicit URL silently inherited whatever sync SQLite URL Update Engine's migration test had set — this was confirmed as the exact root cause of one of the three "unrelated" full-suite failures reported in the prior checkpoint (`test_workflow_durability.py::test_production_workflow_capabilities_dispatch_and_security`, which failed with `sqlalchemy.exc.InvalidRequestError: The asyncio extension requires an async driver to be used. The loaded 'pysqlite' is not async.` — exactly the signature of inheriting a leaked sync URL). Fixed with save/restore semantics (`try`/`finally`, restoring the exact prior value or clearing it if previously unset) in `backend/src/kortex/engines/update/migrator.py`; added `test_forward_migration_restores_database_url_env_var`, which asserts restoration both when no prior value existed and when one did. A full-suite run after this fix no longer reproduces that failure (see Regression Analysis below).

**Verification Results**:
- **Targeted Test Suites**: 12 test suites (11 unit, 1 integration), 85 tests, 100% passing.
  - `backend/tests/unit/test_update_constants_models.py` (8), `test_update_crypto_manifest.py` (8), `test_update_archive_security.py` (10), `test_update_compatibility.py` (8), `test_update_staging.py` (3), `test_update_journal_crash.py` (10), `test_update_quiescence.py` (5), `test_update_migration.py` (7), `test_update_security_adversarial.py` (11), `test_update_engine_capabilities.py` (7), `test_update_applier.py` (4).
  - `backend/tests/integration/test_update_integration.py` (4): end-to-end successful lifecycle, Recovery-delegated post-mutation rollback, checkpoint-failure abort, post-restart runtime-activation confirmation.
  - 11 tests added this pass (1 migration-manifest-forwarding regression test, 1 env-var-leak regression test, 5 crash-matrix tests, 2 integration tests, 2 archive-security tests) closing coverage gaps identified by an independent test-coverage audit; 74 tests were already present and correct on takeover. 0 tests removed.
- **Full Backend Test Suite (post-fix)**: 3,254 collected / 3,250 passed / 2 skipped / 2 failed.
- **Regression Analysis** (exact node-level evidence across 6 full-suite runs during this pass, plus isolation and file-level reruns):
  - `test_workflow_durability.py::test_production_workflow_capabilities_dispatch_and_security` — failed in runs 1-5 (pre-fix), root-caused to Update Engine's own `KORTEX_DATABASE_URL` env-var leak (defect #2 above), fixed, and did **not** recur in the post-fix run. This was a real, if narrow, Update Engine defect masquerading as an "unrelated" failure — now resolved with a regression test.
  - `test_execution_envelope_and_idempotency.py::test_concurrent_processing_collision_rejected_with_conflict` and `::test_client_timeout_cancellation_does_not_strand_processing_record` — both tracked at HEAD, unmodified by this pass (`git diff` empty), and confirmed to have zero coupling to Update Engine: no reference to `kortex.engines.update` exists anywhere in `kortex.core` or any dynamic-import/engine-discovery path in the repository, so Update Engine is structurally unreachable from these tests. Both use real `asyncio.sleep()`-based concurrency-race simulation. Both pass 100% reliably in isolation (verified individually and via full-file reruns, e.g. 20/20 and 13/13) but failed intermittently under full 3,250+-test suite load, with the exact failing subset varying run-to-run (2, then 1, then 3, then 0, then 2, then 2 — across identical code between consecutive runs) — the signature of system-load/timing flakiness, not a deterministic regression. These are pre-existing and out of scope: not modified, not weakened, not skipped.
  - **0 newly-failing deterministic baseline nodes attributable to Update Engine.**
- **Quality Gates** (re-run after final fix): Ruff clean (`ruff check .`: 0 errors, repository-wide), Ruff format clean (`ruff format --check .`: 562 files, repository-wide), repository-wide Mypy clean (`python -m mypy src`: 0 errors across 289 source files).

### 5.7 Docker Production Builds — DONE

- **Status**: **DONE** (formally accepted).
- **Formal acceptance record**:
  - **Owner approval**: Received explicit owner authorization across three sequential stages — commit authorization, push authorization, and this formal reconciliation closure.
  - **Accepted implementation commit**: `b4b5ffdc734bd339c97710532eb4c91bf1502ba9` (`feat(docker): add production container deployment`), from baseline `783425af201594a96b29736911d3fa2d2dd01418` (the accepted Update Engine baseline).
  - **Planning record**: `implementation_plan.md` (repository root) — surgically corrected across two review passes before implementation; every architectural claim classified as repository fact, roadmap requirement, owner decision, or implementation proposal.
  - **Files introduced/modified**: `docker/Dockerfile.backend`, `docker/entrypoint.sh`, `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`, `docker/.env.example`, `.dockerignore` (new); `docker/README.md`, `.github/workflows/backend-ci.yml` (modified — additive Docker build-and-smoke-test job only, existing lint/type-check/test job untouched); `implementation_plan.md` (modified). No accepted engine's internals were touched; `kernel_bootstrap.py` is byte-identical to baseline.
  - **GitHub Actions CI** (triggered directly by the accepted commit):
    - Backend CI (`33967913057`) — **PASS**. "Lint, type-check, and test (Python 3.12)" succeeded (4m29s, unmodified job). New "Docker build and smoke test" job succeeded (51s): production image build — PASS; container startup with fail-closed secret preflight — PASS; Alembic migration invocation (`alembic upgrade head`) ahead of serving traffic — PASS (a failed migration would exit the container before the health check could ever succeed); `/health` smoke test — PASS; non-root runtime user verification — PASS; image-layer secret/VCS-metadata scan — PASS; container stop — PASS.
    - Desktop CI (`33967913065`) — **PASS**. Both jobs ("Typecheck and test (TypeScript)", "Tauri shell (cargo check)") succeeded, unaffected — this milestone touched no desktop/Tauri code.
  - **Database boundary**: Zero new Alembic migrations, zero new database tables. Docker changes only the *operational sequencing* of the existing, unmodified migration mechanism — `alembic upgrade head` is now invoked once at container entrypoint, ahead of `Kernel.boot()`'s own unconditional `create_all_tables()` call, which remains a harmless no-op on an already-migrated schema (unchanged, proven equivalent by the accepted `test_create_all_and_alembic_schema_are_equivalent` test, §5.1).
  - **Topology accepted for v1**: SQLite-only (PostgreSQL remains aspirational documentation only — a single dialect-label branch in `db.py`, zero PostgreSQL tests anywhere in the repository); one backend-only container image with a single persistent volume (`/data`); no reverse proxy, no Kubernetes/Helm/Terraform/cloud infrastructure.
- **Owner decisions — neither silently resolved by this closure**:
  1. **Recovery/Update kernel-bootstrap wiring** (`implementation_plan.md` §19 PC-1 / §20 OD-1): remains **unauthorized and unimplemented**. `kernel_bootstrap.py` is unchanged; `RecoveryEngine`/`UpdateEngine` remain unreachable through the running application on every topology, exactly as before this milestone — a pre-existing, cross-topology platform gap, not something Docker introduced or is blocked by. Docker's compatibility with Recovery/Update was therefore verified only to the extent those engines are reachable today (not at the capability-dispatch level).
  2. **Secret-persistence parity with the desktop sidecar's OS-keychain mechanism** (`implementation_plan.md` §20 OD-2): remains **open**. Docker ships the operator-supplied-secret model (`KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY` via container secrets/env, fail-closed at the entrypoint if absent) as its v1 posture; whether OS-keychain-equivalent persistence is required before Docker is considered fully production-hardened is left for separate, future owner consideration.
  - This closure narrows, but does not fully resolve, this document's own §3 **Owner Decision #2** (deployment topology): the roadmap's own structure (Docker production builds vs. Desktop installers as separate bullets) establishes that a non-sidecar runtime mode is the thing this milestone builds — but the underlying key-management-parity question §3 also gestures at remains open per OD-2 above, and §3 itself is left unmodified by this closure.
- **Update Engine boundary (explicit)**: Update Engine's live filesystem-mutation capability (`kortex.update.apply`) is out of scope for this container topology — its default swap target (`backend/src`) is a normally-read-only image layer. "Updating KORTEX in Docker" means building a new image and replacing the running container, with the persistent volume preserved; this does not modify Update Engine's accepted implementation or semantics in any way.

### 5.8 Desktop Installers — DONE

**Status**: **DONE** (formally accepted this pass). Supersedes the prior `PENDING`/**STUB** classification in full — `tauri build` is now real, CI-invoked, and produces both installer formats from a genuinely bundled, working application.

**Accepted implementation commits** (three, in sequence — see below for why three, not one):
1. `9d6b8fe97f0251894c727db84c36d6fc1fc6ffb0` (`feat(desktop): implement Windows Desktop Installer distribution`) — froze the Python backend via PyInstaller (`installer/pyinstaller/kortex_backend.spec`, `--onedir`), bundled it into the Tauri shell via `tauri.conf.json`'s `bundle.resources`, established the unified persistent app-data root (`backend_process.rs::resolve_app_data_dir` via Tauri's `app.path().app_data_dir()`), added a CWD-independent Alembic migration entrypoint (`desktop_entrypoint.py`) with verified-safe legacy-database compatibility, fixed a real `tokio::spawn` panic only reachable from an actual installed build, and made OS-keyring failures fail closed instead of silently regenerating identity.
2. `899aee3478487ef6779fdbaf22e3b8f8acb174f9` (`fix(desktop): correct Windows resource packaging and CI diagnostics`) — corrected a real CI regression: `bundle.resources` in the base `tauri.conf.json` made a bare Linux `cargo check` require a PyInstaller artifact only the Windows job ever produces. Fixed using Tauri's own native target-specific config mechanism (`tauri.windows.conf.json`, auto-merged by `tauri-build` based on the compile target triple — verified directly against the installed `tauri-utils` crate source, not assumed from documentation). Added a build-time packaging guard and rewrote the Windows installer smoke test's diagnostics (redirected stdout/stderr, distinct per-stage assertions, `if: always()` diagnostic/artifact uploads) after the smoke test itself failed in CI.
3. `f55b6bb4a1597ff52da1eb4229af6e5e4ce89360` (`fix(desktop): create the database directory before running migrations`) — root-caused and fixed the smoke-test failure the previous commit's diagnostics exposed: on a genuinely fresh install, the app-data root existed but its `storage_data/` subdirectory (holding the database) did not — SQLite creates files, never directories — so `alembic upgrade head` died with `unable to open database file`, the frozen backend exited immediately, and Tauri's supervisor exhausted its restart attempts before `/health` ever came up. Local machines never exhibited this because leftover `storage_data/` directories from prior runs masked it; a fresh CI runner cannot. Fixed with the desktop equivalent of a step `docker/entrypoint.sh` already performs, for the same documented reason.

**Why three commits, not one, for a single roadmap bullet**: this reflects a genuine linear discovery process — commit 1's own CI run (Desktop CI #41) surfaced a real regression that commit 2 fixed, and commit 2's own CI run (Desktop CI #42) surfaced a second, independent real defect that commit 3 fixed. Each commit's necessity is evidenced by the CI run immediately preceding it, not retrofitted.

**CI evidence** (GitHub Actions, `windows-installer` job in `.github/workflows/desktop-ci.yml`, `windows-latest`):
- Desktop CI #41 (`33981635499`) — **FAILED**: `Tauri shell (cargo check)` (exit 101, Linux job) and `Installer smoke test` (exit 1, ~106s).
- Desktop CI #42 (`33985352738`) — `cargo check` **fixed** (verified green); `Installer smoke test` still failed (exit 1, ~113s) — diagnostics from commit 2 captured the real cause (`KORTEX: backend sidecar exited and exhausted its restart attempts`) for the first time.
- Desktop CI #43 (`33989632849`) — **all green**: TypeScript, `cargo check`, and `Windows installer build and smoke test` (11m 37s total) all succeeded, including the smoke test.
- Desktop CI #44 (`33992464220`, alongside the Native Keyring work, §5.11) — still green, confirming no regression: `Windows installer build and smoke test` succeeded end-to-end including the packaging guard and smoke test (30s).

**Full installer lifecycle verified** (both in CI, to the extent the smoke test covers, and locally, covering stages the CI smoke test does not yet exercise): install (MSI and NSIS both independently confirmed buildable and installable) → locate installed application (bundled backend resource present) → launch the real installed Tauri executable → backend process exists and `/health` becomes reachable (4-7s across multiple runs) → kill the backend process → Tauri's existing supervisor restarts it under a new PID → `/health` reachable again (1-2s) → close application → reinstall over the existing installation → database confirmed byte-identical (SHA-256) across reinstall → uninstall → installed binaries removed, bundled backend removed → application data (database, `storage_data`) confirmed preserved and byte-identical. Run twice consecutively from a genuinely fresh machine state (no prior app-data), both fully passing, to distinguish deterministic behavior from flakiness.

**Explicitly not resolved by this acceptance** (owner decisions, not silently closed):
- **OD-DI-2 (code signing)**: both MSI and NSIS remain **unsigned**. No certificate, signing identity, or CI signing credential exists anywhere in the repository — confirmed by direct inspection of `tauri.conf.json`/`tauri.windows.conf.json` (`certificateThumbprint`/`signingIdentity` absent) and `.github/workflows/desktop-ci.yml` (no signing step). This is a distribution/release-engineering decision, not fabricated or invented here. See `docs/release/RELEASE_CANDIDATE_READINESS.md` for its RC classification.
- **OD-DI-3 (macOS/Linux desktop distribution)**: explicitly out of scope, not built, not faked. Windows x64 is the only released desktop target.
- **Keyring native backend**: was a real, separate defect discovered during this milestone's own smoke-test debugging and deliberately deferred to its own dedicated pass rather than opportunistically fixed here — see §5.11, now also DONE.

**Package footprint** (informational, not a release blocker): MSI ≈134.7 MB, NSIS ≈103.1 MB, frozen backend ≈17.4 MB (onedir, pre-archive). No build, CI, or runtime failure has been caused by this size at any point across five CI runs.

### 5.9 CI/CD — DONE

**Formally accepted this pass.** The original implementation record below (§5.9's pre-existing text, retained verbatim as history) explicitly disclosed that no actual GitHub Actions run had ever occurred at the time it was written. That gap is now closed: both workflows have since executed **dozens of times** across the Docker (§5.7), Desktop Installers (§5.8), and Native Windows Keyring (§5.11) work — including genuine failures caught and fixed through real CI evidence, not merely passed on the first try:
- Desktop CI runs `33967913065` (Docker milestone, unaffected job confirmation), `33981635499`/`33985352738`/`33989632849`/`33992464220` (Desktop Installer + Keyring milestones — #41 and #42 both contain real, CI-only-reproducible failures that were diagnosed and fixed using CI evidence itself, exactly the kind of "a genuinely broken PR is actually blocked" proof the original record flagged as unverified).
- Backend CI runs `33967913057` (Docker) through `33992464212` (this pass's own HEAD) — consistently green across every push in this span.
- The Rust `ubuntu-latest` runner behavior flagged as "Not verified" in the original record has since been exercised for real, repeatedly, including catching a genuine Linux-only `cargo check` regression (Desktop CI #41) that Windows-only local testing could not have caught — direct evidence resolving that stated unknown.

No changes were made to the workflows' scope, triggers, or non-goals as part of this acceptance — this is a status change reflecting accumulated real-world execution evidence, not new implementation.

**Original implementation commit**: see `git log` for the `ci: establish production hardening CI validation` commit immediately following this document's original writing. **Files introduced at that time**: `.github/workflows/backend-ci.yml`, `.github/workflows/desktop-ci.yml`. No other file touched — `git status`/`git diff --stat` confirmed only `.github/` as new, untracked content before that commit.

**Original plan** (preserved for record; see "Planned this pass" wording it superseded): objective, why-it's-next, dependencies, explicit scope/non-goals, and acceptance criteria as originally written are retained below as history and were followed as written.

- **Objective**: automated lint + test pipelines that run on every push/PR, covering backend (Python), desktop (TypeScript), and Rust/Tauri.
- **Dependencies**: none.
- **Explicit non-goals honored**: no release/deploy pipeline, no artifact/installer building, no code-signing automation, no change to `pre-commit` config or `QUALITY_GATES.md`'s thresholds, no Docker, no deployment automation, no migration/boot-behavior change.

**CI scope actually implemented**:
- `backend-ci.yml` — one job, `ubuntu-latest`, Python 3.12 (`backend/pyproject.toml`'s own `requires-python`), `pip install -r requirements-dev.txt`, then `ruff check .`, `ruff format --check .`, `mypy src`, `pytest -q` — the exact commands already used locally throughout this session and by `.pre-commit-config.yaml`, no new tooling.
- `desktop-ci.yml` — two jobs: `frontend` (`pnpm/action-setup` reading the repo's own pinned `packageManager` field, Node 22 — a CI-required default since no Node version is declared anywhere in the repo, `pnpm install --frozen-lockfile`, then `pnpm typecheck`/`pnpm test`, which the root `package.json` already fans out across both workspace packages); `rust` (toolchain pinned to `1.77`, matching `apps/desktop/src-tauri/Cargo.toml`'s own declared `rust-version`, `cargo check` as the blocking gate, `cargo clippy` run informationally with `continue-on-error: true` since no repo-established clippy-strictness convention exists).
- Triggers: `push`/`pull_request` to `main` only (the repo's default branch), with `concurrency`/`cancel-in-progress` to avoid redundant runs.

**Validation performed** (local, pre-commit — no actual GitHub Actions run has occurred yet, since nothing is pushed):
- YAML syntax: both files parse cleanly via `python -c "import yaml; yaml.safe_load(...)"`. No `actionlint` (GitHub-Actions-schema-aware linter) was available in this environment — deeper workflow-schema validation was not performed and is explicitly not claimed.
- Backend commands run locally, exactly as the workflow invokes them: `ruff check .` → **2073 pre-existing errors** across the backend; `ruff format --check .` → **169 files** would be reformatted; `mypy src` → **87 pre-existing errors** in 26 files (includes one environment-only gap, `Library stubs not installed for "yaml"`); `pytest -q` → consistent with every full-suite run this session (2440+ passed, a handful of known pre-existing/environmental/contention failures, none related to CI/CD).
- Frontend commands run locally: `pnpm typecheck` → clean, 0 errors (both `design-system` and `apps/desktop`). `pnpm test` → clean, 575 tests passed (design-system: 20 files/50 tests; apps/desktop: 74 files/525 tests, run separately after a combined run was cut off by an unrelated local timeout, not a real failure).
- Rust commands run locally **on Windows**, not the Ubuntu runner the workflow targets: `cargo check` → clean, 0 errors. `cargo clippy -- -D warnings` → 1 pre-existing lint (`large_enum_variant` in `sidecar.rs`) — confirms the design choice to run clippy informationally, not as a blocking gate. **Not verified**: whether `cargo check`/`clippy` succeed on the actual `ubuntu-latest` runner — Tauri/`keyring`/`window-vibrancy` and other platform-conditional dependencies could behave differently on Linux than the Windows toolchain used for this local check.
- **Not executed** (requires an actual CI runner, not available in this environment): the real GitHub Actions execution of either workflow; any evidence a genuinely broken PR is actually blocked by these checks in practice.

**Known limitations** (disclosed, not concealed):
1. **Repository Debt Remediation (COMPLETED)**: The pre-existing lint, formatting, typing, and test dependency debt has been formally resolved:
   - Ruff lint: 0 errors (`python -m ruff check .`).
   - Ruff format: 0 files needing reformatting (463 files formatted, `python -m ruff format --check .`).
   - Mypy: 0 errors in 235 source files (`python -m mypy src`).
   - Dependencies: `backend/requirements.txt` synchronized with `backend/pyproject.toml` (`tzdata`, `pdfplumber`, `rapidocr-onnxruntime`).
   - Tests: Full backend suite clean; all 19 pre-existing historical test failures resolved (0 failures).
2. Node version (22) is a CI-required default, not a repo-declared value — no `.nvmrc`/`engines` field exists anywhere in the repo.
3. Rust toolchain validated locally only on Windows; Ubuntu-runner behavior for `cargo check`/`clippy` is unverified until the workflow actually executes on GitHub Actions.
4. `cargo clippy` runs informationally (non-blocking) rather than as a hard gate, since no repo-established clippy-strictness convention exists — this is a deliberate scope-discipline choice, not an oversight.
5. As designed, this workflow validates the repository; it does not build, sign, publish, or deploy anything — Docker, installer signing, and release automation remain entirely separate, unimplemented work packages.
6. All three owner decisions (§3 — Recovery Engine interpretation, deployment topology, migration boot-integration strategy) remain fully unresolved; nothing in this CI/CD implementation encodes an assumption about any of them.

### 5.10 Fresh-Machine Production Validation — PENDING

**REPOSITORY-DERIVED, not a roadmap line item.** `backend/tests/e2e/test_m71_cold_start.py` proves desktop-sidecar-managed cold start (already certified, M7.1). No equivalent test exists for a bare/server-only deployment. `kernel_bootstrap.py`'s own docstring states the ephemeral-key fallback (when `KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY` are unset) is "acceptable for M3's demonstration scope, not for a shipped product" — in production this is mitigated only because the desktop sidecar's `secure_keys.rs` supplies those env vars; a bare backend/server deployment has no such guarantee. `_default_sqlite_url()`'s storage directory default (`_default_app_data_dir()`) is a proper cross-platform path — but a separate `KORTEX_STORAGE_DIR`-controlled default elsewhere in `kernel_bootstrap.py` (`_DEFAULT_STORAGE_DIR = "kortex_api_storage"`) is cwd-relative, a real fresh-machine footgun for non-desktop deployments. Depends on Owner Decision #2 (§3) — validation scope depends entirely on which topology(ies) are authorized. **Not touched by this pass** — Docker's own fresh-machine path is separately covered by its own CI smoke test (§5.7); this item concerns a bare/non-containerized server deployment specifically, which remains unauthorized.

### 5.11 Production Secret Storage / Native Windows Keyring — DONE

**Status**: **DONE** (formally accepted this pass).

**Accepted implementation commit**: `98d94b4c4da9cbec0b3c50af6894af88f8796aae` (`fix(desktop): enable native Windows Credential Manager for keyring`), from baseline `f55b6bb4a1597ff52da1eb4229af6e5e4ce89360` (the accepted Desktop Installers baseline, §5.8).

**Defect found and fixed**: `apps/desktop/src-tauri/Cargo.toml` declared `keyring = "3"` with no platform feature enabled. Per the `keyring` crate's own source (`keyring-3.6.3/src/lib.rs`), this resolves to the crate's in-memory `mock` credential store on **every** platform, including Windows, unless a platform-native feature (`windows-native`, `apple-native`, `linux-native`/`*-secret-service`) is explicitly enabled. `secure_keys.rs`'s `KeyringKeyStore` and `ipc.rs`'s `KeyringTokenStore` were therefore never touching Windows Credential Manager in any build produced before this fix, including every prior Desktop Installer verification claim in §5.8 — those claims about "keyring failures fail closed instead of silently regenerating identity" were true against the mock, never proven against real OS persistence, until this pass. **Directly confirmed, not inferred**: a disposable probe crate mirroring the exact pre-fix dependency configuration stored a value in one process; a separate process reading it back reported `CONFIRMED_ABSENT`, not `Found`.

**Corrective change**: `keyring = { version = "3", features = ["windows-native"] }` — the smallest change that activates the crate's existing native-backend support. `windows-native`'s extra dependencies (`windows-sys`, `byteorder`) are gated by the `keyring` crate itself under `[target.'cfg(target_os = "windows")']` (confirmed by reading its `Cargo.toml`), so this has no effect on non-Windows compilation and does not claim macOS/Linux native support. No change was needed to `secure_keys.rs`'s or `ipc.rs`'s existing `Found`/`ConfirmedAbsent`/`Unreadable` semantics — `keyring::Entry`/`keyring::Error` are backend-agnostic at the crate's public API, confirmed by reading `Entry::new`'s implementation directly, and the existing `unreadable_keyring_fails_closed_instead_of_silently_replacing_identity` unit test (against an in-memory `KeyStore` double) already proves that logic correctly, independent of which backend is active.

**Real Windows Credential Manager verification** (not mocked, proven twice):
- **Local, pre-commit**: a disposable probe ran the full lifecycle — fresh state (`ConfirmedAbsent`) → store (process A) → separate process B reads back, exact match → separate process C reads back, exact match → delete → `ConfirmedAbsent` again. Independently cross-checked via `cmdkey /list` (outside the `keyring` crate's own API): a real Windows Credential Manager entry (`LegacyGeneric:target=probe-account.kortex-keyring-probe-DISPOSABLE-TEST`) was observed to exist after store and be gone after delete.
- **In CI**: the same lifecycle is now a committed, `#[ignore]`-gated Rust test (`secure_keys::tests::real_windows_keyring_integration::real_windows_keyring_persists_across_process_restart`) that re-invokes the compiled test binary as genuinely separate OS processes — not merely a second in-process call, which the mock backend would also satisfy — under a disposable service/user identifier distinct from `KeyringKeyStore`'s real production identifiers, invoked by exact name in `.github/workflows/desktop-ci.yml`'s `windows-installer` job. No secret value is ever printed, logged, or asserted against directly — only lengths and equality booleans. **Ran in Desktop CI #44 (`33992464220`): success, 196s.**

**No regression to Desktop Installers (§5.8)**: the full installer lifecycle (install → launch → `/health` → crash/restart → reinstall-data-preservation → uninstall-data-preservation) was re-verified locally with this change in place, all passing, and Desktop CI #44's `Installer smoke test` step passed in 30s — no change to installer behavior, only to the dependency backing one internal mechanism.

**Verification results**:
- `cargo test --lib`: 48 passed, 0 failed, 2 ignored (the two new real-backend tests, `#[ignore]`-gated by design) — local and equivalent in CI.
- Real Windows keyring integration test: 3/3 consecutive local runs passed; 1/1 CI run passed (196s).
- `cargo check`: clean. `cargo fmt --check`: unrelated pre-existing drift confirmed present in `sidecar.rs` before this commit (not part of the CI baseline, which never runs `fmt`) — not introduced by this work, not fixed by this work (out of scope). `cargo clippy`: exactly the same one pre-existing, already-documented `large_enum_variant` warning in `sidecar.rs` — zero new warnings.
- `pnpm typecheck`: clean.

**Explicitly not resolved by this acceptance**:
- **macOS/Linux native credential-store parity** — out of scope; only Windows is a released target (§5.8's OD-DI-3).
- **OD-2 (§3, Docker secret-persistence parity)** — a distinct question about whether Docker's separate operator-supplied-secret model should *also* gain OS-keychain-equivalent persistence; this pass fixed the desktop sidecar's own mechanism, which is not the same question, and does not resolve it.

## 6. Critical Path (PROPOSED SEQUENCING — not roadmap text)

**Status as of this pass**: every item below except the last is now `DONE`. Retained as history to show the sequencing that was actually followed, not as a forward-looking plan.

```
Migrations (DONE, formally accepted)
    |
    +-- Update Engine (DONE — formally accepted this pass)
    +-- Backup Engine (DONE)
    +-- Docker (DONE)
    |
    v
Sentinel / Monitoring (DONE)
    |
    v
Desktop Installer (DONE — formally accepted this pass, §5.8)
    +-- Native Windows Keyring (DONE — formally accepted this pass, §5.11)
    +-- CI/CD (DONE — formally accepted this pass, §5.9; real GitHub Actions
    |          execution evidence now exists across all of the above)
    |
    v
[NOT DONE] Desktop installer code signing (OD-DI-2, §5.8) — release-engineering
           decision, see docs/release/RELEASE_CANDIDATE_READINESS.md
    |
    v
[NOT DONE] Fresh-machine production validation for a bare/server (non-Docker,
           non-desktop) topology — topology-dependent, Owner Decision #2 (§3),
           genuinely unauthorized and untouched
```

Recovery Engine no longer sits outside this chain as of this pass — Owner Decision #1 (§3) is resolved by acceptance of the as-built engine.

## 7. Parallel Work (PROPOSED SEQUENCING)

- CI/CD scaffolding has no dependency on Migrations and can start immediately, independently.
- Sentinel and Monitoring metrics work has no hard dependency on Migrations and could proceed in parallel with Update/Backup/Docker work.
- Desktop installer signing-identity acquisition (an ops/procurement task, not code) can proceed independently of everything else.

## 8. Production Gate

Evidence that must exist before declaring Production-Ready:
- **EXISTING ROADMAP REQUIREMENT**: all seven Phase 7 checklist items in `.kortex/roadmap.md:62-72` implemented. **Documentation inconsistency, flagged not silently resolved**: `.kortex/roadmap.md:65-75` still reads `Status: Planned` with all seven checkboxes unchecked, while this document now records all seven as formally accepted `DONE`. Per this document's own §0 governance rule ("if this document conflicts with the current roadmap... STOP and report the conflict — do not silently rewrite history"), this pass does **not** edit `.kortex/roadmap.md` itself — that file's own update ceremony is left for explicit owner action, reported here as a genuine, outstanding documentation-reconciliation item rather than resolved unilaterally.
- **REPOSITORY-DERIVED REQUIREMENT**: a real, tested schema-upgrade path exists (§5.1 — satisfied), since the roadmap's own "Update Engine" bullet is meaningless without one.
- **REPOSITORY-DERIVED REQUIREMENT**: the ephemeral-key-fallback risk documented in `kernel_bootstrap.py`'s own docstring is resolved or explicitly scoped out for whichever deployment topology(ies) are authorized (Owner Decision #2). **Resolved for the desktop-sidecar topology** (§5.11 — real OS keyring now backs key persistence). **Still open for a bare/server topology** (§5.10, unauthorized/untouched) and for Docker's own operator-supplied-secret model specifically (OD-2, §3).
- **PROPOSED SEQUENCING**: Migrations first (done), then the dependency-ordered chain in §6 — now complete except for installer signing and bare-server fresh-machine validation.

## 9. Out of Scope (all Production Hardening passes, unless a future pass is explicitly re-authorized for one of these)

New business modules, new AI features, new connectors, Marketplace redesign, vector/RAG, cloud federation, SaaS architecture, generic observability redesign beyond the named Monitoring Engine bullet, unrelated UX improvements, new orchestration engines, any change to BaseModule/Kernel registration/Finance capabilities, wiring RecipeEngine, building Marketplace write path, HR & Payroll, Operations, and any new milestone number (no M7.7).
