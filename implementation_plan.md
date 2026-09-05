# KORTEX OS — Docker Production Build
## Implementation Plan (Surgically Corrected)

**Target Milestone**: Docker Production Build (`.kortex/roadmap.md` Phase 7, bullet 6 of 7)
**Document Status**: READY FOR IMPLEMENTATION
**Architectural Baseline**: Commit `783425af201594a96b29736911d3fa2d2dd01418` (`main`, HEAD, working tree clean — re-verified during this correction pass)
**Governance**: `AGENTS.md`, `CLAUDE.md`, `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`
**Scope discipline**: Planning-only. No source, Docker, CI, migration, or test file was created or modified while producing this document — only this file changed (`git status --porcelain` re-confirmed: ` M implementation_plan.md`, nothing else).

This is a **surgical correction pass** over the prior version of this document, not a rewrite. It corrects internal inconsistencies identified in review: an unaddressed deployment-topology contradiction, an under-specified key-management boundary, an under-classified storage-root fix, an insufficiently-verified Recovery/Update bootstrap claim, an over-weighted migration-timing "owner decision" that repository evidence actually resolves, and a database-topology conclusion that needed re-justification rather than restatement. Every claim below is tagged **FACT** (verified repository evidence, re-confirmed during this pass where decision-critical), **ROADMAP-REQ** (existing roadmap requirement), **OWNER DECISION** (genuine ambiguity requiring sign-off), or **PROPOSAL** (this plan's recommended design).

---

## 1. Executive Summary

Docker Production Build is `.kortex/roadmap.md`'s sixth Phase 7 bullet, currently `PENDING`/classified **ABSENT** in `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md:61,256-258` **[FACT]**: zero `Dockerfile`/`docker-compose*`/`.dockerignore` exist; `docker/` contains only a `README.md` describing three files that don't exist. Docker is a deployment boundary around the existing KORTEX backend (a FastAPI/uvicorn ASGI application), not a new engine — it must consume Sentinel/Monitoring/Backup/Recovery/Update's existing public contracts without modifying them.

This correction pass resolves six specific issues raised in review:

1. **Deployment topology** was previously left internally inconsistent (treating standalone-server as assumed while also citing an "unresolved" owner decision). §4 now separates what the roadmap's own structure already settles from what remains genuinely open.
2. **Key management** is now given its own dedicated section (§5) explicitly answering "what survives container replacement" and "what is supplied at runtime vs. baked into the image," including License Engine's vendor-key model, which the prior pass omitted.
3. **Storage-root inconsistency** (§6) is now explicitly classified against the requested resolution taxonomy (config-only vs. prerequisite source correction) with justification for why it is config-only.
4. **Recovery/Update bootstrap wiring** (§8) was re-verified by fresh, direct, targeted source inspection during this pass — not merely trusted from the prior research pass — and the finding is unchanged but now evidenced more strongly (`grep -n "Recovery\|Update" backend/src/kortex/api/kernel_bootstrap.py` → zero matches).
5. **Migration timing** is reclassified: it was previously presented as an "owner decision"; repository evidence actually resolves *whether* Alembic must run (yes — `create_all()` cannot evolve an existing schema), leaving only *where it is invoked* as an implementation detail, not a decision requiring sign-off.
6. **Database topology** (SQLite vs. Postgres for v1 Compose) is re-verified, not merely restated, and remains SQLite-only — with the reasoning now explicit rather than assumed.

**Owner decisions remaining: exactly two** (§20), both explicitly non-blocking with a stated fallback. **Final verdict: READY FOR IMPLEMENTATION** (§24).

---

## 2. Current Baseline

**[FACT]** HEAD `783425af201594a96b29736911d3fa2d2dd01418`, branch `main`, working tree clean except this document. Graphify (`graphify-out/`) was regenerated in the prior pass and reports `built_at_commit: 783425af`, an exact match — not regenerated again this pass, per instruction, since no code changed.

**[FACT]** Accepted Phase 7 engines: Sentinel, Monitoring, Backup, Recovery, Update — all "DONE" or "IMPLEMENTED — AWAITING REVIEW" per `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` §4. None of their internals are reopened by this plan; the only touch point proposed anywhere in this document is a minimal, explicitly-owner-gated bootstrap *registration* addition (§8, §20 OD-1), which does not modify any engine's own code.

**[FACT]** `.kortex/roadmap.md:65-75` (verbatim Phase 7 list): Sentinel, Monitoring Engine, Backup Engine, Recovery Engine, Update Engine, **Docker production builds**, Desktop installers (Tauri .msi/.exe/.dmg) — seven bullets, no further elaboration, no dependency statement. Docker → Desktop Installers is the terminal pair in this file's own numbering (nothing follows). This is an **existing roadmap requirement**, not something this plan invents.

**[FACT]** CI/CD and fresh-machine validation are, per the reconciliation document's own governance rules (§0/§1), **repository-derived requirements** (supporting infrastructure the roadmap bullets implicitly need), not separate roadmap milestones — this plan does not promote them to roadmap status, and does not invent a new milestone number (no "M7.7").

---

## 3. Repository-Evidence Corrections

This section records what changed between the prior pass and this one, and why.

| Prior statement | Correction | Reason |
|---|---|---|
| Deployment topology treated as both assumed-resolved and cited-as-unresolved | Split into a roadmap-structural fact (a non-sidecar mode is definitionally required) and a narrower, genuinely open security-parity question (§4, §20 OD-2) | Internally contradictory as previously written |
| Key management folded into "Environment Configuration" with no dedicated treatment of License Engine's vendor keys or an explicit "what survives replacement" answer | Promoted to its own section (§5) with License Engine's compiled-key model added | Correction prompt explicitly required addressing license/vendor keys and the two named questions |
| Storage-root fix stated as "zero code change" without naming which of the three requested resolution categories it falls under | Explicitly classified as **Resolution A** (Docker runtime establishes the canonical root; application default unchanged) with justification for why B (source correction) is not needed (§6) | Requested explicit A/B/C classification |
| Recovery/Update bootstrap absence stated as a research-agent finding, corroborated but not re-verified fresh during synthesis | Re-verified directly this pass: `grep -n "Recovery\|Update" backend/src/kortex/api/kernel_bootstrap.py` → zero matches (not even the bare string appears) | Correction prompt explicitly distrusted the unverified claim |
| Migration timing (`alembic upgrade head` at entrypoint) listed as "Owner Decision B" | Reclassified: *whether* Alembic must run is resolved by repository evidence (`create_all()` cannot alter existing tables — proven by the accepted `test_create_all_and_alembic_schema_are_equivalent` test, which only proves equivalence on a fresh schema, not evolution); *where* to invoke it is an implementation detail | Repository evidence already answers the load-bearing question; only ergonomics remained, and the correction prompt disallows labeling resolved questions as owner decisions |
| SQLite-only Compose topology carried forward from the prior pass's recommendation | Re-verified independently this pass (not merely restated): `db.py:151-152` remains the only Postgres-specific code found anywhere in `backend/src`; conclusion unchanged, but now stated as **RESOLVED BY REPOSITORY EVIDENCE**, not a recommendation-that-happens-to-be-restated | Correction prompt required re-verification, not restatement |
| License Engine's key model was not addressed at all | Added: `LicenseEngine()` (`kernel_bootstrap.py:262`, zero constructor args) reads **no environment variables** (`grep` for `os.environ\|getenv\|KORTEX_` in `license/config.py` → zero matches); its trust anchor is a compiled Ed25519 public key in `license/crypto.py` (`ED25519_PUBLIC_KEY_LENGTH_BYTES`-validated "trusted root key"), the same baked-in-trust-anchor pattern as Update Engine's `COMPILED_VENDOR_UPDATE_KEYS` — **zero Docker secret-injection required for licensing** | Correction prompt explicitly named license/vendor public keys as a required topic |

---

## 4. Deployment Topology

**[FACT]** `.kortex/roadmap.md:65-75` lists "Docker production builds" as its own bullet, textually distinct from "Desktop installers (Tauri .msi/.exe/.dmg)." A Docker container cannot host a Tauri desktop shell (no GUI, no OS keychain access, no windowing system) — by construction, *building Docker Production Build at all* necessarily means building a non-desktop-sidecar runtime mode. This is not an inference this plan makes unilaterally; it is a direct structural consequence of the roadmap already listing Docker as a separate deliverable from the desktop shell.

**[FACT]** The repository already documents an intended headless entrypoint for exactly this mode: `apps/server/README.md:5-11,15-18` — *"Runs the KORTEX backend without the Tauri desktop shell, enabling: Server-based deployment for multi-user enterprise environments. Headless operation for CI/CD... Docker container deployment,"* with `uvicorn kortex.api.main:app --host 0.0.0.0 --port 8000` as its example command (`apps/server/` contains only this README — a planning stub, no runner code, but it corroborates intent, not code).

**Correcting the prior inconsistency**: `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md:47` frames "Owner Decision #2" as *"is KORTEX's production model exclusively desktop-sidecar-managed... or does it also need a standalone server/Docker deployment mode?"* — worded as if the existence of a non-sidecar mode were itself undecided. Given the roadmap's own bullet structure (above), that framing is **stale/overbroad**: the roadmap already answers "should a non-sidecar mode exist" (yes — it is the very thing this milestone builds). What that reconciliation entry actually gestures at, more precisely, is a **narrower, still-genuine** question: *should Docker be considered production-ready with full security parity to the desktop-sidecar's OS-keychain-backed secret persistence, or is a documented, reduced-parity posture (operator-supplied secrets) acceptable for v1?* That narrower question is real and is carried forward as **Owner Decision OD-2** (§5, §20) — it is not silently resolved, and it does not block this plan (§20 explains exactly how implementation proceeds either way).

| Claim | Classification |
|---|---|
| Docker production builds is a roadmap item | **ROADMAP-REQ** (`.kortex/roadmap.md:70`) |
| The repository has an intended headless/server entrypoint | **FACT** (`apps/server/README.md`) |
| A non-sidecar runtime mode is the thing this milestone builds | **RESOLVED BY REPOSITORY EVIDENCE** (structural consequence of the roadmap's own bullet separation — not silently assumed, explicitly reasoned above) |
| Whether Docker must achieve OS-keychain-equivalent secret-persistence parity before being "production-ready" | **OWNER DECISION (OD-2)** — genuinely open, non-blocking (§20) |

---

## 5. Key/Secret Management

**Exhaustive inventory of key/secret material relevant to a Docker deployment**, each answering "supplied at runtime or baked into the image?" and "survives container replacement?":

| Secret/key | Source of truth | Resolution/fallback | Supplied how (Docker) | Survives container replacement? |
|---|---|---|---|---|
| `KORTEX_MASTER_KEY` | `kernel_bootstrap.py:_resolve_key`, L116-128 | **Silent ephemeral `os.urandom(32)`** if unset — logged warning, not fail-closed at this layer (module docstring L10-16: *"acceptable for M3's demonstration scope, not for a shipped product"*) | Runtime env var / Docker secret — **never baked into the image** | **Only if the operator supplies the same value again.** Nothing in the current architecture persists it for a container; the only existing persistence mechanism (`secure_keys.rs`, OS keyring) is Tauri-desktop-only and has no container equivalent |
| `KORTEX_AUTH_SIGNING_PRIVATE_KEY` | same `_resolve_key` helper | same silent-fallback behavior | same as above | same as above |
| `KORTEX_BACKUP_KEY` (→ falls back to `KORTEX_MASTER_KEY`) | `backup/crypto.py:71-98` | **Fails closed** (`BackupEncryptionError`) if neither resolves | Runtime env var, optional (falls back to master key) | Same persistence caveat as `KORTEX_MASTER_KEY` if used standalone |
| Update Engine vendor signing keys (`COMPILED_VENDOR_UPDATE_KEYS`) | `update/crypto.py:28-31` | Compiled into source | **Baked into the image** (not a runtime secret — it is a public-key trust anchor, not a private credential) | N/A — identical across every container built from the same source, by design |
| License Engine trusted root key | `license/crypto.py` (`ED25519_PUBLIC_KEY_LENGTH_BYTES`-validated "trusted root key") | Compiled into source, same pattern as Update Engine's | **Baked into the image** | N/A — same reasoning as above; **zero environment variables are read anywhere in `license/config.py`** (re-verified this pass, zero grep matches) |
| `KORTEX_DATABASE_URL` | `db.py:127` | Falls back to OS-app-data-dir SQLite path | Runtime env var (this plan requires it be set explicitly — §6/§7) | Points at the persistent volume; the URL itself is not secret, but must be consistent across replacements |

**What survives container replacement, precisely**: the persistent volume (§7) and whatever secret values the operator's own secrets manager/orchestrator re-supplies identically on redeploy. **Nothing inside the image or the container's own filesystem persists a key across replacement** — this is not a Docker-specific weakness, it is the same limitation `kernel_bootstrap.py`'s own docstring already discloses for any non-desktop-sidecar topology. This plan does not invent a new key-management subsystem to solve it (explicitly prohibited); it uses the existing `KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY`/`KORTEX_BACKUP_KEY` env-var contract exactly as `kernel_bootstrap.py` and `backup/crypto.py` already define it.

**Missing-secret behavior, precisely**: `KORTEX_MASTER_KEY`/`KORTEX_AUTH_SIGNING_PRIVATE_KEY` unset → silent ephemeral generation (not a hard failure) at the Python layer. **[PROPOSAL]** A container-entrypoint preflight check (shell-level, not a Python code change) that refuses to start if either is unset/malformed converts this into a loud, fail-fast container-boundary behavior without touching the already-accepted Python fallback code — this is a Docker-layer safeguard, not a redesign of `kernel_bootstrap.py`.

**Can the current architecture safely support standalone Docker without a prerequisite security correction?** **Yes, with a disclosed limitation**: operator-supplied secrets via the container's own secrets mechanism (env var, Docker secret, orchestrator vault) is the standard, expected pattern for containerized applications generally, not a KORTEX-specific gap requiring invention — the same posture any twelve-factor app takes. What is *not* claimed is parity with the desktop sidecar's OS-keychain persistence; that gap is disclosed explicitly, not hidden, and is OD-2 (§20).

---

## 6. Storage Topology

**[FACT — re-confirmed]**: `kernel_bootstrap.py:57-58,141-143` defaults `KORTEX_STORAGE_DIR` to `"kortex_api_storage"` and constructs `StorageEngine(base_directory=storage_dir)`. `StorageEngine`'s own class default is `"storage_data"` (`storage/engine.py:35`). Backup/Recovery/Update are constructed with **zero** arguments in `kernel_bootstrap.py` (L265,268,271: `SentinelEngine()`, `MonitoringEngine()`, `BackupEngine()`) and independently hardcode `"storage_data/..."` relative constants in their own `constants.py` files, entirely decoupled from `KORTEX_STORAGE_DIR`. Left unconfigured, a container would split state across two directories.

**Resolution classification** (per the requested taxonomy):

> **A. Docker runtime explicitly establishes the canonical storage root, leaving the existing application default unchanged.** ✅ **This is the correct resolution.**

Justification for choosing A over B (a source-code prerequisite correction): `KORTEX_STORAGE_DIR` already exists precisely as an operator-facing configuration surface for this exact purpose — it is not a bug that it's configurable, it is the intended mechanism. Setting `KORTEX_STORAGE_DIR=storage_data` explicitly in the container environment, combined with a fixed, volume-backed working directory so the plain relative string `"storage_data"` resolves identically for `StorageEngine` and for Backup/Recovery/Update's independently-hardcoded relative paths, requires **zero source code changes** to any accepted engine. This is ordinary Docker configuration, not a prerequisite correction — and it does **not** mask data divergence by "mounting both directories" (explicitly rejected by the correction prompt): only **one** root (`storage_data`) is ever used in this design; `kortex_api_storage` is simply never allowed to come into existence in the first place, because the env var that would otherwise default to it is always set.

- **Affected components**: `StorageEngine` (documents/blobs), `BackupEngine` (backups/), `RecoveryEngine`/`UpdateEngine` (journals/staging — moot until §8/OD-1 wires them in, but the same alignment applies the moment they are).
- **Why it matters**: without this, `StorageEngine`'s file/object store and Backup's archive directory would silently diverge onto two roots — not a crash, a **silent correctness defect** (documents in one place, backups referencing a database that lives somewhere the backup process never captured consistently alongside it).
- **What the fix changes**: nothing in application behavior — it is a value assignment for an existing, already-read env var.
- **What must remain unchanged**: `StorageEngine`'s own class default (`"storage_data"`) and every Backup/Recovery/Update constant — none of these need or receive any edit.
- **Tests required**: an implementation-time integration smoke test (not a repository test-suite change) — create a document via `StorageEngine` inside the running container, take a Backup, confirm both land under the same volume subtree (`/data/storage_data/...`).

No prerequisite correction is required for this item (contrast with §8/§19, which does require one).

---

## 7. Database and Migration Strategy

**[FACT — database topology, re-verified this pass, not merely restated]**: `db.py:151-152` (`elif "postgresql" in self._url or "asyncpg" in self._url:`) remains, on fresh inspection, the **only** PostgreSQL-specific code anywhere in `backend/src`. No PostgreSQL-specific pooling, no PostgreSQL migration path, and no PostgreSQL-exercising test exists (the sole `asyncpg` hit in `backend/tests` is an architecture-purity regex banning direct `asyncpg` imports in engine code, not a Postgres test). `backend/README.md:10` and `.kortex/stack.md:15-20` both describe PostgreSQL as the primary/enterprise-mode store — this is aspirational documentation, contradicted by the actual, verified code.

> **Conclusion: RESOLVED BY REPOSITORY EVIDENCE, not a preference restated from the prior pass.** SQLite is the only database path this codebase has ever tested or wired end-to-end. Shipping a Postgres service in v1 Compose would present untested functionality as production-ready, which the plan does not do. This is a **factual** determination (what is actually supported today), not a values-based tradeoff between two equally-viable options — so it is not listed as an owner decision. If the owner later wants Postgres, that is new, separate, untested work requiring its own implementation and test pass — explicitly out of scope here, not silently declined.

**[FACT — migration mechanism]**: Four Alembic revisions exist in a linear chain, head `4c99c2ff7376`. `Kernel.boot()` (`kernel.py:154-155`) unconditionally calls `create_all_tables()` on every boot (`Base.metadata.create_all`, `checkfirst=True` — creates missing tables only, **never alters or evolves an existing table**). No registered production engine invokes Alembic; it exists today only as a manual CLI operation. The reconciliation document's own critical-path note (§6 of that document, itself explicitly labeled "PROPOSED SEQUENCING — not roadmap text, not authoritative") already suggests: *"Docker (container-level migration-on-start, not create_all())."*

**Reclassifying migration timing** (correcting the prior pass's over-weighting of this as "Owner Decision B"):

- **Whether Alembic must run to support real production schema evolution across releases** — **RESOLVED BY REPOSITORY EVIDENCE.** `create_all()` is structurally incapable of altering an existing table (add a column, change a type, etc.); only Alembic can. There is no genuine alternative here, so this is not an owner-level choice between equally valid options.
- **Where/when to invoke `alembic upgrade head`** (a container entrypoint step, vs. a separate manual/CI deploy step) — **IMPLEMENTATION DETAIL.** This plan recommends the entrypoint (§12): it runs once, synchronously, before `uvicorn` starts, using the same `KORTEX_DATABASE_URL` the app itself uses (one source of truth, matching `alembic.ini`'s own design comment). Idempotent on restart (proven by the accepted `test_upgrade_head_when_already_at_head_is_idempotent` test). No destructive downgrade is ever attempted or proposed, consistent with the existing forbidden-in-place-downgrade rule already established for Update Engine's own Alembic model.

**Required behaviors, addressed explicitly**:
- **Migration failure**: entrypoint uses `set -e`; a failed `alembic upgrade` exits non-zero before `uvicorn` starts — the container never serves traffic against a half-migrated schema.
- **Startup failure**: same as above — fails loud, not silent.
- **Retry behavior**: governed by the orchestrator's restart-policy (`unless-stopped`/similar); a migration that fails deterministically will fail identically on every retry until the operator intervenes — this plan does not invent automatic remediation.
- **Concurrent containers**: Alembic's own `alembic_version` table plus `checkfirst`-safe DDL make a second container's `alembic upgrade head` (e.g., during a rolling redeploy) a safe no-op if the first container already migrated to head; this plan does not add new locking, since Alembic already handles the idempotent-reapplication case and this repository does not run multiple writer replicas today (single-container topology, §11).
- **Schema compatibility**: enforced the same way it already is outside Docker — an old application version is not guaranteed compatible with a newer migrated schema; that is an existing, unchanged property of the repository's migration model, not something Docker introduces or must additionally solve.
- **Update Engine interaction**: Update Engine's own Alembic-driving code (`migrator.py`) is unreachable today (§8) and, even once wired, only runs during an explicit `kortex.update.apply` call — a different lifecycle moment than container boot. Within this container topology (§8's resolution: Update Engine's live-mutation capability is out of scope for containers), the practical update path is: new image (with new migrations baked into `backend/alembic/versions/`) → redeploy → entrypoint's `alembic upgrade head` carries the DB forward.
- **Recovery interaction**: if a migration fails destructively, Recovery Engine's existing snapshot-restore design is the correct remedy — but since Recovery is unreachable today (§8), the practical v1 fallback is an **operator-driven, volume-level restore** from an independently-taken backup, not an automated `kortex.recovery.create` call. Disclosed, not hidden.
- **No new Docker-specific migration framework is introduced** — this uses Alembic exactly as already configured.

---

## 8. Docker vs. Update Engine Boundary

**[FACT]** `applier.py:41-46` defaults `target_root` to `Path(__file__).resolve().parents[3]`, which resolves to `backend/src` — the live Python source tree. `PROTECTED_PATHS` (`applier.py:21-35`) explicitly excludes `storage_data/backups`, `storage_data/.recovery`, `storage_data/.update`, the SQLite DB files, `alembic.ini`/`alembic/env.py`/`alembic/script.py.mako`, `.venv`, `.git` — but **not** general `backend/src/kortex/**` code, confirming that path is Update Engine's intended mutation target. A Docker image's application-code layer is normally read-only at runtime; Update Engine's default swap therefore cannot safely execute against a standard immutable-image container without either a writable code mount (undermining the immutability the image format is meant to guarantee) or a purpose-built container-mode variant that does not exist in this codebase today.

**[FACT — re-verified]** No `KORTEX_CONTAINER`/`.dockerenv` detection exists anywhere in `backend/src` (zero grep matches for either string). A prior, separate planning document (the repository's own now-superseded Update Engine plan, preserved only in git history) had drafted such a detection scheme (`VERIFY_AND_MIGRATE_ONLY` mode) but it was never implemented — it is not part of the accepted Update Engine and this plan does not resurrect or implement it.

**Explicit boundary (this plan's scope decision, not an owner decision — there is no reasonable alternative given image immutability is the very assumption this milestone is built on)**:

| Responsibility | Owner |
|---|---|
| KORTEX application/repository-level update semantics (staged file swap, Alembic-driven schema migration *during an update*, crash-consistent rollback via Recovery) | **Update Engine** — unchanged, not reopened, not modified by this plan |
| Container image lifecycle (build new image with new code/migrations baked in → validate → replace the running container → preserve the persistent volume) | **Docker deployment model** — this plan |

**This plan does not claim Update Engine can mutate an immutable production image.** It does not invent `docker pull`-driven self-update, registry-orchestration, hot reload, or in-container image replacement — none of these exist in the repository and none are proposed. Within the Docker topology, Update Engine's live-mutation capability (`kortex.update.apply`'s filesystem-swap behavior) is **out of scope / unsupported for containers**; "updating KORTEX in Docker" means image rebuild + container replacement, full stop.

---

## 9. Backup/Recovery Compatibility

**[FACT]** Backup **is** reachable today (`BackupEngine()` registered, `kernel_bootstrap.py:271`). Its capabilities (`kortex.backup.{create,list,get,verify,delete,diagnostics.get}`) work unmodified inside a container once §6's storage-root alignment is applied — backups land under `/data/storage_data/backups/` and survive container replacement via the persistent volume.

**[FACT — re-verified this pass, directly, not merely corroborated]**: `grep -n "Recovery\|Update" backend/src/kortex/api/kernel_bootstrap.py` returns **zero matches** — the bare strings "Recovery" and "Update" do not appear anywhere in that file, not in an import, a comment, or a conditional registration block. `grep -rn "RecoveryEngine(\|UpdateEngine("` across all of `backend/src` matches only the engines' own module/interface files — never any external call site. Both engines' dedicated test suites (`test_recovery_*`, `test_update_*`, 7 files) construct them directly and in isolation, not via the shared production bootstrap. **Outcome: (B) — they are genuinely NOT wired**, confirmed by fresh direct inspection this pass, not assumed from the prior research pass.

**Classification of this discrepancy**:
- **Is it required for Docker productionization?** No — Docker packaging itself (image, entrypoint, volumes, health, CI) is fully specifiable and testable without Recovery/Update being reachable; only the specific "Recovery compatibility verified"/"Update boundary verified" acceptance gates become conditional.
- **Is it a pre-existing platform integration gap?** Yes — it predates and is independent of Docker; it affects every topology (desktop sidecar included) equally.
- **Is it a separate prerequisite?** Yes, if the owner wants Recovery/Update capabilities reachable through the running application at all (§20 OD-1) — but it is **not** a Docker-specific defect and Docker does not need it fixed to be correctly scoped.
- **Does Docker planning use this as an excuse to modify the accepted engines?** No. This plan does not implement the wiring; it documents the gap and gates it behind explicit owner authorization (§19, §20 OD-1). Recovery's and Update's own internals are not reopened, audited, or modified by anything in this document.

---

## 10. Sentinel/Monitoring Compatibility

**[FACT]** Both are registered in `kernel_bootstrap.py` (L265, L268) and reachable today — no gap analogous to §9 exists here. Sentinel: 3 capabilities (`kortex.sentinel.{health,status,diagnostics}.get`), `SentinelStatus` enum (`STARTING,HEALTHY,DEGRADED,FAILED,UNKNOWN,STOPPING,DISABLED`), 30s default startup grace period. Monitoring: 4 capabilities (`kortex.monitoring.{metrics,timeseries,dashboard,diagnostics}.get`), consumes Sentinel's public `kortex.sentinel.health.changed` event, both 100% ephemeral/in-memory.

**[PROPOSAL]** Docker's `HEALTHCHECK` (§13) consumes only the existing unauthenticated `GET /health` route — it does not call these authenticated capabilities directly. This is consuming an existing public contract, not inventing a new health surface.

---

## 11. Image Architecture

**[PROPOSAL]** Single backend-only image, no bundled database service. Multi-stage build on `python:3.12-slim-bookworm` (Debian family): a builder stage installs `backend/requirements.txt` into a venv (no compiled toolchain required — every dependency ships manylinux wheels for `cp312`/`x86_64`, proven by CI's own successful `pip install` with only `libgl1`/`libglib2.0-0` as system deps, `backend-ci.yml:39-40`); a runtime stage copies the venv, `backend/src`, and `backend/alembic/{env.py,alembic.ini,script.py.mako,versions/}`, installs the same two system libraries, creates a non-root `kortex` user, and runs via `docker/entrypoint.sh` (§12). Alpine is explicitly not recommended — nothing in this repository validates musl-libc compatibility for OpenCV/ONNXRuntime, and CI's own proven-working set is Debian/Ubuntu `apt` packages.

**[PROPOSAL]** Non-root: `RUN mkdir -p /data && chown kortex:kortex /data` before `VOLUME ["/data"]`/`USER kortex`, so a fresh named volume inherits correct ownership at first creation.

**Environment variables** (§5's secrets plus §6's storage alignment): required — `KORTEX_MASTER_KEY`, `KORTEX_AUTH_SIGNING_PRIVATE_KEY` (operator-supplied, never defaulted or baked in); required-explicit — `KORTEX_STORAGE_DIR=storage_data`, `KORTEX_DATABASE_URL=sqlite+aiosqlite:////data/storage_data/kortex_local.db`; optional — `KORTEX_BACKUP_KEY` (falls back to master key if unset, per existing contract).

**Ports**: 8000 only, bound `0.0.0.0` inside the container (the desktop sidecar's `127.0.0.1` bind is not reachable across a container network namespace — a standard Docker networking fact, not a repository gap). No TLS termination exists in the application (`main.py` has no TLS/SSL code) — a reverse proxy/TLS terminator in front of the container is the operator's responsibility, not invented here.

---

## 12. Startup/Shutdown

**[PROPOSAL — entrypoint sequence]**:
```
1. Validate KORTEX_MASTER_KEY / KORTEX_AUTH_SIGNING_PRIVATE_KEY are set and
   well-formed -> fail fast if not (container-boundary safeguard; does not
   modify kernel_bootstrap.py's own Python-level fallback).
2. alembic -c backend/alembic.ini upgrade head
3. exec uvicorn kortex.api.main:app --host 0.0.0.0 --port 8000
```
Step 3 triggers the existing, unchanged FastAPI lifespan → `build_and_boot_kernel()` → `Kernel.boot()` → connect DB → `create_all_tables()` (harmless no-op post-migration, §7) → boot 16 engines/modules in dependency order.

**[FACT — implementation-time unknown]** No repository evidence measures cold-start duration for the full boot sequence (`DocumentIntelligenceEngine`'s OCR-model loading is the likely long pole). `HEALTHCHECK --start-period` must be set from an actual measurement taken during implementation, not invented here.

**[FACT — shutdown]** No custom Python `signal.signal`/`atexit` handling exists anywhere in `backend/src` — shutdown relies entirely on uvicorn's built-in SIGTERM/SIGINT → ASGI-lifespan-shutdown (`main.py:56-58`) → `Kernel.shutdown()` → engines' `.stop()` in reverse boot order → DB disconnect. This is uvicorn's standard, already-relied-upon behavior; nothing new is required.

**[PROPOSAL]** Set an explicit, generous `stop_grace_period` (e.g. 30s, not Docker's 10s default) in production Compose — an in-flight Backup/Recovery/Update operation holding a maintenance lock may need more than 10s to reach a safe interruption point. A harder kill after the grace period is recoverable, not catastrophic: every one of these engines already has a journal-driven crash-recovery sweep designed for exactly this scenario (proven by the accepted crash-matrix tests).

---

## 13. Health/Readiness

**[FACT — exact semantics, re-confirmed]** `GET /health` (`main.py:291-297`): HTTP 200 if `report["system_health"]["status"]` is `"healthy"` or `"degraded"`, else 503. Response body also carries `kernel_state`, `db_dialect`, `db_connected`, `bootstrap_required` (a first-run-setup flag, not a failure signal, does not affect status code).

**Distinguishing the five states the correction explicitly asked for**:
- **Process alive**: the container process is running; nothing about `/health` is required for this — it's `docker ps`/PID-level.
- **HTTP reachable**: per standard ASGI/uvicorn lifespan behavior, the server does not process `_any_` HTTP request — including `/health` itself — until the FastAPI lifespan's startup step (`build_and_boot_kernel()`) completes (`main.py:50-58` sets `app.state.kernel` only after that awaited call finishes, and every route handler dereferences it with no defensive None-check, confirming the code relies on this ASGI guarantee). **There is no "process alive but HTTP-visibly-still-initializing" state exposed by this endpoint** — until boot finishes, connections to `/health` simply do not get a response (or the TCP connection is refused/queues), indistinguishable at the Docker healthcheck level from "still starting" vs. "crashed" except by whether the process is still running at all.
- **KORTEX initialized**: the instant `/health` responds at all, the full 16-engine boot sequence has already completed successfully — reachability and initialization are the same event for this application, by construction.
- **KORTEX healthy / degraded**: both return 200 (the existing, unchanged contract) — Docker's binary healthcheck **cannot** distinguish these; a finer distinction is available only to a caller that reads the JSON body directly (e.g. Monitoring Engine's own dashboard, already doing exactly this via public capabilities). This plan does not invent a finer-grained Docker-level signal — that would mean inventing readiness semantics the application does not itself expose.

**[PROPOSAL]**:
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=<measured> --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status<400 else 1)"
```
Stdlib-only (no `curl`/`wget` added to the minimal image).

---

## 14. CI/CD

**[FACT]** Neither `backend-ci.yml` nor `desktop-ci.yml` contains any Docker step; both explicitly scope this out in their own header comments today.

**[PROPOSAL — extend, not redesign]** One new job appended to the existing CI, gated on the current lint/test job: `docker build` → start the container with fresh, throwaway, CI-generated secrets (never committed) → poll `/health` until 200 (bounded by the measured start-period) → `docker stop`, assert clean exit → `docker image inspect` sanity checks (non-root `USER`, no `.git`/`.env` in the image). No registry-publish step (not required by the roadmap bullet). No new vulnerability-scanning platform introduced (none exists today; `docker scout`, bundled with the Docker CLI, is noted as an optional owner-requested addition, not a requirement).

---

## 15. Testing Matrix

| Area | Verification |
|---|---|
| Build | Clean `docker build`; `.dockerignore` keeps `.git`/`.venv`/tests out of the build context |
| Startup | Fresh container + fresh volume boots cleanly; preflight correctly refuses a missing/malformed key; `alembic upgrade head` succeeds from an empty volume; `/health` reaches 200 within the measured start-period |
| Core runtime | `kortex.security.auth.authenticate` (bootstrap-exempt) succeeds against a freshly-migrated empty DB; a representative authenticated call (e.g. `kortex.monitoring.dashboard.get`) succeeds end-to-end |
| Security | Non-root process; `KORTEX_MASTER_KEY` absent from `docker history --no-trunc`; filesystem outside `/data` not writable by `kortex` |
| Persistence | `docker stop`/`start` and `compose down`/`up` (volume-preserving) retain documents/DB rows/backups |
| Backup | `kortex.backup.create` writes under `/data/storage_data/backups/`, survives restart |
| Recovery/Update | **Conditional on OD-1 (§20)**: if wired, verify `kortex.recovery.create`/`kortex.update.check` reachable via `POST /capabilities/invoke`; if not, explicitly marked N/A, never claimed as tested |
| Sentinel/Monitoring | `/health` reflects a simulated degraded condition; `kortex.monitoring.dashboard.get` returns populated telemetry |
| Shutdown | `docker stop` completes within grace period under normal conditions; a SIGKILL mid-Backup is recoverable on next start via the existing crash-recovery sweep |
| Migrations | `alembic upgrade head` run twice in a row (simulated restart) is idempotent |
| CI | New Docker job passes; existing jobs remain green, unmodified in behavior |

---

## 16. Security Validation

- Non-root execution (`docker exec ... whoami`).
- No secret material in any image layer (`docker history --no-trunc` grep for key material — none should ever be present since none is ever baked in, §5).
- `.dockerignore` excludes `.git`, `.venv`, `backend/tests`, `node_modules`, caches, `docs/`, `graphify-out/`, `apps/desktop/` (unrelated Tauri/Rust/TS code not imported by the backend), and the root placeholder directories (`data/`, `logs/`, `knowledge/`, `marketplace/`, `recipes/`, `templates/`, `sdk/`, `shared/` — README-only stubs; implementation-time grep should confirm no backend import reaches any of these before finalizing the exclusion list, since this was not exhaustively re-verified this pass).
- Filesystem write surface limited to `/data`.
- Exposed ports limited to 8000.
- Dependency provenance: `pip install -r backend/requirements.txt` with no hash-pinning today (no `pip-tools`/lockfile in the repo) — noted as a possible future hardening item, not required for this milestone's acceptance.
- No new vulnerability-scanning platform introduced without repository precedent (§14).

---

## 17. Failure Matrix

| Failure | Behavior |
|---|---|
| Image build failure | CI fails at `docker build`; no image tagged |
| Invalid/missing secret | Entrypoint preflight rejects explicitly and fast (§5, §12) |
| Storage volume missing | `Path.mkdir(parents=True, exist_ok=True)` (used everywhere, no code hardens against this) recreates empty directories rather than erroring — **a misconfigured/missing volume produces a silently-fresh empty instance, not a loud failure**. This is the single most important disclosed risk in this plan (carried from the prior pass, re-confirmed, not resolved by new code — operators must verify volume-mount configuration explicitly) |
| Permission failure | `PermissionError` surfaces in container logs during `Kernel.boot()` — diagnosable, not silent |
| Migration failure | Entrypoint exits non-zero before serving traffic; no in-place downgrade attempted; operator restores `/data` from an independent backup (§7) |
| Healthcheck failure | Orchestrator-specific restart/removal behavior — outside this application's and this plan's control |
| Process crash / container restart | Restart-policy restarts; once Recovery/Update are wired (OD-1), their journal-driven crash-recovery sweep resolves any interrupted operation on next boot — already proven by the accepted crash-matrix tests |
| Corrupted persistent state | Recovery Engine's snapshot-restore, once wired; until then, operator-driven volume-level restore (§7) |
| Backup/Recovery unavailable | Backup: existing fail-closed behavior unchanged. Recovery: N/A until OD-1, explicitly disclosed |
| Update interaction | Out of scope for containers by design (§8) — not a failure, a defined boundary |
| Insufficient disk space | Existing `shutil.disk_usage` preflight checks in Backup/Recovery/Update apply unchanged inside a container; margins were sized for desktop use and should be re-validated against real container storage-driver overhead at implementation time |

---

## 18. Scope / Non-Goals

**IN SCOPE**: production image, runtime packaging, persistent storage topology, runtime configuration, startup/shutdown, health, SQLite-only migration operation, security hardening, CI validation, container runtime smoke tests, Backup/Recovery/Update compatibility (Recovery/Update conditional on OD-1), Sentinel/Monitoring compatibility, minimal operational documentation.

**OUT OF SCOPE** (no repository evidence requires otherwise): Kubernetes, Helm, Terraform, cloud infrastructure, Docker Swarm, service mesh, Marketplace deployment, registry-release automation, Desktop installers, Windows executable replacement, runtime pip installation, dependency modernization beyond the one flagged drift (§19), database redesign, storage redesign, new KORTEX engines, new migrations solely for Docker, hot reload, unrelated refactoring, reopening any accepted engine's internals.

---

## 19. Prerequisite Corrections

Items in this section are **not** Docker configuration — they are source-level changes this plan documents but does **not** implement, each requiring its own explicit authorization.

### PC-1: Wire `RecoveryEngine`/`UpdateEngine` into `kernel_bootstrap.py`

- **Current defect/inconsistency**: `RecoveryEngine`/`UpdateEngine` are implemented, tested, and accepted, but never instantiated in `kernel_bootstrap.py` — their 12 capabilities are unreachable through the running application on every topology.
- **Evidence**: `grep -n "Recovery\|Update" backend/src/kortex/api/kernel_bootstrap.py` → zero matches (re-verified this pass, directly).
- **Why Docker exposes it**: Docker's own acceptance gates ask for "Recovery compatibility verified"/"Update boundary verified" — without this wiring, those gates can only be marked N/A, not genuinely verified through the running container.
- **Minimal correction**: two additional `kernel.register_engine(...)` calls, matching the existing pattern for Sentinel/Monitoring/Backup (`kernel_bootstrap.py:265,268,271`).
- **Does it change existing behavior?**: additive only — makes two already-defined, already-accepted capability sets reachable; creates no new capability, event, or migration (§9's classification stands).
- **Tests required**: full backend regression re-run; re-run `test_production_capability_permissions.py` (or equivalent) to confirm no regression in boot-order/dependency-resolution behavior now that two more engines participate in `BootEngine`'s topological sort.
- **Owner approval required**: **Yes** — this is §20 OD-1. Not implemented here.

### PC-2 (minor): `aiosqlite` version-pin drift

- **Current inconsistency**: `backend/pyproject.toml:15` declares `aiosqlite>=0.20.0`; `backend/requirements.txt:14` declares `aiosqlite>=0.22.0`.
- **Evidence**: direct read of both files (prior pass).
- **Why Docker exposes it**: this plan recommends building the image from `requirements.txt` (the file CI already trusts) — a persistent drift risks the image silently diverging from what `pyproject.toml`-based installs (e.g. a contributor's `pip install -e ".[dev]"`) actually validate.
- **Minimal correction**: bump `pyproject.toml`'s pin to match.
- **Does it change existing behavior?**: no functional change expected (both are lower-bound version floors already satisfied by whatever version is actually installed today).
- **Tests required**: none beyond the existing suite already passing with the current installed version.
- **Owner approval required**: low-risk; can be bundled with this milestone's implementation pass without a separate sign-off, but is listed here for visibility rather than silently folded in.

**No other prerequisite corrections were identified.** The storage-root inconsistency (§6) is explicitly **not** listed here — it is resolved entirely through Docker-layer configuration, requiring no source change.

---

## 20. Owner Decisions

| Decision | Status | Evidence | Recommendation | Blocking? |
|---|---|---|---|---|
| **OD-1**: Authorize wiring `RecoveryEngine`/`UpdateEngine` into `kernel_bootstrap.py` (PC-1) | OWNER DECISION REQUIRED | §9, §19 PC-1 — zero registration confirmed by direct, fresh grep | Authorize; it is additive, low-risk, and required for this milestone's own stated Recovery/Update integration requirement to be genuinely checkable | **No.** If declined, Docker packaging (§11-§17) proceeds entirely unchanged; only the Recovery/Update rows in the testing matrix (§15) and acceptance gates (§22) are marked N/A instead of verified |
| **OD-2**: Is OS-keychain-equivalent secret-persistence parity (matching the desktop sidecar) required before Docker is considered "production-ready," or is operator-supplied-secrets-via-standard-container-mechanisms an acceptable v1 posture? | OWNER DECISION REQUIRED | §5 — `secure_keys.rs` is Tauri-only; `kernel_bootstrap.py`'s own docstring already discloses this gap for any non-sidecar topology, not something Docker introduces | Accept operator-supplied secrets for v1 (the standard containerized-application pattern); defer a stronger persistence mechanism to a future, separately-scoped hardening pass | **No.** The packaging plan (§5, §11) fully specifies the operator-supplied-secret contract regardless of the answer; the decision only affects whether additional, separate work is later authorized to build a stronger mechanism |

**Everything else previously considered for owner-decision status has been resolved by repository evidence** (§3 table): deployment-topology existence (§4), storage-root fix classification (§6), migration-timing mechanism (§7), database topology (§7), and the Update Engine/image-immutability boundary (§8) — none of these remain open questions.

---

## 21. Implementation Sequence

```
1. Docker build foundation (.dockerignore, docker/Dockerfile.backend)
2. Entrypoint (docker/entrypoint.sh: key preflight -> alembic upgrade head -> exec uvicorn)
3. Storage/env alignment (KORTEX_STORAGE_DIR=storage_data, explicit KORTEX_DATABASE_URL)
4. Compose topology (docker/docker-compose.yml, docker/docker-compose.prod.yml)
5. Health/readiness wiring (HEALTHCHECK -> existing /health)
6. [Conditional on OD-1] PC-1: kernel_bootstrap.py Recovery/Update registration
   -- independent of 1-5/7-11, proceeds in parallel once authorized
7. CI integration (new build+smoke-test job)
8. Security validation (non-root, secret-leakage, .dockerignore effectiveness)
9. Runtime smoke tests (full §15 matrix)
10. Documentation (rewrite docker/README.md; confirm CONTRIBUTING.md needs no change)
11. Production acceptance evidence (§22 gate run; repository tests/ruff/mypy re-confirmed green;
    Graphify re-verified built_at_commit == final HEAD)
```

---

## 22. Acceptance Gates

- [ ] Production image builds (multi-stage, non-root final layer).
- [ ] Container starts cleanly against a fresh, empty volume (preflight + `alembic upgrade head` succeed).
- [ ] `/health` reaches 200 within the measured start-period; simulated failure returns 503.
- [ ] Persistent volume retains state across `docker stop`/`start` and `compose down`/`up`.
- [ ] `alembic upgrade head` succeeds from empty and is idempotent on restart.
- [ ] Authentication and a representative capability dispatch succeed through the container's HTTP boundary.
- [ ] Backup compatibility verified.
- [ ] Recovery/Update compatibility: verified if OD-1 is authorized and implemented; otherwise explicitly N/A with reason documented.
- [ ] Sentinel/Monitoring compatibility verified via existing public contracts.
- [ ] Graceful shutdown verified within grace period; SIGKILL-after-timeout recoverable.
- [ ] No secret material in any image layer; non-root execution confirmed.
- [ ] New Docker CI job passes; existing CI jobs remain green, unmodified in behavior.
- [ ] Full backend test suite remains green; ruff and mypy pass repository-wide.
- [ ] Graphify regenerated against the final implementation commit; `built_at_commit == HEAD`.
- [ ] Working tree clean before requesting commit authorization.

---

## 23. Open Questions

Both carried in §20 (OD-1, OD-2) — no further open questions remain. Neither blocks implementation of the approved Docker scope; both are explicitly non-blocking with a stated fallback if declined.

---

## 24. Final Planning Verdict

```
================================================================================
DOCKER PRODUCTION BUILD PLAN — READY FOR IMPLEMENTATION
================================================================================
```

Both remaining owner decisions (§20) are non-blocking, each with a fully-specified path forward regardless of the answer — implementing the approved Docker packaging scope does not require either to be resolved first. No genuine architectural ambiguity prevents an implementation agent from knowing what production mode KORTEX's Docker deployment supports: a headless, non-desktop-sidecar server mode, running the existing FastAPI/uvicorn application, backed by SQLite on a persistent volume, with Recovery/Update integration conditional on a minimal, separately-authorized bootstrap-wiring prerequisite (PC-1). Every architectural claim in this document is classified as repository fact, roadmap requirement, owner decision, or implementation proposal; none of the six issues raised in review remain unaddressed.

**Final verification checklist**:
1. HEAD confirmed unchanged: `783425af201594a96b29736911d3fa2d2dd01418`. ✅
2. No source/Docker/CI/test file modified — only this document. ✅ (`git status --porcelain` → ` M implementation_plan.md` only)
3. Only `implementation_plan.md` changed. ✅
4. No credentials/secrets exposed anywhere in this document. ✅
5. Every major claim classified (FACT/ROADMAP-REQ/OWNER DECISION/PROPOSAL). ✅
6. Owner Decision #2 (deployment topology) not silently resolved — narrowed and carried forward as OD-2, with the stale/overbroad prior framing explicitly explained (§4). ✅
7. Storage-root inconsistency explicitly classified (Resolution A) and handled (§6). ✅
8. Recovery/Update bootstrap finding directly re-verified this pass, not assumed (§9, §19 PC-1). ✅
9. Migration strategy evidence-backed, reclassified from owner-decision to resolved-mechanism-plus-implementation-detail (§7). ✅
10. Docker vs. Update Engine responsibilities explicit (§8). ✅
11. No new roadmap milestone invented. ✅
12. Final verdict consistent with the two explicitly-non-blocking owner decisions. ✅
