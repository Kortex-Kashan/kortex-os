# KORTEX OS — Release Candidate Readiness

**Status of this document**: RC evidence matrix produced by the Final Production Reconciliation & Release Candidate Preparation pass. It consolidates evidence already recorded in `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` (the authoritative living control document — read that first for full detail on any row below) into one RC-facing acceptance matrix, per that reconciliation pass's own governance.

**Produced against**: HEAD `98d94b4c4da9cbec0b3c50af6894af88f8796aae`, branch `main`.

**Statuses used**: `PASS`, `FAIL`, `BLOCKED`, `DEFERRED`, `OWNER REVIEW REQUIRED`. No vague statuses are used.

---

## 1. RC Acceptance Matrix

| # | Area | Requirement | Evidence | Status | Blocker? | Owner Decision Needed? |
|---|---|---|---|---|---|---|
| 1 | Architecture baseline | Six architectural phases + Phase 7 production hardening complete | Reconciliation §4 status table — all Phase 7 work packages `DONE` | PASS | No | No |
| 2 | Phase 1–6 acceptance | Core kernel, business foundation, desktop/UI, document intelligence, process intelligence/license, module base/Finance/HR/Operations | Frozen per this task's own Rule 2; not reopened, no reproducible defect found or sought | PASS | No | No |
| 3 | Phase 7 acceptance | Sentinel, Monitoring, Backup, Recovery, Update, Docker, Desktop Installers | Reconciliation §5.2–§5.9 — all `DONE` | PASS | No | No |
| 4 | Sentinel | Health monitoring, integrity, deadlock/crash-loop detection | Reconciliation §5.2 — 41 targeted + 50 cross-engine tests, 0 regressions, DONE | PASS | No | No |
| 5 | Monitoring | Metrics, dashboards, threshold alerting | Reconciliation §5.3 — 42 targeted tests, 3,016 full-suite passed at acceptance, DONE | PASS | No | No |
| 6 | Backup | AES-256-GCM encrypted, fail-closed, retention-safe | Reconciliation §5.4 — 47 targeted tests, fail-closed-on-missing-key test reconfirmed passing this pass, DONE | PASS | No | No |
| 7 | Recovery | Staged restore, 4-tier verification, rollback, journal | Reconciliation §5.5 — 74 net-new tests, adversarial security suite reconfirmed passing this pass; **formally accepted this pass** (was "IMPLEMENTED — AWAITING REVIEW") | PASS | No | No — resolved this pass |
| 8 | Update | Ed25519-signed manifests, staged migration, 3-layer rollback | Reconciliation §5.6 — 85 targeted tests, adversarial security suite reconfirmed passing this pass; **formally accepted this pass** | PASS | No | No — resolved this pass |
| 9 | Docker | Production image, non-root, fail-closed secrets, health smoke test | Reconciliation §5.7 — accepted commit `b4b5ffd`; **reconfirmed green at this pass's HEAD** via Backend CI run `33992464212` ("Docker build and smoke test": success) | PASS | No | No |
| 10 | Windows installer | Real MSI + NSIS, bundled frozen backend, full lifecycle | Reconciliation §5.8 — three commits (`9d6b8fe`/`899aee3`/`f55b6bb`), Desktop CI #41→#44, full 16-stage lifecycle verified twice consecutively; **formally accepted this pass** (was `PENDING`/STUB) | PASS | No | No — resolved this pass |
| 11 | Native keyring | Real Windows Credential Manager persistence, fail-closed | Reconciliation §5.11 — commit `98d94b4`, real cross-process CI-executed integration test passed (196s); **formally accepted this pass** (new work package) | PASS | No | No — resolved this pass |
| 12 | Database/migrations | Fresh install, existing DB, legacy compatibility, partial-migration safety | §2 below — full backend suite re-run this pass (3,260 passed), `test_alembic_migrations.py` and `test_desktop_entrypoint_migration.py` (8 tests, including the fresh-install-missing-directory regression added in `f55b6bb`) both green | PASS | No | No |
| 13 | Storage topology | One persistent app-data root; install directory read-mostly | §2 below — verified via the Desktop Installer lifecycle test: database/`storage_data`/backups all under Tauri's `app_data_dir()`, confirmed absent from the install directory, reinstall/uninstall preserve it byte-identically | PASS | No | No |
| 14 | Security | Execution identity, tenant isolation, fail-closed secret handling | §3 below — representative adversarial/capability-identity test suites re-run this pass, all passing; no redesign performed (frozen per Rule 2) | PASS | No | No |
| 15 | Tenant isolation | Capability dispatch enforces authoritative principal tenant, rejects caller override | Part of the full backend suite (`test_capability_identity_propagation_architecture.py` and engine-specific capability tests) — included in the 3,260 passed this pass | PASS | No | No |
| 16 | Execution identity | Authenticated identity remains the execution identity through nested/background paths | Same suite as above; frozen, not re-audited beyond confirming continued green | PASS | No | No |
| 17 | CI/CD | Real, repeatedly-executed GitHub Actions coverage | Reconciliation §5.9 — dozens of real runs across Docker/Desktop Installer/Keyring work, including two genuine CI-only failures found and fixed (Desktop CI #41/#42); **formally accepted this pass** (was "AWAITING REVIEW", "no actual run has occurred") | PASS | No | No — resolved this pass |
| 18 | Backend tests | Full suite, exact counts, no hidden failures | §4 below — 3,260 passed / 2 skipped / 1 failed (full-suite run); the 1 failure reproduced 3/3 in isolation plus 20/20 in its own file, confirmed pre-existing/non-deterministic-under-load, not a regression | PASS (with one documented, pre-existing, non-blocking flake) | No | No |
| 19 | Desktop tests | TypeScript/vitest suite | §4 below — 575 passed (design-system 50 + apps/desktop 525), 0 failed | PASS | No | No |
| 20 | Windows installer smoke | CI-executed install/launch/health/uninstall | Desktop CI #43 (`33989632849`) and #44 (`33992464220`) — both green, smoke test 30s in #44 | PASS | No | No |
| 21 | Docker smoke | CI-executed build/startup/migration/health | Backend CI #44 (`33992464212`) — "Docker build and smoke test": success | PASS | No | No |
| 22 | Versioning | Formal RC version/tag convention | §5 below — none exists; all package manifests at `0.1.0`; last git tag (`v0.2.0`) is 213 commits stale, predates Phase 7 entirely | OWNER REVIEW REQUIRED | No (does not block engineering readiness) | **Yes** — proposed convention below, not silently adopted |
| 23 | Signing | Code-signing status of MSI/NSIS | §5 below — unsigned; no certificate, signing identity, or CI signing credential exists anywhere in the repository | OWNER REVIEW REQUIRED | Depends on release policy | **Yes** — classification depends on whether this RC is for internal/testing distribution or public release |
| 24 | Documentation | Reconciliation doc accurately reflects accepted state | This pass rewrote reconciliation §3–§9 with full evidence; CHANGELOG.md and `.kortex/roadmap.md`'s Phase 7 checklist remain stale (flagged, not silently edited — see §6 below) | PASS (for the reconciliation doc itself); documentation debt flagged separately | No | No — flagged for owner awareness only |
| 25 | Graphify baseline | `built_at_commit == HEAD` | Regenerated this pass; graph rebuilt (real topology change — Graphify indexes `docs/architecture/*.md` content, not code-only), stamp reads `98d94b4c`, exactly matching HEAD at the time of regeneration | PASS | No | No |
| 26 | Git cleanliness | Working tree clean, intended commit only | §7 below | PASS (after this pass's own commit) | No | No |

## 2. Migration / Storage Verification Detail

- **Fresh install**: `test_desktop_entrypoint_migration.py::TestFreshDatabase` and `TestFreshInstallWithMissingStorageDirectory` (the regression test added in `f55b6bb` for the exact defect that broke Desktop CI #41/#42) — both green.
- **Existing fully-migrated database**: `TestFullyMigratedDatabase` — idempotent re-run, green.
- **Legacy `create_all()`-only database** (pre-Alembic): `TestLegacyCreateAllDatabase` (2 cases, including the empty-but-present `alembic_version` table case found during the Desktop Installer milestone) — green.
- **Partial/incomplete legacy schema**: `TestPartialLegacyDatabase` (2 adversarial cases) — confirmed to stamp no further than the last fully-verified revision, never guessing past a gap — green.
- **Legitimately-tracked intermediate Alembic revision**: `TestPartiallyMigratedAlembicDatabase` — upgraded forward correctly, nothing dropped — green.
- **CWD-independent execution**: `resolve_alembic_config()`/`resource_root()` use `sys._MEIPASS`/absolute paths, not the process's working directory — proven by the installer lifecycle test running the frozen backend from the real app-data directory as its CWD, not the source checkout.
- **Storage topology**: `backend_process.rs::resolve_backend_sidecar_config_production_path_with_keys` derives `KORTEX_DATABASE_URL` and `KORTEX_STORAGE_DIR` from the *same* `resolve_app_data_dir()` value — one authoritative root, confirmed by the lifecycle test's direct filesystem inspection (database and `storage_data` both under `%APPDATA%\com.kortex.desktop`, absent from the install directory).
- **Install-directory write behavior**: confirmed the install directory (`%LOCALAPPDATA%\KORTEX Desktop`) contains no `storage_data` subdirectory after a full run — the lifecycle test asserts this directly.
- **Backup/Recovery interaction with the database**: not re-exercised this pass (frozen, no reproducible defect found); `test_recovery_integration.py`/`test_update_integration.py` remain part of the full backend suite and passed in this pass's run.

## 3. Security Verification Detail

- **Execution identity / tenant isolation**: representative capability-identity tests (`test_capability_identity_propagation_architecture.py`, `test_production_capability_permissions.py`, and per-engine capability suites) are part of the 3,260 tests that passed in this pass's full-suite run. Not redesigned, not reopened; verified via continued-green status only, per Rule 2.
- **Secret storage**: `Found`/`ConfirmedAbsent`/`Unreadable` semantics reconfirmed intact this pass (unit tests unchanged and passing); real OS-backed persistence now proven for the first time (§5.11) rather than assumed.
- **Backup**: `test_crypto_manager_fail_closed_when_key_missing` and `test_verifier_missing_key` re-run directly this pass — both pass. Encryption is mandatory; missing/invalid key fails closed, does not fall back to plaintext.
- **Recovery**: `test_recovery_security_adversarial.py` (6 tests) re-run directly this pass — all pass.
- **Update**: `test_update_crypto_manifest.py` and `test_update_security_adversarial.py` (11 tests) re-run directly this pass — all pass. Manifest signature/hash verification, archive-traversal/ZIP-bomb/symlink defenses all exercised.
- **License**: not touched, not re-audited beyond confirming its tests remain part of the passing full suite — no business/pricing logic added, per this pass's own scope rule.
- **Desktop**: Tauri remains sole lifecycle owner (unchanged — `spawn_and_monitor`/`SidecarManager` untouched by this pass); frozen backend remains the production sidecar; keyring is now genuinely OS-native (§5.11); backend restart behavior reconfirmed via the lifecycle test's explicit kill-and-restart step.

## 4. Test Results (exact counts, this pass)

**Backend** (`pytest -q`, full suite):
```
3,260 passed, 2 skipped, 1 failed
```
- 2 skipped: documented, pre-existing Ollama-unavailable skips (`test_ai_ollama_integration.py`) — environmental, not a gap in coverage of anything this RC touches.
- 1 failed: `test_execution_envelope_and_idempotency.py::test_client_timeout_cancellation_does_not_strand_processing_record`. **Reproduced**: 3/3 passes in isolated single-test runs; 20/20 passes running its full file in isolation. **Cause**: real `asyncio.sleep()`-based concurrency-race simulation, sensitive to system load under the full 3,260+-test suite — the identical signature already documented as pre-existing in the Update Engine's own formal acceptance record (§5.6 of the reconciliation doc), which predates this pass and is structurally unreachable from anything this pass touched (no reference to Update/Recovery/Desktop-Installer/Keyring code exists in this test file). **Classification**: pre-existing, non-deterministic under load, not a regression, **not release-blocking**.

**Desktop** (`pnpm test`, vitest):
```
design-system: 20 files, 50 passed, 0 failed
apps/desktop:  74 files, 525 passed, 0 failed
```

**Quality gates** (backend, repo-wide, re-run this pass): `ruff check .` — 0 errors. `mypy src` — 0 errors, 290 source files. **Quality gates** (desktop Rust, re-run this pass, prior to this pass's own doc-only changes): `cargo check` — clean. `cargo clippy` — exactly 1 pre-existing warning (`large_enum_variant`, `sidecar.rs`), documented in `desktop-ci.yml` since before this pass, not newly introduced.

## 5. Artifact / Release Identity

| Item | Value |
|---|---|
| `apps/desktop/package.json` version | `0.1.0` |
| `apps/desktop/src-tauri/tauri.conf.json` version | `0.1.0` |
| `apps/desktop/src-tauri/Cargo.toml` version | `0.1.0` |
| `backend/pyproject.toml` version | `0.1.0` |
| Docker image tag convention | `kortex-backend:dev` (compose dev), `kortex-backend:${KORTEX_IMAGE_TAG:-latest}` (compose prod) — no version-pinned tag scheme exists |
| Last git tag | `v0.2.0`, pointing to `2b256d7` (`feat(recipe): implement Phase 2 Recipe Engine`) — **213 commits behind current HEAD**, predates all of Phase 7; not a usable RC-tag precedent |
| MSI size | ≈134.7 MB |
| NSIS size | ≈103.1 MB |
| Frozen backend size (onedir, pre-archive) | ≈17.4 MB |
| Docker image size | Not measured this pass (no local Docker daemon available in this environment — build/smoke-test evidence comes from CI only, consistent with how this was verified throughout the Docker milestone) |
| Code signing | **Absent.** No certificate, `certificateThumbprint`/`signingIdentity` (checked in both `tauri.conf.json` and `tauri.windows.conf.json`), or CI signing credential exists anywhere in the repository. MSI and NSIS both build and install correctly unsigned — this is a distribution-trust gap (Windows SmartScreen will warn on an unsigned installer), not a functional defect in the installer itself. |

**Proposed versioning convention** (documented for owner approval, not adopted): semantic versioning (`MAJOR.MINOR.PATCH`) already declared in `CHANGELOG.md`'s own header ("this project adheres to Semantic Versioning"); a natural next step would be bumping all four `0.1.0` manifests together to a single synchronized version at the point the owner formally cuts this RC, tagged `v<version>-rc1` (or similar), but this pass does not perform that bump — it is a release-identity decision, not an engineering-readiness one.

## 6. Documentation Debt (flagged, not fixed this pass)

- **`.kortex/roadmap.md:65-75`** still reads `Status: Planned` with all seven Phase 7 checklist boxes unchecked, contradicting the reconciliation document's now-accurate `DONE` statuses. Per the reconciliation document's own governance rule (§0: "if this document conflicts with the current roadmap... STOP and report the conflict — do not silently rewrite history"), this pass does not edit that file. Recommend an explicit owner-authorized pass to check those boxes.
- **`CHANGELOG.md`** was last updated through the M7.5 (Knowledge Engine ↔ AI Studio) entry and its own `[Unreleased]` note already discloses a prior gap; it does not yet cover any Phase 7 Production Hardening work (Sentinel through Native Keyring) at all. Backfilling six-plus milestones' worth of changelog entries was judged disproportionate to this reconciliation pass's scope and was not attempted.
- **External M7.x vs. native Phase 6/7 numbering conflict** (reconciliation §2) remains unresolved, exactly as it has been throughout — explicitly out of scope for every Production Hardening pass, this one included.

## 7. Git State (at the time this document and the reconciliation update were authored)

- HEAD before this pass's own commit: `98d94b4c4da9cbec0b3c50af6894af88f8796aae`
- Files changed by this pass: `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md` (rewritten sections, no code touched), `docs/release/RELEASE_CANDIDATE_READINESS.md` (new, this file)
- No source code, test, workflow, or configuration file was modified by this reconciliation pass — see the accompanying commit for the exact diff.

## 8. RC Gate

See the final report delivered alongside this document for the formal `RC READY` / `RC NOT READY` determination and its precise reasoning. This document is the evidence matrix that determination is based on.
