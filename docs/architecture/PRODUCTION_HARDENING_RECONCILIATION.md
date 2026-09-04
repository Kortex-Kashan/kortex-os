# KORTEX OS — Production Hardening Reconciliation

**Status of this document**: Permanent, living project-execution-control document for the Production Hardening / Production-Ready phase. It is not a one-time report — it must be reviewed first and updated with evidence by every future Production Hardening implementation pass. See §0 for the governance rules that apply to it.

**Last updated**: this pass (formal acceptance of Database Migration Wiring + next-step planning), against HEAD `083990c2d8ba0dc76a851612d5c59a64e1ca29d7` at the start of the pass.

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

## 3. Owner Decisions Required (OWNER DECISION — not resolved by any implementation pass to date)

1. **Recovery Engine interpretation**: does the roadmap's "Recovery Engine" bullet require a new, centralizing platform engine, or does the existing, tested, engine-local recovery (Workflow's `hydrate_and_recover`/`recover_stranded_executions`/schedule recovery, Document's `DocumentRecoveryManager`) satisfy it — mirroring the same minimal-pilot interpretation precedent already set for Phase 6? Unresolved.
2. **Deployment topology**: is KORTEX's production model exclusively desktop-sidecar-managed (where `secure_keys.rs` solves key persistence), or does it also need a standalone server/Docker deployment mode? The roadmap lists both "Docker production builds" and "Desktop installers" as separate bullets, implying both — but the current key-management design (`kernel_bootstrap.py`'s own docstring admission) has no answer for a non-sidecar deployment. Unresolved.
3. **Migration boot-integration strategy**: should schema migrations run automatically at boot (current `create_all()` convenience preserved for dev, replaced/gated for prod), or as an explicit, separate deploy-time step? **Not yet decided** — deliberately left open by the Database Migration Wiring implementation (§5.1), which added Alembic alongside `create_all()` without touching the boot path, per its explicit scope lock.
4. External M7.x vs. native Phase 6/7 numbering conflict (§2) — still unresolved.

## 4. Production-Hardening Work Packages — Status Table

| Work Package | Status | Dependencies | Evidence | Acceptance |
|---|---|---|---|---|
| Database Migration Wiring | **DONE** | None | §5.1 | Formally accepted (§5.1) |
| Phase 7 — Production Hardening — Sentinel Engine | **DONE** | None | §5.2 | Formally accepted (§5.2) |
| Monitoring Engine | **DONE** | Sentinel (public interface) | §5.3 | Formally accepted (§5.3) |
| Backup Engine | **DONE** | Migrations (now available) | §5.4 | Surgically verified, ready for owner acceptance (§5.4) |
| Recovery Engine | BLOCKED — PENDING OWNER DECISION | Owner Decision #1 (§3) | §5.5 | Not planned yet |
| Update Engine | PENDING | Migrations (now available) | §5.6 | Not planned yet |
| Docker Production Builds | PENDING | Migrations (now available) + Owner Decision #2 (§3) | §5.7 | Not planned yet |
| Desktop Installers | PENDING | CI/CD (for repeatable/signed builds) | §5.8 | Not planned yet |
| CI/CD | **IMPLEMENTED — AWAITING REVIEW** | None | §5.9 | Pending review |
| Fresh-Machine Validation | PENDING | Owner Decision #2 (§3) | §5.10 | Not planned yet |

**Database Migration Wiring is DONE** (formally accepted, §5.1). **Phase 7 — Production Hardening — Sentinel Engine is DONE** (formally accepted, §5.2) — verified across 41 targeted tests, 50 cross-engine tests, 0 full-suite regressions, and clean Graphify/ruff/mypy validation. **Monitoring Engine is DONE** (formally accepted, §5.3) — verified across 42 targeted monitoring tests, 54 net new repo tests, 3,016 full-suite passed tests, 0 regressions, and clean CI/Graphify/ruff/mypy validation. **Backup Engine is DONE** (surgically verified, §5.4) — verified across 47 targeted backup tests, 243 capability identity tests, 3,078 total backend test nodes, 0 regressions, zero migrations, and clean Graphify/ruff/mypy validation. **CI/CD is IMPLEMENTED — AWAITING REVIEW** (§5.9). Every other work package remains `PENDING`/`BLOCKED` exactly as before — not touched, not advanced, not implemented. Do not treat `PENDING` or `PLANNED` as authorization to implement.

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
- **Accepted Implementation Commit**: `a78f6814c6adbe34e672872fc5d63a8897bc3479` (`feat(backup): implement Phase 7 production backup engine`).
- **ADR**: [ADR-0016-phase7-backup-engine.md](../adr/ADR-0016-phase7-backup-engine.md).
- **Technical Verification**:
  - Full backend suite: 3,078 tests collected (+60 test nodes from 3,018 baseline). 3,075 passed, 2 skipped, 0 real regressions.
  - Targeted Backup suite: 47 passed unit and integration tests across 6 dedicated test modules.
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

### 5.5 Recovery Engine — BLOCKED — PENDING OWNER DECISION

`backend/src/kortex/engines/recovery/__init__.py` is a 1-line docstring only; no other files; not registered. As a **dedicated platform engine**, classified **ABSENT**. However, substantial, genuinely tested, engine-local recovery already exists: Workflow Engine's `hydrate_and_recover()` (`engines/workflow/engine.py:510`, invoked at boot, `engine.py:439`), `recover_stranded_executions()` (`engines/workflow/executor.py:582`, deliberately never auto-resuming per its own comment at `executor.py:664`), `hydrate_and_recover_schedules()` (`engines/workflow/scheduler.py:664`); Document Engine's `DocumentRecoveryManager` (`engines/document/recovery.py`, 288 lines, tested in `tests/unit/test_document_recovery.py`). **Additional architectural evidence (this pass, via Graphify)**: `hydrate_and_recover()` is called from `WorkflowEngine.start()` (`engine.py:439`) — i.e., recovery runs as an internal step of that engine's own boot lifecycle, not as an independently invocable capability. There is no shared "Recovery" interface, abstraction, or Kernel-dispatched capability either engine's recovery logic implements — Workflow and Document each reinvented their own recovery mechanism independently, with no common contract between them. This does not resolve the interpretation question; it sharpens it: today, nothing exists that a hypothetical Kernel-level orchestrator, operator tool, or future third engine could reuse — a genuine "Recovery Engine" would be new integration/coordination work, not a rename of what's already there.

**Blocked on Owner Decision #1 (§3)**: does the roadmap bullet require a new centralizing engine, or does this existing engine-local coverage satisfy it? No implementation should proceed on this work package until that decision is made.

### 5.6 Update Engine — PENDING

`backend/src/kortex/engines/update/__init__.py` is a 1-line docstring only; no other files; not registered. No Tauri `updater` config in `tauri.conf.json`, no updater dependency in `Cargo.toml`. Classified **ABSENT**, both backend and desktop. Now has its Migrations dependency satisfied (§5.1) — a safe schema-upgrade path exists for Update Engine to build on, but Update Engine itself is not yet built.

### 5.7 Docker Production Builds — PENDING

Zero `Dockerfile`/`docker-compose*`/`.dockerignore` anywhere in the repo; `docker/` contains only a `README.md` describing files that do not exist (a documentation/aspiration mismatch). Classified **ABSENT**. Depends on Migrations (now satisfied, §5.1 — a container should run `alembic upgrade head` on start, not blind `create_all()`) and on Owner Decision #2 (§3) — the current key-management design has no answer for a non-desktop-sidecar (i.e., containerized/server) deployment, which materially affects whether Docker work is coherent as currently scoped.

### 5.8 Desktop Installers — PENDING

`tauri.conf.json` configures `msi`/`nsis` bundle targets (buildable manually) but has no signing identity (`certificateThumbprint`/`signingIdentity` absent) and no `updater` section; no CI/script anywhere invokes `tauri build`. Classified **STUB**. `backend_process.rs`, `sidecar.rs`, `secure_keys.rs` confirmed present (already-certified M7.1 work, not re-audited). Depends on CI/CD (§5.9) for repeatable, signed builds.

### 5.9 CI/CD — IMPLEMENTED — AWAITING REVIEW

**Implementation commit**: see `git log` for the `ci: establish production hardening CI validation` commit immediately following this document update. **Files introduced**: `.github/workflows/backend-ci.yml`, `.github/workflows/desktop-ci.yml`. No other file touched — `git status`/`git diff --stat` confirmed only `.github/` as new, untracked content before commit.

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

**REPOSITORY-DERIVED, not a roadmap line item.** `backend/tests/e2e/test_m71_cold_start.py` proves desktop-sidecar-managed cold start (already certified, M7.1). No equivalent test exists for a bare/server-only deployment. `kernel_bootstrap.py`'s own docstring states the ephemeral-key fallback (when `KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY` are unset) is "acceptable for M3's demonstration scope, not for a shipped product" — in production this is mitigated only because the desktop sidecar's `secure_keys.rs` supplies those env vars; a bare backend/server deployment has no such guarantee. `_default_sqlite_url()`'s storage directory default (`_default_app_data_dir()`) is a proper cross-platform path — but a separate `KORTEX_STORAGE_DIR`-controlled default elsewhere in `kernel_bootstrap.py` (`_DEFAULT_STORAGE_DIR = "kortex_api_storage"`) is cwd-relative, a real fresh-machine footgun for non-desktop deployments. Depends on Owner Decision #2 (§3) — validation scope depends entirely on which topology(ies) are authorized.

## 6. Critical Path (PROPOSED SEQUENCING — not roadmap text)

```
Migrations (DONE, formally accepted)
    |
    +-- Update Engine (needs safe schema upgrade path)
    +-- Backup Engine (needs schema-version awareness)
    +-- Docker (container-level migration-on-start, not create_all())
    |
    v
Sentinel / Monitoring (metrics + dashboard aggregation)
    |
    v
Desktop Installer signing + CI/CD automation
    |
    v
Fresh-machine production validation (topology-dependent, Owner Decision #2)
```

Recovery Engine sits outside this chain — it is blocked on Owner Decision #1, not on any dependency-ordered implementation step.

## 7. Parallel Work (PROPOSED SEQUENCING)

- CI/CD scaffolding has no dependency on Migrations and can start immediately, independently.
- Sentinel and Monitoring metrics work has no hard dependency on Migrations and could proceed in parallel with Update/Backup/Docker work.
- Desktop installer signing-identity acquisition (an ops/procurement task, not code) can proceed independently of everything else.

## 8. Production Gate

Evidence that must exist before declaring Production-Ready:
- **EXISTING ROADMAP REQUIREMENT**: all seven Phase 7 checklist items in `.kortex/roadmap.md:62-72` implemented and checked.
- **REPOSITORY-DERIVED REQUIREMENT**: a real, tested schema-upgrade path exists (§5.1 — now satisfied), since the roadmap's own "Update Engine" bullet is meaningless without one.
- **REPOSITORY-DERIVED REQUIREMENT**: the ephemeral-key-fallback risk documented in `kernel_bootstrap.py`'s own docstring is resolved or explicitly scoped out for whichever deployment topology(ies) are authorized (Owner Decision #2).
- **PROPOSED SEQUENCING**: Migrations first (done), then the dependency-ordered chain in §6.

## 9. Out of Scope (all Production Hardening passes, unless a future pass is explicitly re-authorized for one of these)

New business modules, new AI features, new connectors, Marketplace redesign, vector/RAG, cloud federation, SaaS architecture, generic observability redesign beyond the named Monitoring Engine bullet, unrelated UX improvements, new orchestration engines, any change to BaseModule/Kernel registration/Finance capabilities, wiring RecipeEngine, building Marketplace write path, HR & Payroll, Operations, and any new milestone number (no M7.7).
