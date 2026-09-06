# KORTEX OS — Release Candidate Readiness

**Authoritative RC readiness document.** Produced by the Final Production Reconciliation pass and finalized by the Final RC Ambiguity Resolution & Baseline Freeze pass. For per-work-package implementation detail and acceptance records, see `docs/architecture/PRODUCTION_HARDENING_RECONCILIATION.md`; for roadmap completion status, see `.kortex/roadmap.md`.

> **Manual RC validation:** the test environment and runbook the Chief Architect uses to exercise, stress, and attempt to break this RC live in **[`rc-testing/`](rc-testing/README.md)**. That directory records defects found while building the environment (`rc-testing/KNOWN_FINDINGS.md`). **DEFECT-001** (P1, pre-existing — Backup Engine could not resolve the Windows desktop's `0x`-prefixed key, so Backup, and by extension the checkpoint Recovery/Update both depend on, was non-functional on the desktop build) has been **found, fixed, and verified**: `BackupCryptoManager` now accepts the platform's own already-canonical `0x`-prefixed hex representation — the same one `kernel_bootstrap.py` and Docker's entrypoint already accepted — closing the one consumer that was out of step with an established contract. Verified with a real backend, the real desktop-generated key format, and no workaround: `kortex.backup.create` → 200, `kortex.backup.verify` → 200. See `KNOWN_FINDINGS.md` for full detail.

**Status terminology used here, and nowhere blurred**: `DONE`, `PASS`, `TECHNICAL RC READY`, `OWNER DECISION REQUIRED`, `DEFERRED / POST-RC`, `PUBLIC RELEASE BLOCKER`.

---

## 1. RC Definition

KORTEX distinguishes two states that must never be collapsed into a single "production-ready" claim:

**TECHNICAL RC READINESS** — the implemented architecture and accepted production-hardening workstreams are complete enough to freeze a Release Candidate build. This does **not** imply the artifacts are publicly distributable.

**PUBLIC PRODUCTION RELEASE READINESS** — the RC has additionally satisfied release-policy requirements: artifact signing, release identity, distribution trust, an approved release version/tag, and any organization-required release credentials.

The progression is:

```
COMPLETED ENGINEERING → TECHNICAL RELEASE CANDIDATE → RELEASE IDENTITY / SIGNING → PUBLIC PRODUCTION RELEASE
```

**KORTEX's current position:**

```
TECHNICAL RC READY
PUBLIC PRODUCTION RELEASE: PENDING RELEASE IDENTITY / SIGNING POLICY
```

## 2. Current Baseline

| Item | Value |
|---|---|
| Accepted engineering baseline | `98d94b4c4da9cbec0b3c50af6894af88f8796aae` |
| Documentation reconciliation commit | `86469c97820713ff9774973319d28331a51b55b7` |
| Branch | `main` |
| Supported RC distribution topologies | Windows x64 desktop installer (MSI + NSIS); Docker/headless production container |

## 3. Completed Architecture — `DONE`

| Phase | Status | Notes |
|---|---|---|
| Phase 1 — Core Microkernel & Runtime | `DONE` | |
| Phase 2 — Business Foundation | `DONE` | |
| Phase 3 — Desktop / UI | `DONE` | Tauri v2 shell, React/TS/Tailwind, IPC bridge, design system |
| Phase 4 — AI Native Engine & Knowledge Layer | `DONE` | AI Engine (M1–M13), Tool Engine, Knowledge Engine, Document Intelligence, Capability Identity Propagation security |
| Phase 5 — Advanced Business Engines & Approvals | `DONE` | Durable approvals, Process Intelligence, License Engine (M5.7) |
| Phase 6 — Pilot Business Modules | `DONE` | Module base contract, Finance, HR & Payroll, Operations |
| Phase 7 — Production Hardening | `DONE` | §4 below |
| Application Completion track M7.1–M7.5 | `DONE` | Local runtime, AI Studio conversational, Connector/Document/Knowledge ↔ AI Studio integrations |

**There is no next core engine.** The planned engineering roadmap is complete.

## 4. Phase 7 Production Hardening — `DONE`

| Work Package | Status | Evidence |
|---|---|---|
| Sentinel | `DONE` | Reconciliation §5.2 |
| Monitoring Engine | `DONE` | Reconciliation §5.3 |
| Backup Engine | `DONE` | Reconciliation §5.4 |
| Recovery Engine | `DONE` | Reconciliation §5.5 |
| Update Engine | `DONE` | Reconciliation §5.6 |
| Docker Production Builds | `DONE` | Reconciliation §5.7 |
| Desktop Installers (Windows MSI + NSIS) | `DONE` | Reconciliation §5.8 |
| Database Migration Wiring *(repository-derived)* | `DONE` | Reconciliation §5.1 |
| CI/CD *(repository-derived)* | `DONE` | Reconciliation §5.9 |
| Production Secret Storage / Native Windows Keyring *(repository-derived)* | `DONE` | Reconciliation §5.11 |

## 5. Security Readiness — `PASS`

| Area | Status | Evidence |
|---|---|---|
| Execution identity (authenticated identity remains execution identity) | `PASS` | `CapabilityExecutionContext` injected at registration-time, never caller-suppliable; `test_capability_identity_propagation_architecture.py` static guard against recurrence |
| Tenant isolation | `PASS` | Authoritative principal-derived tenant across engines and business modules; caller tenant overrides rejected |
| Secret storage — `Found` / `ConfirmedAbsent` / `Unreadable` semantics | `PASS` | `secure_keys.rs`; `Unreadable` fails closed and never generates a replacement key |
| Secret storage — real OS backing | `PASS` | Windows Credential Manager verified across genuine process boundaries in CI (§10) |
| Backup encryption fail-closed | `PASS` | AES-256-GCM mandatory; missing/invalid key aborts, no plaintext fallback |
| Recovery authorization / rollback / operator halt | `PASS` | Journal-driven, staged-only, fail-closed on interrupted rollback |
| Update manifest authenticity | `PASS` | Ed25519 signature + SHA-256 verification; archive traversal/ZIP-bomb/symlink defenses |
| License (M5.7) infrastructure semantics | `PASS` | Unchanged; no business pricing/enforcement rules added |
| Desktop (Tauri lifecycle ownership, read-mostly install dir) | `PASS` | §9 |

## 6. Database / Migration Readiness — `PASS`

| Case | Status |
|---|---|
| Fresh install / fresh database | `PASS` |
| Fresh install with missing storage directory (real defect, fixed `f55b6bb`) | `PASS` — regression test added |
| Existing fully-migrated database (idempotent re-run) | `PASS` |
| Legacy `create_all()`-only database (pre-Alembic) | `PASS` |
| Legacy database with present-but-empty `alembic_version` | `PASS` |
| Partial / incomplete legacy schema | `PASS` — stamps no further than the last fully-verified revision, never guesses past a gap |
| Legitimately-tracked intermediate Alembic revision | `PASS` — upgrades forward, drops nothing |
| CWD-independent migration execution | `PASS` — absolute/`sys._MEIPASS` resolution, proven by the frozen backend running with app-data as CWD |

Zero new migrations were introduced by any RC-preparation pass.

## 7. Storage Topology — `PASS`

One authoritative persistent application-data root, derived once in Rust (`backend_process.rs::resolve_app_data_dir` via Tauri's `app_data_dir()`) and passed to the backend as both working directory and explicit `KORTEX_DATABASE_URL`/`KORTEX_STORAGE_DIR`.

| Requirement | Status |
|---|---|
| Database beneath the app-data root | `PASS` |
| `storage_data` beneath the app-data root | `PASS` |
| Backups beneath the persistent root | `PASS` |
| Install directory read-mostly (no persistent writes) | `PASS` — verified absent from the install directory |
| CWD does not determine production persistence | `PASS` |
| Uninstall removes binaries, preserves data | `PASS` — database byte-identical (SHA-256) after uninstall |
| Single persistent root (no second root, no per-engine sprawl) | `PASS` |

## 8. Docker Production Readiness — `PASS`

Image build, container startup, Alembic migration ahead of serving traffic, `/health` smoke test, non-root runtime, image-layer secret/VCS-metadata scan, container stop — all exercised by the Backend CI `docker` job on every push, most recently green at run `33992464212`. Persistent state externalized to a volume; production configuration explicit; fail-closed secret preflight.

**Update Engine boundary**: `kortex.update.apply` is out of scope for the container topology — updating KORTEX in Docker means building a new image and replacing the container, with the persistent volume preserved. Update Engine does not and must not mutate a running container image.

## 9. Windows Desktop Readiness — `PASS`

| Requirement | Status |
|---|---|
| MSI builds | `PASS` |
| NSIS builds | `PASS` |
| Frozen backend bundled as a Tauri resource | `PASS` — build-time packaging guard asserts the real artifact |
| Frozen backend boots; migrations run | `PASS` |
| `/health` reachable | `PASS` — 4–7s across runs |
| Desktop application launches | `PASS` |
| Backend crash → Tauri restarts it → healthy again | `PASS` — new PID, 1–2s recovery |
| Persistent data survives reinstall | `PASS` — byte-identical |
| Uninstall removes binaries, preserves app data | `PASS` |
| Install directory not used for persistent writes | `PASS` |
| No secrets leaked into artifacts | `PASS` |

Tauri remains the sole lifecycle owner of the application and the backend sidecar. No second supervisor, service, or scheduled task exists.

## 10. Native Keyring — `PASS`

The `keyring` dependency previously resolved with no platform feature, silently selecting the crate's in-memory **mock** store on every platform including Windows — so master/signing keys never persisted, despite `secure_keys.rs`'s fail-closed design assuming they did. Fixed by enabling `windows-native` (commit `98d94b4`).

Proven, not mocked: a committed, `#[ignore]`-gated integration test re-invokes the compiled test binary as genuinely separate OS processes and verifies store → cross-process retrieve → exact match → delete → confirmed absence against the real Windows Credential Manager, using a disposable identifier distinct from production's. Independently cross-checked out-of-band via `cmdkey`. **Executed in Desktop CI run `33992464220`: success, 196s.** No secret value is printed, logged, or asserted against — only lengths and equality booleans.

## 11. CI/CD — `PASS`

| Workflow / Job | Latest Status |
|---|---|
| Backend CI — Lint, type-check, and test (Python 3.12) | `PASS` (`33992464212`) |
| Backend CI — Docker build and smoke test | `PASS` (`33992464212`) |
| Desktop CI — Typecheck and test (TypeScript) | `PASS` (`33992464220`) |
| Desktop CI — Tauri shell (cargo check) | `PASS` (`33992464220`) |
| Desktop CI — Windows installer build and smoke test | `PASS` (`33992464220`) |
| Desktop CI — Real Windows keyring integration test | `PASS` (`33992464220`, 196s) |

CI has demonstrably caught real defects rather than merely passing: Desktop CI #41 and #42 each surfaced a genuine, CI-only-reproducible failure that local testing could not have caught, both diagnosed and fixed using CI evidence.

## 12. Test Evidence

**Backend full suite**: `3,260 passed, 2 skipped, 1 failed` (3,263 collected).

- **2 skipped** — pre-existing, documented Ollama-unavailable environmental skips.
- **1 failed** — `test_execution_envelope_and_idempotency.py::test_client_timeout_cancellation_does_not_strand_processing_record`. Classification: **PRE-EXISTING NON-DETERMINISTIC TEST ISSUE**. Reproduced: passes 3/3 in isolated single-test runs and 20/20 running its full file in isolation; fails only under full-suite load. Identical signature to the flake already documented during Update Engine acceptance. Structurally unreachable from any recent work. **Not modified, not skipped, not weakened.** Does not block technical RC.

This is deliberately **not** reported as "all tests passed."

**Desktop suite**: `575 passed, 0 failed` (design-system 50, apps/desktop 525).
**Rust (Tauri crate)**: `48 passed, 0 failed, 2 ignored` (the two real-OS-keyring tests, `#[ignore]`-gated by design, executed explicitly in CI).
**Quality gates**: `ruff check .` — 0 errors. `mypy src` — 0 errors across 290 files. `cargo check` — clean. `cargo clippy` — 1 pre-existing `large_enum_variant` warning in `sidecar.rs`, documented before this work, not newly introduced.

## 13. Artifact Evidence

| Artifact | Value |
|---|---|
| Windows MSI | ≈134.7 MB |
| Windows NSIS | ≈103.1 MB |
| Frozen backend (onedir, pre-archive) | ≈17.4 MB |
| Docker image | Built and smoke-tested in CI; size not measured locally (no local Docker daemon) |
| Installer footprint reduction | `DEFERRED / POST-RC` — optional optimization; no build, CI, runtime, or distribution failure has ever resulted from current size |

## 14. Release Identity / Signing

### 14.1 Release Identity — `OWNER DECISION REQUIRED`

Repository evidence is genuinely conflicting, so the correct RC version cannot be determined unilaterally:

| Signal | Value |
|---|---|
| `apps/desktop/package.json`, `tauri.conf.json`, `Cargo.toml`, `backend/pyproject.toml` | all `0.1.0` |
| `CHANGELOG.md` last released version | `[0.2.0]` (2026-08-07) — manifests were never bumped for it |
| Latest git tag | `v0.2.0` → `2b256d7`, **213 commits stale**, Phase 2 era |
| `docs/architecture/ARCHITECTURE_VERSION_1.0.md` | Architecture Version **1.0.0**, *"FROZEN & IMMUTABLE"*, ratified by the Chief Architect, 2026-08-08 |
| Git tag `architecture-v1.0` | exists |

**Recommended RC identifier (exactly one):**

```
KORTEX RC: v1.0.0-rc.1
```

**Rationale from repository evidence**: the architecture is formally frozen and ratified at version 1.0.0; the entire planned roadmap (Phases 1–7 plus the M7.1–M7.5 application-completion track) is complete; this is the first production-ready freeze of that complete, ratified architecture. Continuing a `0.x` line would materially understate a completed 1.0 architecture, and `0.2.0`→`0.3.0` would be indefensible given the scope delivered since (Phases 3–7 in their entirety).

**Not performed, because not authorized** — the exact steps an owner would run after approval:
1. Bump four manifests `0.1.0` → `1.0.0`: `apps/desktop/package.json`, `apps/desktop/src-tauri/tauri.conf.json`, `apps/desktop/src-tauri/Cargo.toml`, `backend/pyproject.toml`.
2. Add a `CHANGELOG.md` `[1.0.0]` entry covering Phase 7 (currently unwritten — see §15).
3. `git tag -a v1.0.0-rc.1 -m "KORTEX v1.0.0-rc.1"`.

No manifest was blindly bumped and no tag was created by this pass.

### 14.2 Code Signing — `PUBLIC RELEASE BLOCKER` / technical RC `NOT BLOCKED`

**Current state**: MSI and NSIS are **unsigned**. Verified by direct inspection — `certificateThumbprint`/`signingIdentity` absent from both `tauri.conf.json` and `tauri.windows.conf.json`; no signing step in `.github/workflows/desktop-ci.yml`; no certificate anywhere in the repository.

| Question | Answer |
|---|---|
| What does signing protect? | Publisher identity (users can verify KORTEX published the installer) and artifact integrity (tamper detection between build and install). Without it, Windows SmartScreen/Defender warns users, and enterprise deployment policies commonly refuse unsigned installers outright. |
| Which artifacts require signing? | The NSIS `-setup.exe`, the `.msi`, and the installed `kortex-desktop.exe` (and, per common practice, the bundled `kortex-backend.exe`). |
| What infrastructure is missing? | An Authenticode code-signing certificate (OV or EV; EV avoids SmartScreen reputation build-up), a signing step in the Windows CI job, and Tauri signing configuration. |
| What credentials are required? | The certificate and its private key — or, preferably, a cloud signing service (e.g. Azure Trusted Signing / an HSM-backed service) so the private key never exists as a file. |
| Where must those credentials live? | GitHub Actions encrypted secrets or a cloud key vault referenced by CI. **Never in this repository**, never in a commit, never in a build log. |
| Does this block technical RC? | **No.** Unsigned artifacts build, install, launch, and run correctly — proven repeatedly on fresh CI Windows runners. |
| Does this block public distribution? | **Yes.** |

No fake certificate, test certificate, or simulated signing step was added. Signing was not made to *appear* successful.

## 15. Deferred Items

| Item | Status | Reason |
|---|---|---|
| macOS/Linux desktop distribution (incl. `.dmg`) | `DEFERRED / POST-RC` — future platform expansion | Not in the RC distribution scope; not built, not faked, no placeholder or non-executable CI job added |
| Docker OS-credential-store parity (OD-2) | `DEFERRED / POST-RC` | Docker production v1 uses the accepted operator-supplied-secret model. Native OS credential-store parity with the Windows desktop keyring is not an RC requirement and remains future release-engineering work. A container has no user-session credential store to bind to. |
| Bare/server fresh-machine validation | `DEFERRED / POST-RC` deployment hardening | Outside the supported RC distribution topology (Windows desktop + Docker are both validated) |
| Installer footprint optimization | `DEFERRED / POST-RC` optional optimization | No functional or distribution blocker demonstrated |
| `CHANGELOG.md` Phase 7 backfill | `DEFERRED / POST-RC` documentation debt | Last updated through M7.5; covers no Phase 7 work. Required before a `[1.0.0]` release entry, not before technical RC freeze |
| External M7.x vs. native Phase 6/7 numbering conflict | `DEFERRED` | Long-standing documentation-numbering question, explicitly out of scope for every production-hardening pass |
| Pre-existing `sidecar.rs` `cargo fmt` drift and `large_enum_variant` clippy warning | `DEFERRED / POST-RC` | Predate all recent work; `fmt` is not part of the CI baseline; clippy is informational by documented, deliberate choice |

## 16. Owner Decisions

Exactly two remain. Both are release-policy decisions that cannot be determined from repository evidence.

1. **RC version / tag** — `OWNER DECISION REQUIRED`. Recommended: `v1.0.0-rc.1`, with rationale and the exact four-file bump documented in §14.1. Conflicting in-repo signals (manifests `0.1.0`, CHANGELOG `0.2.0`, ratified architecture `1.0.0`) mean this cannot be settled unilaterally.
2. **Public-release signing policy** — `OWNER DECISION REQUIRED`. Whether this RC is for internal/testing distribution (unsigned is acceptable) or public release (signing required first), and which signing approach to procure. See §14.2.

No other owner decision is outstanding. OD-2, fresh-machine validation, macOS/Linux, and roadmap synchronization were all resolved to definitive statuses and are no longer owner decisions.

## 17. RC Gate

```
TECHNICAL RC READY
```

KORTEX has no remaining core-engine implementation required for RC. The engineering roadmap is complete through Phase 7. Remaining work, if any, is release-policy/distribution work rather than core product engineering.

```
PUBLIC RELEASE PENDING SIGNING / RELEASE IDENTITY
```

Signing and release identity are the only remaining public-release requirements.

## 18. Final Baseline / Freeze Procedure

The repository is prepared so the owner can freeze the RC with explicit, minimal commands after approval. Nothing below has been performed.

1. **Approve the RC identifier** (§14.1) — recommended `v1.0.0-rc.1`.
2. **Bump the four version manifests** to the approved version (exact files listed in §14.1).
3. **Add the `CHANGELOG.md` release entry** covering Phase 7 (§15).
4. **Commit** the version bump and changelog.
5. **Tag**:
   ```
   git tag -a v1.0.0-rc.1 -m "KORTEX v1.0.0-rc.1"
   git push origin main --follow-tags
   ```
6. **Build the RC artifacts** from the tagged commit (PyInstaller freeze → `pnpm tauri build` → MSI + NSIS), or download them from the tagged commit's Desktop CI run artifacts.
7. **Sign the artifacts** — only once §14.2's signing decision and credentials exist. Until then, artifacts are RC-internal, not for public distribution.

**Freeze state at the time of this document**: roadmap, reconciliation, and RC readiness are mutually consistent with no unexplained contradiction; every deferred item has a stated reason; every remaining owner decision is explicit; Graphify is aligned with HEAD; the working tree is clean.
