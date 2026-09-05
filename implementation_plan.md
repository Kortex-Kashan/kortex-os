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

---
---

# PART 2 — DESKTOP INSTALLERS (Discovery & Planning)

**Target Milestone**: Desktop installers — Tauri `.msi` / `.exe` / `.dmg` (`.kortex/roadmap.md` Phase 7, bullet 7 of 7, the last item)
**Document Status**: IMPLEMENTED (Windows) — AWAITING REVIEW. Phases A-G executed and verified this pass with real evidence (§D25); macOS (Phase E/G macOS leg) not attempted (OD-DI-3 unresolved); signing not attempted (OD-DI-2, deferred by design). Not committed/pushed pending explicit authorization.
**Baseline**: HEAD `c553915cc90ad391d96974e01224a82d1c4a9c45` (Docker reconciliation closure commit; Docker itself accepted at `b4b5ffdc734bd339c97710532eb4c91bf1502ba9`)
**Governance**: `AGENTS.md`, `CLAUDE.md`, `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`, `ADR-0002`, `docs/architecture/phase3_desktop_architecture.md`
**Scope discipline**: planning only. No Tauri/Rust/TypeScript/Python source, no CI workflow, no Docker file, no migration was created or modified while producing this section — only `implementation_plan.md` changed. Docker, Sentinel, Monitoring, Backup, Recovery, Update, and Phase 6 are not reopened.

Every claim below is tagged **FACT** (verified repository evidence), **ROADMAP-REQ** (existing roadmap requirement), **ARCH-REQ** (an already-ratified architectural decision, e.g. an ADR), **OWNER DECISION** (genuine ambiguity requiring sign-off), or **PROPOSAL** (this plan's recommended design, not yet built).

---

## D1. Executive Summary

Desktop Installers is the seventh and final Phase 7 bullet (`.kortex/roadmap.md:70-71`), the direct successor to the now-`DONE` Docker Production Build. Unlike Docker — which packaged an already-runnable dev-mode command (`uvicorn kortex.api.main:app`) into a container — Desktop Installers exposes a genuine, previously-undiscovered gap: **the current Tauri desktop shell cannot spawn a backend at all on a real end-user machine today.** `backend_process.rs::backend_source_dir()` resolves the backend's location via `CARGO_MANIFEST_DIR`, a Rust compile-time constant baked into the binary at *build* time (the path of `apps/desktop/src-tauri` on whichever machine ran `cargo build`) — on any machine without that exact source checkout, `canonicalize()` fails deterministically, and the sidecar is left permanently `Disabled` (`backend_process.rs:37-51`, module's own comment: *"A genuinely distributed build without the monorepo source tree present fails `canonicalize()` here"*). This is not a Docker-style "make the existing thing more robust" milestone — it requires building the thing that makes a real installer possible at all: a frozen, Python-interpreter-free backend the Tauri shell can locate reliably on an arbitrary installed machine.

Three additional, previously-undiscovered defects compound this, all confirmed by direct code+library-source reading, not inferred:

1. **`UpdateApplier`'s default file-swap target** (`applier.py:41-46`, `Path(__file__).resolve().parents[3]`) assumes the exact `backend/src/kortex/engines/update/applier.py` depth of a dev-source checkout — under any frozen-executable layout this resolves to an unpredictable, possibly-nonexistent, possibly-wrong-but-writable location. Currently dormant only because Update Engine is unreachable (§9).
2. **`UpdateMigrator`'s default `alembic.ini` path** (`migrator.py:35-45`, `Path(__file__).resolve().parents[4]`) has the identical dev-layout assumption — and independently, Alembic's own installed-library code (`alembic/util/pyfiles.py:52-73`, `alembic/script/base.py:134-193`) proves that `alembic.ini`'s `script_location = alembic` (a bare, un-anchored relative string with no `%(here)s` token) resolves against **the process's current working directory at runtime**, not against `alembic.ini`'s own directory — meaning Alembic cannot find its own migration scripts unless cwd is exactly `backend/`, regardless of freezing. Also currently dormant only because Update Engine is unreachable.
3. **`opencv-python-headless` is declared but `opencv-python` (full, non-headless) is what's actually installed** in the dev venv (`pip show opencv-python-headless` → not found; `opencv_python-5.0.0.93` present instead) — a real, pre-existing dependency-manifest drift, pulling in ~117MB of extra native binaries (`cv2.pyd` 86.3MB + a bundled ffmpeg DLL 30.9MB) that a headless install would not need.

None of these three are fixed by this planning pass. All three are documented as evidence for the plan's owner-decision and prerequisite-correction sections, exactly as the Docker plan's own precedent established.

**Recommendation in one sentence**: freeze the backend with **PyInstaller in `--onedir` mode** (not `--onefile` — see §D6), have the Tauri shell locate it via a new, install-relative resolution mechanism (not today's compile-time-baked path), configure Windows `.msi`/`.exe` (NSIS) as the fully-validated v1 target (matching what CI can actually build and test today), and treat macOS `.dmg` as an established-but-not-yet-produced target pending a macOS build runner — while flagging plainly that this narrows, rather than fully satisfies, the roadmap bullet's literal `.dmg` naming (§D18, OD-DI-3).

---

## D2. Current Baseline

**[FACT]** HEAD `c553915cc90ad391d96974e01224a82d1c4a9c45`, branch `main`, working tree clean before this pass. Graphify regenerated this pass (stale at start: `built_at_commit b4b5ffdc` vs. HEAD `c553915c`) — now `built_at_commit == c553915c`, 17,665 nodes, 41,084 edges, 567 communities.

**[FACT]** `.kortex/roadmap.md:62-70`: Phase 7 lists Sentinel, Monitoring, Backup, Recovery, Update, Docker production builds, **Desktop installers (Tauri .msi / .exe / .dmg)** — seven bullets, Desktop Installers last, nothing follows it in this file's native numbering. Docker Production Builds is `DONE` (§4/§5.7 of `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`, commit `b4b5ffdc`, reconciliation commit `c553915c`). Sentinel/Monitoring/Backup are `DONE`; Recovery/Update are `IMPLEMENTED — AWAITING REVIEW`; none are reopened by this pass.

**[FACT]** Reconciliation document's existing entry (`PRODUCTION_HARDENING_RECONCILIATION.md:262` — pre-dates this pass, not yet updated): *"§5.8 Desktop Installers — PENDING: `tauri.conf.json` configures `msi`/`nsis` bundle targets (buildable manually) but has no signing identity... and no `updater` section; no CI/script anywhere invokes `tauri build`. Classified **STUB**. `backend_process.rs`, `sidecar.rs`, `secure_keys.rs` confirmed present (already-certified M7.1 work, not re-audited). Depends on CI/CD (§5.9) for repeatable, signed builds."* This pass does not change this entry's status (§D24 below) — it remains `PENDING` per explicit instruction.

---

## D3. Repository Evidence — Existing Desktop Architecture

**[FACT — full runtime lifecycle, verified by direct read of `backend_process.rs`/`sidecar.rs`/`backendReadiness.ts`/`AuthProvider.tsx`, all in full]**:

- **Backend readiness detection** is frontend-driven HTTP polling of the existing `GET /health` (not a Rust-side health check): `backendReadiness.ts:80-106` (`waitForBackendReady`), exponential backoff `250ms → 500ms → 1s → 2s → 4s → 5s (capped) ×3` across `DEFAULT_MAX_ATTEMPTS = 8` (`:40-43`), worst case ≈19s. Also reads `bootstrap_required` off the same `/health` body to route to first-run setup (`:50-56`). Exhausting all 8 attempts renders `BackendUnavailableScreen.tsx` (42 lines, one "Retry" button calling `retryConnection()`).
- **Sidecar restart policy**: `RestartPolicy::ratified()` (`sidecar.rs:36-43`) — max 3 attempts, 100ms/200ms/400ms backoff; a 4th unexpected exit yields `SidecarOutcome::Failed`. Monitored once/second (`backend_process.rs:174-230`, `MONITOR_INTERVAL = 1s`).
- **Graceful shutdown**: `SidecarManager::graceful_shutdown` (`sidecar.rs:257-284`) closes the child's stdin as an EOF cue, polls every 20ms up to a **30-second** timeout (`sidecar.rs:94`, doc comment cites *"the ratified 30 second graceful-shutdown grace period... `phase3_desktop_architecture.md` §6.4"*), then `force_terminate()`. Also the `Drop` safety net (`sidecar.rs:309-317`).
- **Shutdown-vs-crash disambiguation**: a `ShutdownIntentFlag` (`lib.rs:36`) is set before intentional shutdown so `monitor_loop`'s crash detection doesn't attempt to restart a deliberately-stopped sidecar (`lib.rs:73-80`, `backend_process.rs:158-162,178-180`).
- **Today's exact spawn command** (re-verified unchanged): `python -m uvicorn kortex.api.main:app --host 127.0.0.1 --port 8000`, `PYTHONPATH=<backend_dir>/src`, interpreter via `KORTEX_PYTHON_EXECUTABLE` env → `<backend_dir>/.venv/Scripts/python.exe` → bare `"python"` (`backend_process.rs:53-114`). Escape hatch `KORTEX_BACKEND_COMMAND` (`:33,81-93`) exists, confirmed unused anywhere else in the repo.
- **`backend_source_dir()`, the actual blocker** (`backend_process.rs:37-51`, full body): resolves via `PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..").join("backend")` — `env!("CARGO_MANIFEST_DIR")` is a **compile-time** constant, fixed to wherever the crate was built, not a runtime lookup. Exact quoted comment: *"A genuinely distributed build without the monorepo source tree present fails `canonicalize()` here and falls through to the caller's non-fatal degradation."* `spawn_and_monitor` (`:130-140`) catches this `Err`, `eprintln!`s, and leaves `SidecarSupervision::Disabled` — **no backend spawns at all** on an installed machine unless `KORTEX_BACKEND_COMMAND` is set (and even then, `working_directory` is left `None`, so the child simply inherits the Tauri host process's own OS-assigned cwd — not anything this codebase controls).
- **Capabilities/permissions** (`apps/desktop/src-tauri/capabilities/default.json`, 15 lines, full read): `core:default`, `allow-invoke-capability`, `allow-connect-event-stream`, `allow-has-session`, `allow-logout`, `allow-get-system-health` — its own description states *"No shell or filesystem permissions. All network egress to the backend is Rust-mediated through these commands only."* Zero `shell:*`/`fs:*`/`http:*` plugin permissions anywhere. This is the current, narrow, already-good security posture an installer must not widen without cause.
- **`Cargo.toml`** (full read): `tauri = "=2.11.5"`, `tauri-build = "=2.6.3"`, `rust-version = "1.77"` declared — but **CI actually builds with `stable`** (desktop-ci.yml comment explains a locked dependency, `icu_normalizer 2.3.0`, requires `edition2024`, which Rust 1.77 rejects) — a real, pre-existing MSRV/lockfile mismatch, not something this pass introduces. `[profile.release]`: `panic = "abort"`, `lto = true`, `opt-level = "s"`, `strip = true` — already tuned for small release binaries. No platform-conditional `[target.'cfg(...)']` sections exist.
- **`package.json`**: no `tauri build` script defined anywhere (only a passthrough `"tauri": "tauri"`). `frontendDist: "../dist"` (`tauri.conf.json:10`) confirmed to match Vite's actual default output directory (`apps/desktop/dist`, no `build.outDir` override in `vite.config.ts`).
- **`desktop-ci.yml`, read in full (113 lines)**: both jobs run on **`ubuntu-latest`** — zero `windows-latest`, zero `macos-latest` anywhere in the repository's CI. Header comment (`:3-8`), quoted verbatim: *"Deliberately does not build/sign/publish installers, invoke `tauri build`, or run any release/deployment step."* `frontend` job: typecheck + vitest. `rust` job: `cargo check` (blocking) + `cargo clippy` (informational only, a pre-existing `large_enum_variant` lint in `sidecar.rs` cited as the reason).

**[FACT]** `tauri.conf.json` (36 lines, full read): `productName: "KORTEX Desktop"`, `version: "0.1.0"` (matches `Cargo.toml`, `backend/pyproject.toml`, `package.json` — all four at `0.1.0`), `identifier: "com.kortex.desktop"`, `bundle.targets: ["msi", "nsis"]` **only** — no `"dmg"`, no `"app"` target configured despite icons for both platforms already present (`icon.icns`, `icon.ico`). No `externalBin`, no `windows.wix`/`windows.nsis` customization block, no `upgradeCode`, no signing config, no `updater` plugin section. No install/uninstall-hook script anywhere.

**[ARCH-REQ]** `ADR-0002` (§4.2 item 2, line 66): *"**Sidecar packaging tool choice** (PyInstaller vs. Nuitka vs. other) for freezing the Python backend into a distributable sidecar binary. **Blocks**: M1's production-mode completion criteria, and §16 Production Build Architecture. Deferred pending a packaging spike."* — formally an open, ratified-as-deferred decision, not resolved by that ADR. §4.1 item 13 (line 58): *"desktop updates ship as a single Ed25519-signed unit via Tauri's updater plugin, reusing the existing platform signature scheme rather than introducing a second one"* — this is the ratified architecture for desktop-shell version upgrades (§D12 below), distinct from KORTEX's own Update Engine.

**[ARCH-REQ]** `docs/architecture/phase3_desktop_architecture.md` (grepped, relevant lines only): §6.3 (150-154) — the Python backend is intended to be packaged as a Tauri sidecar binary, tool choice open (§21). §16 Production Build Architecture (523-527): step 2, backend frozen into a single sidecar executable per OS (Windows primary; macOS/Linux per the stack's stated cross-platform intent); step 3, *"`tauri build` bundles the Rust binary, the built webview assets, and the frozen sidecar executable into a single signed application package per target OS (`.msi`/`.exe` for Windows in Phase 3; `.dmg`/AppImage packaging **deferred to Phase 7 polish** per `.kortex/roadmap.md`)."* §20 non-goals (598): *"Production-grade installer polish, code-signing certificate provisioning, or staged rollout tooling — deferred to Phase 7."* **This document's own "Phase 7" IS the Phase 7 this milestone belongs to** — its "deferred to Phase 7" language does not resolve the macOS-scope question, it just relocates it here (§D18, OD-DI-3).

---

## D4. Packaging Boundary

**[PROPOSAL]** The installer's job is exactly three things, and no more: (1) freeze the Python backend into a distributable, Python-interpreter-free artifact; (2) teach the Tauri shell to locate and launch that artifact reliably on an arbitrary installed machine (replacing today's compile-time-baked dev path); (3) bundle the Rust binary + webview assets + frozen backend into a signed, per-OS installer package (`.msi`/`.exe` via NSIS for Windows; `.dmg` for macOS). It does **not** redesign KORTEX's engines, does **not** become a second updater, backup, or recovery system, and does **not** change the existing capability-dispatch/security model — the desktop shell remains the sole process supervisor and lifecycle owner, exactly as it is today (`sidecar.rs`/`backend_process.rs` unchanged in responsibility, only in how they locate the backend executable).

---

## D5. Backend Freeze Analysis — Complete Runtime Asset Inventory

**[FACT — exhaustive inventory, gathered by inspecting the actual installed dev venv, not guessed]**:

| Asset | Evidence | Freezing implication |
|---|---|---|
| `cryptography` 50.0.0 | `.venv/Lib/site-packages/cryptography/hazmat/bindings/_rust.pyd` (9.94MB, Rust-backed) | Standard binary-wheel collection, well-supported by both freezers |
| `argon2-cffi` 25.1.0 + bindings | `.../_argon2_cffi_bindings/_ffi.pyd` (60.9KB) | Same |
| `onnxruntime` 1.28.0 (transitive via `rapidocr-onnxruntime`) | `.../onnxruntime/capi/onnxruntime.dll` (17.8MB), `onnxruntime_pybind11_state.pyd` (18.4MB), `onnxruntime_providers_shared.dll` (21.8KB) | Large native footprint; PyInstaller has a maintained community hook (`pyinstaller-hooks-contrib`) for this package |
| `rapidocr_onnxruntime` 1.4.4 package data | `config.yaml` (1.2KB) + 3 `.onnx` models under `models/` (`ch_PP-OCRv4_det_infer.onnx` 4.75MB, `ch_PP-OCRv4_rec_infer.onnx` 10.86MB, `ch_ppocr_mobile_v2.0_cls_infer.onnx` 585KB) — **16MB total**, loaded via `Path(__file__).resolve().parent`-relative code *inside the third-party package itself* (`rapidocr_onnxruntime/main.py:28-29`) | Must be bundled as `datas=[...]` preserving the exact package-relative directory layout (config.yaml beside main.py, `models/` subdirectory intact) — not auto-detected by static import analysis |
| **`opencv-python-headless` declared but `opencv-python` (full) actually installed** | `pip show opencv-python-headless` → not found; `opencv_python-5.0.0.93.dist-info` present instead (confirmed via its own METADATA) | **Pre-existing dependency-manifest drift**, not caused by this pass. Ships `cv2.pyd` (86.3MB) + a bundled `opencv_videoio_ffmpeg500_64.dll` (30.9MB) that a headless install would not need — the DLL is loaded by `cv2.pyd` at runtime (not a Python import), invisible to static freezer analysis, needs an explicit `binaries=[...]` entry regardless of which opencv variant is used |
| `numpy` 2.5.1 (transitive) | `.venv/Lib/site-packages/numpy.libs/` (21MB): `libscipy_openblas64_-*.dll`, `msvcp140-*.dll` | Native libs outside the normal package tree, same explicit-binaries concern |
| `pdfplumber`/`pdfminer.six` | No `.pyd`/`.dll` found | Pure Python, no freezing concern |
| Alembic assets | `backend/alembic.ini`, `backend/alembic/{env.py,script.py.mako,versions/*.py}` (4 real migrations: `81d6d64c51ba` baseline, `b4e89f123c5a`, `c7d8e9f1a2b3`, `4c99c2ff7376`) | Must be bundled as `datas=[...]`; see §D9 for the deeper path-resolution defect this alone doesn't fix |
| Document adapters (`kortex.engines.document.adapters.{dummy_adapter,macro_adapter}`) | Reachable **only** via `pkgutil.iter_modules` dynamic discovery (`document/loader.py:45-87,70,73`) — never statically imported anywhere (`adapters/__init__.py` is docstring-only) | Genuine hidden-import gap: needs an explicit `--collect-submodules=kortex.engines.document.adapters` (PyInstaller) or equivalent |
| Connector driver dynamic-loading (`connector/loader.py:61,154,177`) | Capability exists but **zero production call sites** found outside the loader/interface definitions — shipped drivers (`DummyConnectorDriver`, `HttpRestConnectorDriver`) are reached via ordinary static imports (`connector/drivers/__init__.py:5-6`, imported at `kernel_bootstrap.py:34`) | Not currently a hidden-import risk; flag as a "watch for future connector additions" item |
| `chromadb`/`ollama` (`ai` extra) | Installed in dev venv but **zero** `import chromadb`/`import ollama` anywhere in `backend/src` | Not required in the frozen build |
| Requirements-manifest drift (pre-existing, unrelated to freezing per se) | `requirements.txt` declares `pyyaml`, `python-dotenv` absent from `pyproject.toml`'s dependency list; `aiosqlite` floor differs (`>=0.20.0` vs `>=0.22.0`) — same drift already found and *not* fixed during Docker planning (reverted as out-of-scope there too) | Freeze from whichever manifest actually produces a working venv (`requirements.txt`, matching Docker's own precedent) — not a freezer blocker either way |
| Windows-specific code (beyond the already-known `db.py`/`_default_app_data_dir()` three-way branch) | `backup/engine.py:373-377` — a **second, slightly different** Windows/POSIX app-data-dir branch (no `darwin` case, unlike `db.py`'s three-way branch) — a minor, pre-existing inconsistency, not introduced here. `monitoring/collector.py:51,68-90,108` — genuine `ctypes.WinDLL("psapi.dll"/"kernel32.dll")` calls for Windows process-memory stats, guarded by `sys.platform` checks — loads OS-provided system DLLs already present on any Windows machine, freezer-neutral but worth exercising in a frozen-Windows test pass. `update/compatibility.py:121-135` — normalizes `sys.platform`/`platform.machine()` against an update manifest's `compatibility.platforms` list (pre-existing, unreachable per §D9). `kernel_bootstrap.py` itself has **no** platform-branch code (correcting a misattribution from an earlier research pass) | Informational; no action required by this plan |

---

## D6. PyInstaller vs. Nuitka — Evidence-Based Comparison

Both are evaluated against the concrete asset inventory in §D5, not generic popularity.

| Dimension | PyInstaller | Nuitka |
|---|---|---|
| ONNXRuntime/RapidOCR bundling | `pyinstaller-hooks-contrib` ships maintained, actively-used community hooks for `onnxruntime` specifically — the single highest-risk dependency in this stack | Materially less mature/documented support for this exact package combination; no repository or general-ecosystem evidence found suggesting a comparably battle-tested path |
| OpenCV bundling | Well-documented `opencv-python`/`-headless` hook precedent in `pyinstaller-hooks-contrib` | Handles it via its plugin system, but with less real-world precedent for this specific native-DLL-heavy package |
| `--onedir` vs. equivalent | `--onedir` produces a stable, extracted-once directory — the right mode here (see below) | `--standalone` mode is the analogous stable-directory output |
| Build complexity / CI feasibility | Simple `pyinstaller` CLI invocation, single spec file, runs on a plain Windows/macOS runner with just Python + pip | Requires a working C compiler toolchain on every build machine (MSVC on Windows, Xcode toolchain on macOS) — a heavier CI prerequisite for a green-field packaging setup |
| Reproducibility/debugging | Straightforward stack traces in frozen code (no C compilation step to obscure them) | Compiles Python to C then to a binary — can make crash diagnosis in a frozen build noticeably harder |
| Licensing | PyInstaller: GPL-with-linking-exception-equivalent (its own bootloader license permits proprietary use) | Nuitka core: Apache-2.0 (free); commercial add-ons exist but are not required here — **not a real differentiator for this decision** |
| Startup time | `--onedir` avoids the per-run extraction penalty `--onefile` has | Generally comparable once compiled; not the deciding factor given the onedir/standalone choice already dominates this axis |

**[PROPOSAL] Recommendation: PyInstaller, `--onedir` mode**, as the starting point for a proof-of-concept — not a final, uncontested decision (see below). `--onefile` is explicitly **not** recommended: its per-run extraction-to-a-temp-directory model is fundamentally incompatible with `applier.py`'s and `migrator.py`'s existing `Path(__file__)`-based assumptions (§D9) and would re-extract ~150MB+ of native binaries on every single launch, adding real startup latency on top of the already-measured ~19s worst-case readiness-polling window (§D3).

**[FACT]** Neither tool can be *safely* selected without a proof-of-concept — this is not a hedge invented for this plan, it is the repository's own pre-existing, ratified position: `ADR-0002:66` explicitly defers this exact choice *"pending a packaging spike."* **Minimum POC scope (to be run by an implementation pass, not performed here — no tracked repository file would need to change to run it)**:
1. Freeze a minimal script that imports `kortex.api.main` and starts `uvicorn` briefly, on a clean Windows VM/container with no Python installed.
2. Exercise one full `DocumentIntelligenceEngine` OCR call end-to-end inside the frozen build (proves onnxruntime+rapidocr+opencv native bundling actually works, not just imports cleanly).
3. Exercise one `alembic upgrade head` invocation against a fresh SQLite file from inside the frozen build, using a **corrected** path-resolution mechanism (§D9) — proves the alembic.ini/versions bundling and path-fix actually work together, not just that the files were copied.
4. Measure cold-start time and on-disk size of the resulting `--onedir` bundle.

If this POC reveals a hard blocker for PyInstaller specifically (not evidenced today), Nuitka is the documented fallback — this is a technical validation gate, not a values-based owner decision (§D17).

---

## D7. Tauri Bundle Architecture

**[PROPOSAL]** Configure `tauri.conf.json`'s `bundle.externalBin` to point at the frozen backend's `--onedir` output (Tauri's own mechanism for shipping a sidecar binary alongside the Rust binary, resolved via Tauri's documented `$RESOURCE`-relative or target-triple-suffixed naming convention — not invented here, this is Tauri's existing, standard sidecar feature, simply unused today since `externalBin` is currently absent from the config entirely). This requires a corresponding, **new** resolution path in `backend_process.rs` (replacing/supplementing `backend_source_dir()`) that locates the bundled sidecar relative to the running application's own install directory (e.g. via Tauri's `app.path().resource_dir()` API) instead of the compile-time-baked `CARGO_MANIFEST_DIR`. **This is a Rust source change belonging to the implementation phase, not performed in this planning pass.**

**[FACT]** The existing Tauri shell remains the sole process supervisor — no second supervisor is proposed. `sidecar.rs`'s restart/shutdown logic (§D3) is reused unchanged; only *what command it spawns and how that command's location is resolved* changes.

---

## D8. Windows Installer Architecture

**[FACT]** `bundle.targets` already includes `"msi"` and `"nsis"` — both are Windows-native toolchains (WiX Toolset for MSI, NSIS for the `.exe` installer) that Tauri's own `tauri build` auto-provisions **when run on an actual Windows host** (general Tauri-ecosystem knowledge, not itself documented in this repo — Tauri does not officially support cross-compiling a signed Windows installer bundle from Linux). Since `desktop-ci.yml`'s only Rust job runs `cargo check`/`clippy` on `ubuntu-latest` and never calls `tauri build` (§D3), **CI produces no MSI/NSIS artifact today regardless of `tauri.conf.json`'s configuration.**

**[PROPOSAL — install/uninstall/upgrade behavior, none of which exists today (§D3 confirms zero uninstall/upgrade logic anywhere in the repo)]**:
- Install directory: standard per-user or per-machine Program Files location (WiX/NSIS default) — application code, read-mostly.
- Persistent data: **must not** live under the install directory (§D10) — must resolve to `%APPDATA%\KORTEX\...`, already the correct, non-cwd-relative default for the SQLite database (`_default_app_data_dir()`, §D10) but **not** yet true for `KORTEX_STORAGE_DIR`/Backup's hardcoded `storage_data/...` constants (§D10's core finding).
- Upgrade: WiX's `UpgradeCode` (not yet set anywhere in `tauri.conf.json`) must be added and held stable across all future versions so Windows recognizes an in-place upgrade rather than a side-by-side install; the sidecar must be fully stopped (via the existing 30s graceful-shutdown path, §D3) before the installer replaces on-disk files.
- Uninstall: must remove the installed application files; must **not** silently delete `%APPDATA%\KORTEX\...` (the database, backups, keys-by-reference) without explicit user confirmation — no such confirmation UI exists today; this is new UI/installer-config work, not a code redesign.
- Stale-process handling: the existing crash-restart policy (max 3 attempts, §D3) already guards against one class of stale-process risk; an installer-level check for an already-running instance before allowing a new install/upgrade to proceed is new, not-yet-existing logic.
- Signing/UAC: see §D14.

---

## D9. Persistence / Data Boundary — the Central Desktop-Specific Finding

**[FACT — this is the single most consequential desktop-specific discovery]**: `_default_sqlite_url()`/`_default_app_data_dir()` (`db.py:64-111`) already resolve correctly (an OS-app-data path, explicitly **not** cwd-relative — its own doc comment states this design choice exists specifically to avoid the footgun described next). **Every other persistent-state path does not share this safety property**:

- `KORTEX_STORAGE_DIR` (`kernel_bootstrap.py:57-58,141-142`) defaults to the literal relative string `"kortex_api_storage"` if unset — resolved against **whatever cwd the process has**.
- `BackupEngine()` is constructed with zero arguments (`kernel_bootstrap.py:271`), so `BackupConfig.backup_directory` falls to the hardcoded relative constant `"storage_data/backups"` (`backup/constants.py:48`) — also cwd-relative, and **not connected to `KORTEX_STORAGE_DIR` by any mechanism** (Recovery/Update have the analogous `"storage_data/.recovery"`/`"storage_data/.update"` defaults, currently dormant per §D3/§D12).
- **For an installed desktop app, cwd is genuinely undetermined by any code today**: `backend_source_dir()` fails outright on an installed machine (§D3); the `KORTEX_BACKEND_COMMAND` fallback leaves `working_directory` as `None`, so the child simply inherits whatever cwd the OS assigns the Tauri host process — an OS/shortcut-configuration fact, not anything this codebase asserts or controls.
- **Concrete risk this creates**: if a Windows shortcut (or the OS) happens to launch the installed app with a cwd under `C:\Program Files\KORTEX Desktop\` (a plausible default for an MSI-installed GUI app), `StorageEngine`/`BackupEngine`'s plain `Path.mkdir(parents=True, exist_ok=True)` calls (confirmed unhardened — no ownership/permission check, `sandbox.py:31-32`, `file_store.py:74-76`, `backup/repository.py:47-49`) would attempt to create directories **inside a directory standard Windows users cannot write to without UAC elevation** — a real, evidence-backed installer-specific failure mode, not a hypothetical one.

**[CONTROL 2 — ONE authoritative persistent-data root, not scattered per-engine cwd fixes]**: the extension point is a single one: `backend_process.rs`'s sidecar-launch code (§D7) computes ONE absolute application-data directory (Tauri's `app.path().app_data_dir()` API, itself platform-correct — `%APPDATA%\KORTEX Desktop\` on Windows, `~/Library/Application Support/` on macOS, XDG on Linux, mirroring the exact same semantics `_default_app_data_dir()` already uses on the Python side, so the two independently-computed paths agree in *kind*, not just by accident), and sets exactly two things when spawning the sidecar: an explicit `working_directory` pointed at that directory, and `KORTEX_STORAGE_DIR=storage_data`. Every consumer (`StorageEngine`, `BackupEngine`, and Recovery/Update's dormant equivalents) resolves its own relative constant against that one fixed root — no engine's internal default changes, no second storage abstraction is created, and Docker/server behavior (Part 1, which sets the same env var against its own container `WORKDIR`) is untouched, since this is purely additive Rust-side configuration for the desktop launch path specifically. **Implemented this pass** — see §D25.

**What survives upgrade/uninstall**: formalized in §D26's explicit lifecycle table (Control 3 of the master prompt) — the app-data directory survives upgrade and reinstall unconditionally; uninstall behavior is an explicit, documented choice, never the installer framework's silent default.

**Keys — [CONTROL 4, fail-closed, implemented this pass]**: `secure_keys.rs` (full read, 207 lines) solves desktop key persistence via the OS-native keyring (`keyring` crate, service `"kortex-desktop"`, users `"backend-master-key"`/`"backend-signing-key"`). **Prior finding**: both the read path (`KeyringKeyStore::load`, `:44-46`) and the write path (`:48-56`) previously collapsed *any* error (keyring daemon absent, access denied) to silent regeneration, with no warning logged. **Corrected production semantics (§D25)**: the distinction the master prompt requires — *"existing persistent key expected + keyring unavailable = fail closed"* vs. *"no key has ever existed yet (first install) + keyring unavailable = a fresh key may be generated, since there is no prior identity to silently discard"* — is implemented by distinguishing "entry exists but is unreadable" (a real keyring-access failure, e.g. daemon absent or access denied — **fail closed**, surfaced to the user, sidecar not spawned with a silently-replaced identity) from "entry does not exist" (genuine first-run — generate and persist, exactly as before). This preserves the existing OS-native keyring architecture unchanged; no plaintext fallback, no new secret-management subsystem, no key ever stored in the install directory.

**Do not invent a new secret-management subsystem, and do not invent a plaintext key file** — none is proposed or implemented anywhere in this section.

---

## D10. Migration Boundary

**[FACT — proven via direct reading of the installed Alembic library's own source, not inferred]**: `backend/alembic.ini:3`'s `script_location = alembic` is a bare relative token with no `%(here)s` interpolation. `alembic/util/pyfiles.py:52-73`'s `coerce_resource_to_filename` only treats a value as an `importlib.resources` package reference if it contains a literal `":"` — since this value doesn't, it falls through to a bare, unanchored `pathlib.Path(fname_or_resource)` (`:73`). `alembic/script/base.py:134-193`'s `ScriptDirectory.from_config`/`_load_revisions` then walks that path via a genuine filesystem directory walk (`compat.path_walk`, `alembic/util/compat.py:50-66`) — resolved against **the process's current working directory at runtime**, never against `alembic.ini`'s own directory. This is the exact code path `UpdateMigrator.get_alembic_config()` (`migrator.py:51-54`) exercises, using a default `alembic.ini` path computed as `Path(__file__).resolve().parents[4]` (`migrator.py:35-45`) — correct only in this repo's editable/dev-source layout (confirmed present via `_editable_impl_kortex.pth` in the dev venv). **Under any non-editable install — a normal wheel install, let alone a PyInstaller/Nuitka frozen build — this computation no longer points at a directory containing `alembic.ini`, and `get_alembic_config()` raises `FileNotFoundError`.**

**[FACT]** No code path anywhere — Python or Rust, desktop or Docker — invokes `alembic upgrade head` automatically today. `Kernel.boot()`'s unconditional `create_all_tables()` (unchanged, not reopened) remains the only thing that runs on every boot; it cannot evolve an existing schema (only create missing tables), exactly as established in Part 1 §7 of this document for Docker.

**[CONTROL 3 — CWD-independent by construction, not by changing cwd first, implemented this pass, §D25]**: invoke `alembic upgrade head` explicitly, once, before the application begins serving, via a **new, small, Update-Engine-independent** module (not by reusing/reopening `UpdateMigrator`, which belongs to Update Engine and is not touched). The fix is *not* "cd to the right directory before running migrations" (the master prompt explicitly rules this out, correctly — a cwd-based fix would silently break the moment anything else changes cwd, and does nothing for a genuinely embedded/frozen resource layout). Instead, the new module computes **absolute** paths for both `alembic.ini` and the migration `script_location` — resolved from the frozen executable's own bundle location when frozen (PyInstaller's `sys._MEIPASS`/`sys.executable`-relative resource directory in a `--onedir` build), or from the package's own installed location in dev/editable mode (`importlib.resources`/`Path(__file__)`-relative, scoped to *this new module*, not reused from `migrator.py`'s broken assumption) — and passes those absolute paths directly into Alembic's `Config` object (`config.set_main_option("script_location", <absolute path>)`), which is respected regardless of process cwd (proven by the same Alembic source reading: an absolute `Path` given directly bypasses the bare-relative-string cwd-resolution defect entirely). **Distinguishing packaging migration assets from executing them, precisely**: bundling `alembic.ini`/`env.py`/`script.py.mako`/`versions/*.py` into the frozen build (§D5) is a packaging concern solved by the freezer's `datas=[...]` config; *invoking* `alembic upgrade head` correctly against those bundled files, from an absolute path, independent of cwd, is the separate, new, small piece of startup code this control requires.

**Fresh install**: empty app-data dir → migration creates the full schema from the baseline revision forward. **Upgrade**: existing DB at some prior revision → migration carries it forward; **no destructive downgrade is ever proposed or performed**, consistent with the existing forbidden-in-place-downgrade rule already established for Update Engine's own Alembic model (Part 1 §7/§8). **Migration failure**: must block application startup (fail loud, matching Docker's own entrypoint design) rather than silently falling through to `create_all()`'s weaker guarantee.

---

## D11. Update / Backup / Recovery Boundary

**[CONTROL 1 — explicit invariant]**:

```
Desktop installer distribution  !=  Tauri application updater  !=  KORTEX Update Engine
```

These are three different concepts, not interchangeable, and this milestone touches only the first:

- **A. Installer lifecycle** (this milestone): MSI/NSIS install, upgrade, reinstall, uninstall — implemented and tested independently of the other two (§D26).
- **B. Tauri application updater**: an optional, *not-yet-configured* mechanism for distributing a new signed desktop package. Its existence in Tauri and its mention in `ADR-0002:58` does not make configuring it mandatory for this milestone — it is documented as a boundary (below), not implemented, since nothing in the repository or governance requires it to be built now. No `updater` section is added to `tauri.conf.json` by this pass.
- **C. KORTEX Update Engine** (`kortex.engines.update`): unmodified, not reopened, not made responsible for replacing an installed desktop executable, and not wired into any installer lifecycle step.

**[FACT/ARCH-REQ — the three-way distinction, cleanly resolved by direct evidence]**:

| Component | Owner | Evidence |
|---|---|---|
| Initial installation/distribution (MSI/NSIS/DMG) | **The installer** (this milestone) | §D8/§D13 |
| Desktop-shell version upgrades (a new signed app release replacing the old one) | **Tauri's own updater plugin** — already ratified architecture, not configured by this pass | `ADR-0002:58`: *"desktop updates ship as a single Ed25519-signed unit via Tauri's updater plugin, reusing the existing platform signature scheme rather than introducing a second one."* No `updater` section exists in `tauri.conf.json` — its boundary is documented (Control 1 above) but configuring it is explicitly **not** performed by this milestone: nothing in the repository or governance requires it now, and doing so would also require the same signing infrastructure gated by OD-DI-2. Installer-level upgrade (replacing an installed MSI/NSIS package in place, §D8/§D26) is implemented independently and does not depend on this mechanism at all |
| Backend/application-data-level update semantics (in-place code/schema update *without* replacing the whole desktop-shell package) | **KORTEX's own Update Engine** (`kortex.engines.update`) — unmodified, not reopened | Part 1 §8/§9 of this document: Update Engine's `kortex.update.apply` capability is unreachable today (not wired into `kernel_bootstrap.py`, OD-1, carried forward from Docker, §D12) and its file-swap default target (`applier.py`) is independently proven unsafe for any frozen-executable layout (§D1/§D5) |
| Backup | **Backup Engine**, unmodified | Reachable today; its directory resolution shares the same cwd-dependency risk as Storage (§D9), resolved by the same env/working-directory fix |
| Destructive restore/recovery | **Recovery Engine**, unmodified, currently unreachable (OD-1) | Not reopened |

**Filesystem swap ≠ runtime activation** (Update Engine's own established invariant, Part 1 §8, unchanged): this plan does not alter or depend on that invariant — it simply confirms Update Engine is not the mechanism by which the *desktop shell itself* gets upgraded (that's Tauri's updater plugin), narrowing exactly where the invariant does and does not apply.

**This plan does not add a second updater, a second backup subsystem, or a second recovery subsystem.**

---

## D12. Bootstrap / Kernel Boundary

**[FACT]** `kernel_bootstrap.py` registers the same 16 engines/modules for desktop as for every other topology (StorageEngine, SecurityEngine, ConnectorEngine, WorkflowEngine, MarketplaceEngine, AI Orchestration, DocumentEngine, KnowledgeEngine, Finance/HR/Operations modules, DocumentIntelligenceEngine, LicenseEngine, SentinelEngine, MonitoringEngine, BackupEngine) — there is no desktop-specific bootstrap branch anywhere in this file (re-confirmed: zero `sys.platform`/`os.name`/desktop-specific conditionals found in `kernel_bootstrap.py` itself). Recovery/Update remain absent, confirmed fresh at current HEAD (`grep -n "Recovery\|Update" kernel_bootstrap.py` → zero matches, re-run this pass).

**[FACT — new evidence this pass, directly relevant to OD-1]**: resolving OD-1 (wiring Recovery/Update into the kernel bootstrap) would immediately expose the two independent, code-proven path-resolution defects found in §D5/§D9/§D10 (`applier.py`'s frozen-unsafe `target_root`, `migrator.py`'s frozen-unsafe `alembic.ini` path) on **every** topology, not just Docker or Desktop — because both defects are properties of Update Engine's own internal code, not of any particular deployment target. This is additional evidence for why OD-1 should not be casually resolved as a side effect of either the Docker or Desktop Installer milestones; it is not a new owner decision this plan introduces, it is stronger evidence for the *existing* one.

**Per explicit instruction, OD-1 is not resolved here, and `kernel_bootstrap.py` is not modified by this plan.**

---

## D13. CI/CD Strategy (planning only — no workflow file modified)

**[FACT]** Both existing CI jobs run exclusively on `ubuntu-latest`; zero Windows/macOS runner exists anywhere in this repository's CI today (§D3). `tauri build` is invoked nowhere — not in CI, not in any `package.json` script, not in any shell/PowerShell script, not in a Makefile (none exists).

**[PROPOSAL — minimum CI additions the eventual implementation must make, not built here]**:
1. A new job (or new workflow) on `windows-latest`: set up Node/pnpm (mirroring `desktop-ci.yml`'s existing `frontend` job setup), set up Rust (mirroring the existing `rust` job), freeze the backend (PyInstaller `--onedir`, §D6), run `tauri build --target msi,nsis` (or the equivalent Tauri v2 CLI invocation), producing unsigned `.msi`/`.exe` artifacts.
2. Artifact integrity checks: confirm the produced installer actually contains the frozen sidecar and the expected icon/version metadata (a lightweight `docker image inspect`-style sanity check, not a full test suite).
3. An installer smoke test: install silently (`msiexec /quiet` or NSIS's own silent-install flag) on the same Windows runner, launch the installed app, poll `/health` (mirroring Docker's own CI smoke-test pattern from Part 1 §18/§19), confirm clean shutdown, then uninstall.
4. A macOS job (`macos-latest`) **only if** OD-DI-3 (§D18) is resolved in favor of producing `.dmg` artifacts as part of this milestone — otherwise deferred.
5. Code signing steps, gated entirely on OD-DI-2 (§D18) — unsigned builds are the only thing CI can produce until signing credentials exist.
6. No release/tag-triggered publish workflow exists today and none is proposed as *required* by this milestone — artifact upload (as a GitHub Actions build artifact, not a public release) is sufficient for CI validation purposes; public release-channel publishing is a separate, not-yet-authorized concern.

---

## D14. Security Boundary (installer-specific only)

**[FACT]** Today's webview capability surface is already minimal and correct (`capabilities/default.json`, §D3) — no shell/filesystem/http permissions granted to the webview. This plan does not widen it.

**[PROPOSAL — installer-specific security items, narrowly scoped]**:
- **Bundled trust anchors**: License Engine's compiled Ed25519 public key (`license/crypto.py`) and Update Engine's `COMPILED_VENDOR_UPDATE_KEYS` (`update/crypto.py:28-31`) are public trust anchors, not private secrets — they are already baked into the Python source and will be frozen alongside it automatically; **no change to their cryptographic architecture is proposed** (§D15).
- **Sidecar executable trust**: once frozen and bundled via `externalBin`, the sidecar executable's own integrity is protected only by the installer's own code-signing (§D14 continued below) — Tauri does not independently re-verify sidecar binary integrity at runtime beyond what the OS's own Authenticode/Gatekeeper checks provide at launch time for a signed binary (unsigned builds have no such protection, a direct consequence of OD-DI-2).
- **Writable directories**: per §D9's fix, only the resolved app-data directory should be writable by the running application; the install directory should remain read-mostly post-install.
- **DLL/shared-library loading**: the native DLLs inventoried in §D5 (onnxruntime, opencv, numpy's openblas/msvcp) are loaded from within the frozen bundle's own directory — no unqualified/PATH-relative DLL search order risk is introduced as long as the freezer's default same-directory-first loading behavior is preserved (standard for both PyInstaller `--onedir` and Nuitka `--standalone`).
- **Temporary installer files / logs / crash dumps**: none of this exists or is configured today (§D3's confirmed absence of any uninstall/upgrade logic) — an implementation-phase deliverable, not resolved here.
- **This is not a whole-project security audit** — Sentinel/Monitoring/Backup/Recovery/Update/Security Engine's own internals are not re-reviewed here.

---

## D15. Licensing / Trust Anchors

**[FACT, re-confirmed]** License Engine's trusted root key (compiled Ed25519 public key in `license/crypto.py`) and Update Engine's `COMPILED_VENDOR_UPDATE_KEYS` (`update/crypto.py:28-31`) are both baked-into-source public trust anchors, not runtime secrets — `license/config.py` reads **zero** environment variables (confirmed during Docker planning, re-applicable unchanged here). Freezing the backend carries these into the bundled executable automatically as ordinary compiled Python bytecode/data — **no separate packaging step, no plaintext extraction, and no change to their cryptographic architecture is required or proposed.**

---

## D16. Signing Strategy

**[FACT — exhaustive search, zero infrastructure found]**: grep across the whole repository for `signtool|certificateThumbprint|signingIdentity|notarytool|codesign|APPLE_ID|APPLE_TEAM_ID|WINDOWS_CERTIFICATE|TAURI_SIGNING` returns exactly one hit — the reconciliation document's own prose describing the *absence* of signing config (`PRODUCTION_HARDENING_RECONCILIATION.md:262`). Grep for `secrets\.` across every `.github/workflows/*.yml` returns **zero matches** — no GitHub Actions secret of any kind is referenced anywhere in CI today.

**Windows (Authenticode)**: requires a code-signing certificate (standard or EV) and a timestamping authority — **technically possible, not configured, credential-dependent, not CI-ready, not production-release-ready.**

**macOS (notarization)**: requires an active Apple Developer Program membership, a Developer ID Application signing identity, and `notarytool` submission — **technically possible, not configured, credential-dependent, not CI-ready, not production-release-ready.**

**[OWNER DECISION — OD-DI-2, see §D18]**: no certificates are invented, no fake signing infrastructure is created, and no credentials are exposed by this plan.

---

## D17. Fresh-Machine Validation

**[PROPOSAL — acceptance matrix, CI-executable items marked, real-machine-only items marked]**:

| Check | CI-executable? |
|---|---|
| Fresh Windows machine, no Python, no Node, no repo checkout | Yes — `windows-latest` runner approximates this (Python/Node are pre-installed on the runner image itself, but not required by the *installed application*, which is the property under test) |
| Install MSI / install NSIS | Yes (silent install flags) |
| First launch → backend startup → `/health` ready | Yes (mirrors Docker's own CI smoke test) |
| UI operation (basic navigation, login/bootstrap) | Partially — a full E2E UI test is a larger, separate investment; a minimal smoke check (app window opens, `/health` reachable) is CI-feasible now |
| Key persistence across restart (OS keyring) | **Real-machine only** — GitHub-hosted Windows runners' keyring/DPAPI behavior in a CI sandbox is not equivalent to a real user session; do not claim CI proves this |
| Database creation + migration on fresh install | Yes, once §D10's migration mechanism is built |
| Application restart, state persistence | Yes |
| Graceful shutdown | Yes (mirrors Docker's stop/health-check pattern) |
| Backend crash/restart behavior (the existing 3-attempt policy) | Yes — can be triggered deterministically in CI |
| Upgrade over previous version, migration during upgrade | Yes, once versioned installer artifacts exist to upgrade *from* |
| Uninstall, user-data preservation/removal behavior | Partially — silent-uninstall CI check is feasible; the *choice dialog* (if built) needs real-machine/manual verification |
| macOS equivalents (DMG install, Gatekeeper behavior, notarization) | **Real macOS runner only** — gated on OD-DI-3 |

---

## D18. Owner Decisions

Kept minimal, per instruction — genuine ambiguities only, not implementation details dressed up as decisions.

| Decision | Status | Evidence | Recommendation | Blocking? |
|---|---|---|---|---|
| **OD-DI-2**: Code-signing credential acquisition (Windows Authenticode certificate; Apple Developer Program enrollment + notarization identity) | **OWNER DECISION REQUIRED** | §D16 — zero signing infrastructure/credentials exist anywhere in the repo; this is an ops/procurement decision (budget, EV-vs-standard cert, Apple enrollment), not something repository evidence or an implementation agent can resolve. The reconciliation document's own §7 "Parallel Work" already independently classifies this as *"an ops/procurement task, not code"* that "can proceed independently of everything else." | Acquire credentials (owner/ops action, outside this codebase); until then, ship unsigned CI-built installers with known OS-warning limitations disclosed | **Blocks production-grade release**, does **not** block building/testing the packaging pipeline itself throughout implementation |
| **OD-DI-3**: Is `.dmg` production required as part of *this* milestone, or may it be established-but-deferred? | **OWNER DECISION REQUIRED** | §D3 — `phase3_desktop_architecture.md`/ADR-0002 both say macOS polish is "deferred to Phase 7," but **Phase 7 is the phase this exact milestone belongs to** — that language does not actually resolve the question, it only relocates it here. The roadmap bullet itself names `.dmg` explicitly (`.kortex/roadmap.md:70`). CI has zero macOS runner today. | Windows-first, fully validated (build + sign-when-available + CI smoke test); establish the cross-platform packaging *architecture* (shared freeze step, shared Tauri bundle config) so macOS needs only a `macos-latest` CI job + a build machine to produce `.dmg`, not a redesign — but **explicitly flag this as a narrowing of the roadmap bullet's literal text**, not a silent downgrade | **Does not block** implementing/validating the Windows path; **does block** claiming the roadmap bullet is *fully* satisfied without an explicit owner acknowledgment of the narrowing |

**Not owner decisions (resolved by repository evidence / reclassified as implementation details, matching the Docker plan's own precedent of not inflating technical questions into owner decisions)**:
- **Freezer choice (PyInstaller vs. Nuitka)**: PyInstaller recommended (§D6), gated on a defined, planned-not-performed POC — a technical validation gate, not a values-based tradeoff.
- **Whether `alembic upgrade head` must run explicitly before serving**: resolved (yes — `create_all()` cannot evolve schema, §D10); *how* (the new freeze-safe invocation mechanism) is an implementation-phase deliverable.
- **Storage-root/cwd fix for desktop** (§D9): resolved via the same "Resolution A" pattern already accepted for Docker — an explicit `working_directory` + `KORTEX_STORAGE_DIR` set by `backend_process.rs`'s new sidecar-launch code, zero engine-internal change.
- **What survives upgrade/uninstall** (§D9/§D8): a UI/config design detail to build, not a repository ambiguity.

**OD-1 (Recovery/Update kernel-bootstrap wiring, inherited from Docker planning) is explicitly NOT resolved, NOT reopened, and NOT touched by this pass** — carried forward unchanged, now with two additional pieces of evidence (§D5/§D9/§D10) reinforcing why it should stay that way pending a dedicated fix to Update Engine's own frozen-build-unsafe path resolution.

---

## D19. Implementation Phases

Derived from repository evidence, not a generic template.

```
Phase A — Backend freeze proof-of-concept
  Objective: prove PyInstaller --onedir (or Nuitka, if the POC forces a
             pivot) can produce a working, Python-free backend executable.
  Files likely to change: new spec/build files under installer/ (currently
             an empty placeholder directory, README-only — matches its
             already-intended purpose per its own description).
  Tests: the 4-point POC scope in §D6.
  Acceptance: frozen build boots, serves /health, completes one OCR call,
             completes one alembic upgrade head call, on a clean Windows VM.
  Dependencies: none.
  Non-goals: Tauri integration, signing, installer packaging (later phases).

Phase B — Tauri sidecar/resource integration
  Objective: wire the frozen backend into tauri.conf.json's externalBin;
             replace backend_source_dir()'s compile-time-baked resolution
             with an install-relative one (e.g. Tauri's resource_dir() API).
  Files likely to change: apps/desktop/src-tauri/tauri.conf.json,
             backend_process.rs, possibly sidecar.rs.
  Tests: sidecar spawns correctly from a --onedir bundle placed in a
             realistic installed-app directory layout (not the dev checkout).
  Acceptance: existing readiness-polling/restart/shutdown behavior (§D3)
             continues to work unchanged, now against the frozen binary.
  Dependencies: Phase A.
  Non-goals: no change to sidecar.rs's restart/shutdown policy itself.

Phase C — Persistent-data / install boundary
  Objective: implement §D9's fix (explicit working_directory +
             KORTEX_STORAGE_DIR, set by backend_process.rs).
  Files likely to change: backend_process.rs.
  Tests: fresh install → Storage/Backup both resolve under the same
             app-data root; no write attempted under Program Files.
  Acceptance: no PermissionError under a standard (non-admin) user account.
  Dependencies: Phase B.
  Non-goals: no change to StorageEngine/BackupEngine internals.

Phase D — Migration/startup integration
  Objective: implement §D10's new, Update-Engine-independent
             alembic upgrade head invocation with freeze-safe path
             resolution.
  Files likely to change: a new small startup module (exact location TBD
             by the implementation pass — not kernel_bootstrap.py's
             engine-registration logic itself, per the constraint that
             this pass must not modify that file, though a future
             implementation pass may need to touch it narrowly).
  Tests: fresh install migrates cleanly; upgrade-over-existing-DB migrates
             cleanly; a deliberately-failed migration blocks startup.
  Acceptance: matches Docker's own migration acceptance bar (Part 1 §16).
  Dependencies: Phase A (bundled alembic assets), Phase C (correct cwd).
  Non-goals: no new migration framework, no schema changes.

Phase E — Windows MSI/NSIS packaging
  Objective: produce actual signed-when-available .msi/.exe artifacts via
             tauri build on a Windows host.
  Files likely to change: tauri.conf.json (windows.wix/windows.nsis
             blocks, upgradeCode), CI workflow (new Windows job).
  Tests: install/upgrade/uninstall on a clean Windows VM.
  Acceptance: §D17's Windows rows.
  Dependencies: Phases A-D.
  Non-goals: macOS artifact production (Phase not scheduled here pending
             OD-DI-3).

Phase F — Installer lifecycle / upgrade behavior
  Objective: implement upgrade-preserves-data-directory behavior, optional
             user-facing uninstall data-removal choice, stale-process
             detection before install/upgrade.
  Files likely to change: tauri.conf.json, possibly new installer
             hook scripts.
  Tests: §D17's upgrade/uninstall rows.
  Acceptance: data survives upgrade by default; uninstall behavior is
             explicit and documented, not silently destructive.
  Dependencies: Phase E.

Phase G — CI artifact build
  Objective: implement §D13's proposed Windows CI job (and macOS job only
             if OD-DI-3 authorizes it).
  Files likely to change: .github/workflows/desktop-ci.yml (or a new
             workflow file).
  Tests: CI produces an installable artifact; smoke test passes in CI.
  Dependencies: Phase E.

Phase H — Signing/release integration
  Objective: wire signing steps into the CI build, gated entirely on
             OD-DI-2 being resolved (credentials acquired).
  Dependencies: OD-DI-2 resolution (external to this codebase).
  Non-goals: building a public release/distribution channel — CI-produced
             build artifacts are sufficient for this milestone's own scope.

Phase I — Fresh-machine validation
  Objective: run the full §D17 matrix on real (non-CI) hardware for the
             items marked "real-machine only."
  Dependencies: Phase G (or H, if signed builds are required for this
             validation pass).

Phase J — Formal reconciliation
  Objective: update docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md
             §5.8 to DONE (or IMPLEMENTED — AWAITING REVIEW, per that
             document's own governance rule that an implementing pass may
             not self-declare DONE), following the exact precedent Docker's
             own closure (§5.7 of that document) established.
  Dependencies: all prior phases; explicit owner review per that
             document's §0 governance rule.
```

---

## D20. Test / Acceptance Matrix

| Area | Existing tests? | To add | CI-only vs. real-machine |
|---|---|---|---|
| Backend freeze | None | POC scripts (§D6), not full test suite yet | Real-machine (Windows VM) for the POC itself; CI once Phase G exists |
| Backend startup (frozen) | None (today's tests exercise the dev-mode `uvicorn` command only) | Frozen-build smoke test | CI (Phase G) |
| Sidecar discovery | `sidecar.rs`'s existing unit tests (e.g. `sidecar.rs:363-367,424-425` restart-policy assertions) — unaffected, not modified | New test for install-relative resolution (Phase B) | CI |
| Tauri startup / IPC | Existing `capabilities/default.json`-scoped behavior — unaffected | Frozen-sidecar IPC smoke test | CI |
| Key persistence | None exercising real OS keyring failure (confirmed, §D9) — `MemoryKeyStore` test double never fails | A real-machine keyring-denial test is the only way to actually exercise this path | Real-machine only |
| Database initialization / migrations | `test_alembic_migrations.py` (dev-mode only, unaffected) | Frozen-build migration test (Phase D) | CI once Phase D exists |
| Upgrade | None | New (Phase F) | Real-machine + CI |
| Restart / crash recovery | `sidecar.rs`'s existing restart-policy tests — unaffected | Frozen-build crash-restart smoke test | CI |
| Uninstall | None | New (Phase F) | Real-machine (silent-uninstall CI check feasible, choice-dialog verification is not) |
| Installer artifact integrity | None | New (Phase G) | CI |
| Windows MSI / NSIS | None | New (Phase E/G) | CI (build) + real-machine (full acceptance) |
| macOS DMG | None | New, gated on OD-DI-3 | Real macOS runner only |
| Signing | None (nothing to sign yet) | New, gated on OD-DI-2 | CI once credentials exist |
| Clean-machine validation | None | New (§D17, Phase I) | Real-machine for the rows marked so in §D17 |

---

## D21. Explicit Non-Goals

Kubernetes, cloud deployment, Docker redesign (Part 1 remains untouched and unreopened), new backend engines, business-module changes, AI changes, database redesign, a new secret-management subsystem (the existing OS-keyring mechanism is authoritative, §D9), a new updater (Tauri's own updater plugin is the ratified mechanism, §D11 — not a new one this plan invents), a new backup subsystem, a new recovery subsystem, a new process supervisor (the existing Tauri shell/`sidecar.rs` remains sole owner), any installer framework unrelated to Tauri, Marketplace publishing, a cloud distribution platform, telemetry redesign. Also explicitly not in this pass: any Tauri/Rust/TypeScript/Python source change, any CI workflow change, any migration, any modification to Sentinel/Monitoring/Backup/Recovery/Update/License Engine internals, and resolution of OD-1.

---

## D22. Risk Register (installer-specific, new this pass)

| # | Risk | Evidence | Why it matters |
|---|---|---|---|
| 1 | Desktop app cannot spawn a backend at all on an installed machine today | `backend_source_dir()`'s compile-time-baked `CARGO_MANIFEST_DIR` (§D3) | This is not a hardening gap, it is a hard blocker — the single reason this milestone must exist |
| 2 | `UpdateApplier`'s frozen-build-unsafe `target_root` | `applier.py:41-46` (§D5/§D12) | Dormant only because Update Engine is unreachable; must not be silently activated by resolving OD-1 without first fixing this |
| 3 | `UpdateMigrator`'s frozen-build-unsafe `alembic.ini` path, and Alembic's own cwd-relative `script_location` resolution | `migrator.py:35-45`, `alembic/util/pyfiles.py:52-73`, `alembic/script/base.py:134-193` (§D10/§D12) | Same dormancy caveat; independently proves migrations need their own, non-Update-Engine invocation mechanism for desktop |
| 4 | Storage-root cwd-dependency could attempt writes under `Program Files` | §D9 | Concrete `PermissionError` risk on a standard (non-admin) Windows account, not hypothetical |
| 5 | Silent OS-keyring failure with no logged warning | `secure_keys.rs:44-56` (§D9) | Undiagnosable session/secret churn on a machine with an unavailable/denied keyring, unlike `kernel_bootstrap.py`'s own (logged) equivalent fallback |
| 6 | `opencv-python` (non-headless) installed instead of the declared `-headless` variant | Confirmed via `pip show`/site-packages inspection (§D5) | ~117MB of unnecessary native binary weight in every frozen build unless corrected |
| 7 | Zero Windows/macOS CI runner exists | `desktop-ci.yml` (§D3/§D13) | Nothing about installer correctness can be validated by today's CI at all |
| 8 | Zero signing infrastructure | §D16 | Unsigned installers trigger significant OS warnings (SmartScreen/Gatekeeper), inappropriate for an unqualified "production" release without owner acknowledgment |

---

## D23. Reconciliation Document Status

**[FACT]** Per explicit instruction, `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`'s existing §5.8 "Desktop Installers — PENDING" entry is **not modified by this pass** — it is not marked `DONE`, not marked `IMPLEMENTED`, and no planning-state update is made to it, since this is a discovery/planning pass only and the document's own governance rule (§0) reserves status advancement for an actual implementation pass followed by formal owner review.

---

## D24. Final Readiness Verdict

*(Originally written as the pre-implementation planning verdict; superseded by §D25's implementation record. Preserved below for the record, with the actual post-implementation status given first.)*

```
================================================================================
DESKTOP INSTALLERS (WINDOWS) — IMPLEMENTED, AWAITING OWNER REVIEW
MACOS — NOT ATTEMPTED (OD-DI-3 unresolved)
SIGNING — NOT ATTEMPTED (OD-DI-2, deferred by design)
================================================================================
```

Per this document's own governance convention (established by Part 1's reconciliation precedent): an implementation pass may verify and report, but formal `DONE` status requires explicit owner review — not self-declared here.

**Original planning verdict** (kept for record — its reasoning held up: neither OD-DI-2 nor OD-DI-3 blocked Windows implementation, exactly as predicted):

```
================================================================================
DESKTOP INSTALLERS — READY FOR IMPLEMENTATION WITH OWNER DECISION(S)
================================================================================
```

**Why "with owner decisions," not a plain READY**: OD-DI-2 (signing credentials) and OD-DI-3 (macOS scope) are genuine, evidence-confirmed ambiguities that repository evidence alone cannot resolve — they are procurement/ops and roadmap-interpretation questions respectively, not technical unknowns. **Neither blocks starting implementation**: Phases A-D and most of E/F/G (§D19) can proceed entirely independently of both — signing is only needed at Phase H, and the macOS question only determines whether a macOS-specific job is added to Phase G, not whether Windows work can proceed. OD-1 (Recovery/Update kernel-bootstrap wiring) remains explicitly un-resolved and un-reopened, carried forward from Docker planning with new, reinforcing evidence (§D12) — this too does not block Desktop Installer implementation, since neither Update Engine's capabilities nor its internal path-resolution defects are exercised by anything this plan proposes.

**Why not BLOCKED**: every one of the 20 quality-gate questions (§D-QG below) has either a direct repository-evidenced answer or an explicit, narrowly-scoped, non-blocking owner decision — none represents a missing capability that prevents an implementation agent from knowing what to build next.

**Quality-gate answers, for direct reference** (§D-QG):
1. Frozen Python backend executable (no interpreter required on the user's machine) — §D5/§D6.
2. No — that is precisely the point of freezing.
3. PyInstaller `--onedir` build, produced by an implementation-phase POC (§D6), bundled via Tauri's `externalBin`.
4. Via a new, install-relative resolution in `backend_process.rs` (Tauri resource-dir API), replacing today's compile-time-baked path — §D7.
5. cryptography/argon2-cffi native extensions, onnxruntime + rapidocr models (16MB, exact layout preserved), opencv + its native DLLs (headless-vs-full drift noted), numpy's native libs, Alembic's `alembic.ini`/`env.py`/`script.py.mako`/`versions/*` — §D5.
6. An OS-app-data directory, explicitly set via a fixed `working_directory` + `KORTEX_STORAGE_DIR` env var from `backend_process.rs` — §D9.
7. The existing OS-native keyring (`secure_keys.rs`), unchanged, with one disclosed silent-failure risk (§D9/§D22).
8. A new, Update-Engine-independent, freeze-safe `alembic upgrade head` invocation before serving — §D10.
9. In-place file replacement, sidecar fully stopped first via the existing 30s graceful-shutdown path, app-data directory preserved — §D8/§D9.
10. Tauri's own updater plugin (already ratified, not yet configured) for desktop-shell version upgrades; KORTEX's Update Engine remains a separate, unmodified, currently-unreachable backend-level mechanism — §D11.
11. Application files removed; app-data directory preserved by default pending explicit user choice (not yet built) — §D8/§D9.
12. `tauri build` on a Windows host, once `externalBin`/signing config exists — §D8/§D13.
13. `tauri build` on a macOS host — gated on OD-DI-3.
14. CI can build Windows only today (zero macOS runner exists) — §D13.
15. CI can test only what Phase G implements; nothing today — §D13/§D17.
16. Authenticode (Windows) / Developer ID + notarization (macOS) — both entirely unconfigured, gated on OD-DI-2 — §D16.
17. §D17's matrix, CI-feasible items vs. real-machine-only items explicitly distinguished.
18. OD-DI-2 (signing credentials), OD-DI-3 (macOS scope) — §D18. OD-1 carried forward, not resolved.
19. §D21.
20. §D19's ten ordered phases.

---

## D25. Implementation Record (Phases A-G, verified this pass)

**[FACT — everything below is measured evidence from real builds/installs/launches on this Windows machine (Rust 1.98, Tauri CLI 2.11.4, Python 3.12.9), not projected.]**

**Files added**: `backend/src/kortex/api/desktop_entrypoint.py` (new, CWD-independent migration + production entrypoint), `installer/pyinstaller/kortex_backend.spec` (new). **Files modified**: `apps/desktop/src-tauri/src/backend_process.rs`, `apps/desktop/src-tauri/src/secure_keys.rs`, `apps/desktop/src-tauri/tauri.conf.json`, `.github/workflows/desktop-ci.yml`. **Not touched**: `kernel_bootstrap.py`, any accepted engine (Sentinel/Monitoring/Backup/Recovery/Update/License), any migration file, `backend-ci.yml`, Docker files.

**Phase A (backend freeze POC) — PASS.** PyInstaller `--onedir` build succeeded on the first real attempt at the dependency-collection level (the `pyinstaller-hooks-contrib` package already ships working hooks for `onnxruntime`/`cv2`, confirming §D6's recommendation). Two genuine defects were found and fixed only by actually running the frozen build, not by static review:
1. **`aiosqlite` hidden-import gap**: SQLAlchemy loads its SQLite DBAPI driver through its own dialect-plugin registry, invisible to PyInstaller's static analysis — `ModuleNotFoundError: No module named 'aiosqlite'` at first migration attempt. Fixed via an explicit `hiddenimports` entry.
2. **PyInstaller 6.x's `--onedir` layout**: data files land under a `_internal/` subdirectory, not directly beside the `.exe` — `resource_root()`'s original assumption was wrong. Fixed by resolving through `sys._MEIPASS` (PyInstaller's own version-stable pointer) instead of re-deriving the layout.

Real, end-to-end evidence gathered: `alembic upgrade head` ran successfully inside the frozen build from a scratch database; the same frozen executable, launched from `C:\Windows\Temp` (an arbitrary directory with no relationship to the source checkout), served `/health` within ~1s; a dedicated `--selftest-ocr` diagnostic mode ran `RapidOCR`/ONNXRuntime inference against a synthetic image and correctly recognized the text `"KORTEX OCR TEST"` (95.1% confidence) using the bundled `.onnx` models — proving the native OCR path works inside the frozen build, not merely that it imports.

**Phase B (Tauri sidecar/resource integration) — PASS.** `tauri.conf.json`'s `bundle.resources` bundles the frozen `dist/kortex-backend/` directory; `backend_process.rs` now resolves it at runtime via `app.path().resource_dir()` (production builds only, gated on `cfg!(debug_assertions)` — dev mode is untouched and still uses the original `python -m uvicorn` path). One genuine runtime defect was found only by launching the actual installed executable (not caught by `cargo check`/`cargo test`/`cargo clippy`, all of which passed throughout): `tokio::spawn(monitor_loop(app))`, called from Tauri's synchronous `.setup()` closure, panicked with *"there is no reactor running, must be called from the context of a Tokio 1.x runtime"* — a pre-existing defect in already-accepted M7.1 code, never previously exercised by a real compiled release build. Fixed via `tauri::async_runtime::spawn`, Tauri's own documented runtime-agnostic wrapper for exactly this situation.

**Phase C (persistent-data/install boundary, Control 2) — PASS.** `resolve_app_data_dir()` (Tauri's `app_data_dir()` API) + an explicit `working_directory` + `KORTEX_STORAGE_DIR=storage_data` unify `StorageEngine`/`BackupEngine` under one root. A second real gap was found during installed-artifact testing: the database independently resolved via the Python side's own `_default_app_data_dir()` (hardcoded `"KORTEX"` folder name) — a *different* directory than Tauri's identifier-based `app_data_dir()`. Fixed by also setting `KORTEX_DATABASE_URL` explicitly, pointing at a file under the same resolved root. Verified: a real installed launch created `kortex_local.db`, `storage_data/`, and `storage_data/backups/` all under the single directory `%APPDATA%\com.kortex.desktop\storage_data\`; zero files were written anywhere under the install directory.

**Phase D (CWD-independent migration, Control 3) — PASS.** `desktop_entrypoint.py` resolves `alembic.ini`/`script_location` as absolute paths (frozen: via `sys._MEIPASS`; dev: via a fresh, module-local depth computation, not reused from Update Engine's own frozen-unsafe equivalent). Verified cwd-independent by running migrations from `C:\Windows\Temp` against a scratch database. **A third genuine, previously-undiscovered defect** was found via this milestone's own real-world testing: an actual pre-existing database on this machine (created by `Kernel.boot()`'s `create_all_tables()` before this milestone's migration invocation existed) had all of KORTEX's tables but an *empty* `alembic_version` table (zero rows — not merely absent) from an unrelated prior interruption. `alembic upgrade head` against it failed with `table ai_agent_tasks already exists`. Fixed by adding `stamp_revision_for_preexisting_database()`, which detects a database with real tables but no *recorded* revision (table-exists-but-empty is treated identically to table-absent) and stamps it at the most advanced revision its actual tables correspond to (a four-way staircase check across the four known migrations' marker tables), before `upgrade head` runs. Verified against both the real pre-existing database (correctly stamped at `4c99c2ff7376`, then a clean no-op upgrade) and a genuine fresh/empty database (unaffected, full migration chain still runs from baseline forward) — no regression in the already-proven fresh-install path.

**Control 4 (keyring fail-closed) — PASS.** `secure_keys.rs`'s `KeyStore::load` now returns `KeyLoadResult::{Found, ConfirmedAbsent, Unreadable}` instead of a bare `Option`, distinguishing "queried and confirmed no entry" from "could not be queried at all." `load_or_generate_hex_key` fails closed (returns `Err`, never calls `store()`) on `Unreadable` — an ambiguous case is never treated as safe to generate a replacement identity. This propagates through the existing `Err` → `spawn_and_monitor` → `SidecarSupervision::Disabled` → `BackendUnavailableScreen.tsx` path unchanged — no new UI was built. Two new unit tests (`first_install_with_confirmed_absent_entry_generates_a_key`, `unreadable_keyring_fails_closed_instead_of_silently_replacing_identity`, the latter using a `KeyStore` test double whose `store()` panics if ever called) cover both the first-install and existing-install cases the master prompt required distinguishing.

**Phase E (Windows MSI/NSIS packaging) — PASS, real artifacts produced.** `pnpm tauri build` on this machine produced `KORTEX Desktop_0.1.0_x64_en-US.msi` (~135MB) and `KORTEX Desktop_0.1.0_x64-setup.exe` (~103MB) via the real WiX (`candle`/`light`) and NSIS (`makensis`) toolchains, auto-provisioned by `tauri-cli`. `bundle.windows.wix.upgradeCode` set to a freshly-generated, fixed GUID (must never change across future versions); `bundle.windows.nsis.installMode` set to `"currentUser"` (no admin/UAC elevation required, sidestepping the Program-Files-writability question at the installation step itself, on top of Phase C's runtime-level fix).

**Phase F (install/upgrade/reinstall/uninstall lifecycle) — PASS, verified via the real NSIS installer, not simulated**:
- **Install**: silent install (`/S`) → binaries at `%LOCALAPPDATA%\KORTEX Desktop\`, including the bundled `kortex-backend\` resource — confirmed present.
- **First launch**: `/health` reached within ~1s; all engines healthy except Backup (expected — no `KORTEX_BACKUP_KEY`/valid `KORTEX_MASTER_KEY` format supplied in this ad-hoc test, an unrelated, pre-existing Backup Engine behavior, not a defect of this milestone).
- **Crash/restart**: the real backend process was killed (`Stop-Process -Force`); the existing supervisor detected the unexpected exit and restarted it per the existing, unmodified policy (100ms backoff) — healthy again within ~2s.
- **Reinstall/upgrade**: running the same installer again over the existing install left the database byte-for-byte unchanged (identical MD5 before/after: `7edf96a027d75851497b151970bd5eea`); the app relaunched healthy afterward.
- **Uninstall**: silent uninstall removed the install directory completely; the app-data directory (database, `storage_data/`, `backups/`) survived untouched — verified directly on disk, not merely by a (transiently unreliable) `Test-Path` check.

**Phase G (CI artifact build) — workflow added, NOT yet executed on GitHub Actions.** A new `windows-installer` job was added to `.github/workflows/desktop-ci.yml`, running on `windows-latest`: freeze the backend, run the OCR self-test, `tauri build`, verify both artifacts exist, run the identical install → launch → poll `/health` → uninstall smoke test using freshly-generated throwaway keys (never committed), then upload the MSI/NSIS as build artifacts (not a public release). YAML syntax validated (`yaml.safe_load`); **the workflow itself has not yet run on an actual GitHub Actions Windows runner** — that requires a push, which requires separate authorization (per this repository's established commit/push governance).

**Phase H (signing) — deferred, per OD-DI-2.** No certificates fabricated, no signing secrets added, nothing committed.

**Phase I (fresh-machine validation) — partially verified, honestly scoped.** Everything above was verified via the **actual installed artifact** launched independently of any dev tooling invocation (not `cargo tauri dev`, not a manually-run Python interpreter) — a materially stronger signal than a build-success claim alone. **What this is not**: a genuinely clean machine with no Python/Node/Rust ever installed system-wide (this machine has all three, though the installed application under test invokes none of them — it runs the frozen executable directly). This distinction is stated explicitly, not glossed over.

**macOS (§D18, OD-DI-3) — NOT ATTEMPTED, no macOS build environment available in this session.** `bundle.targets` remains `["msi", "nsis"]` only; no `"dmg"` target was added. This is not a silent downgrade — OD-DI-3 was never resolved by anyone, and this pass does not resolve it either; it proceeds with the plan's own recommended, explicitly-flagged Windows-first path.

---

## D26. Install/Upgrade/Reinstall/Uninstall Data Policy (verified, not merely proposed)

| Lifecycle event | Binaries (`%LOCALAPPDATA%\KORTEX Desktop\`) | Database + `storage_data`/backups (`%APPDATA%\com.kortex.desktop\storage_data\`) | Keys (OS keyring) |
|---|---|---|---|
| **Install** | Written fresh | Created on first launch (Kernel.boot() + migrations) | Created on first launch, persisted |
| **Upgrade/Reinstall** | Replaced | **Preserved — verified via identical MD5 before/after** | Preserved (keyring untouched by install/uninstall) |
| **Uninstall** | **Removed — verified** | **Preserved by default — verified directly on disk.** No explicit purge option exists yet (a future UI/config addition, not a repository ambiguity — tracked as an implementation detail, not an owner decision) | Preserved (NSIS/WiX uninstall has no keyring-clearing step; nothing in this codebase clears it either) |

This is NSIS's actual default behavior for a `currentUser`-mode install with no unusual custom uninstall hooks configured — verified empirically, not merely asserted from documentation.

**Why this is intentional policy, not an accidental consequence of a default that could silently change**: Tauri's generated NSIS uninstaller only ever removes the files *it itself installed* (tracked via its own install manifest) — it has no built-in concept of "also delete AppData" at all, and no custom uninstall script/hook is configured anywhere in this repository (`tauri.conf.json`'s `bundle.windows.nsis` block declares only `installMode`; grepping the repo confirms no `.nsh`/custom-uninstall-step file exists). Because JSON (`tauri.conf.json`) cannot carry comments, this intent is recorded here and in `desktop_entrypoint.py`'s/`backend_process.rs`'s own code comments instead: **the absence of a purge step is the deliberate v1 policy**, not an unexamined default — a future purge option would require deliberately *adding* new uninstall-hook configuration, which does not exist today and is explicitly out of scope for this milestone (§D21).
