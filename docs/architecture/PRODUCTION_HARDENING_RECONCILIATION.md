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
| Phase 7 — Production Hardening — Sentinel Engine | **IMPLEMENTED — AWAITING REVIEW** | None | §5.2 | Pending review (§5.2) |
| Monitoring Engine | PENDING | None hard | §5.3 | Not planned yet |
| Backup Engine | PENDING | Migrations (now available) | §5.4 | Not planned yet |
| Recovery Engine | BLOCKED — PENDING OWNER DECISION | Owner Decision #1 (§3) | §5.5 | Not planned yet |
| Update Engine | PENDING | Migrations (now available) | §5.6 | Not planned yet |
| Docker Production Builds | PENDING | Migrations (now available) + Owner Decision #2 (§3) | §5.7 | Not planned yet |
| Desktop Installers | PENDING | CI/CD (for repeatable/signed builds) | §5.8 | Not planned yet |
| CI/CD | **IMPLEMENTED — AWAITING REVIEW** | None | §5.9 | Pending review |
| Fresh-Machine Validation | PENDING | Owner Decision #2 (§3) | §5.10 | Not planned yet |

**Database Migration Wiring is DONE** (formally accepted, §5.1). **CI/CD is IMPLEMENTED — AWAITING REVIEW** (§5.9). **Phase 7 — Production Hardening — Sentinel Engine is now IMPLEMENTED — AWAITING REVIEW** (§5.2) — the Sentinel Engine infrastructure is fully implemented, registered into Kernel bootstrap, verified against 41 unit/integration/failure-injection tests, and integrated into system health rollup, but this status is **not** `DONE`: formal architectural/owner acceptance is a separate, subsequent review pass. Every other work package remains `PENDING`/`BLOCKED` exactly as before — not touched, not advanced, not implemented. Do not treat `PENDING` as authorization to implement.

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

### 5.2 Phase 7 — Production Hardening — Sentinel Engine — IMPLEMENTED — AWAITING REVIEW

**Implementation record** (this pass):
- **Components Implemented**:
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
  - Integration verified with `BootEngine.run_system_health_checks()`, Kernel `invoke_capability()`, and clean Kernel shutdown.
  - `ruff` passed with 0 errors; `mypy` passed with 0 issues in 11 source files.
- **Status**: **IMPLEMENTED — AWAITING REVIEW** (not `DONE`). Formal architectural/owner review is pending.

### 5.3 Monitoring Engine — PENDING

`backend/src/kortex/engines/monitoring/__init__.py` is a 2-line docstring only; no other files; not registered. Classified **STUB**. No metrics/dashboard aggregation point exists anywhere — M7.6's AI telemetry (`AIToolCompletedEvent`, exporter counters) is scoped only to the AI engine, not cross-engine. No dedicated tests exist.

### 5.4 Backup Engine — PENDING

`backend/src/kortex/engines/backup/__init__.py` is a 1-line docstring only; no other files; not registered; zero backup/scheduling/retention logic anywhere in `backend/src`. Classified **ABSENT**. Now has its Migrations dependency satisfied (§5.1) — schema-version-aware backup is possible but not yet built.

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
1. **The backend workflow will almost certainly fail on its very first real run** — not due to any CI/CD defect, but because it faithfully surfaces pre-existing repository debt this pass was explicitly forbidden from fixing: 2073 ruff lint errors, 169 files needing reformatting, and 87 mypy errors, none introduced by CI/CD or Database Migration Wiring. This was a deliberate choice: weakening the checks (excluding files, loosening rules, adding `--exit-zero`) to make the pipeline "green" would conceal exactly the failures CI/CD exists to surface. A separate, explicitly authorized cleanup pass is required before this workflow can realistically gate merges.
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
