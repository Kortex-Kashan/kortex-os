# Browser-B3 Implementation Report — Profiles + Persistent Sessions

**Status**: Complete. See `browser_roadmap_b0_b10.md`'s Browser-B3 entry for the summary; this report is the detailed evidence.

## 1. Baseline

Verified before any code was written:
- `HEAD == origin/main == ed1e8450751ee290e5fc1ff7fddadb6fdb186a04` (B1+B2, both already pushed).
- `v1.0.0-rc.2` tag unchanged at `041084539f4c68a05db4ffa49903bf8c726c4300`.
- Protected files (`CHANGELOG.md`, `.kortex/roadmap.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md`) carried their pre-existing, unrelated diffs (58/11/61 lines) — confirmed byte-identical to that baseline at the end of B3 too.
- `scratch/` untracked, untouched throughout.

## 2. Conditions Resolved (B3.0)

Four conditions were resolved and explicitly approved by the project owner before implementation began — see `browser_decision_log.md` D21, D22, D23, D25 for full detail:

- **OD-B7** (tenant identity bridge): `ipc.rs`'s `IpcClientState` captures `tenant_id` from the login/refresh response's own already-disclosed `SecurityPrincipal` payload — no backend change, no token decoding.
- **OD-B9** (WebView2 distinct-profile coexistence): confirmed live via a temporary, disposable preflight — two distinct profile directories coexist in one process; destroy-then-recreate against the same directory works.
- **D23** (audit architecture): interim local structured JSON-Lines logging, not a new backend capability.
- **Legacy migration**: quarantine-not-delete-not-assign strategy, confirmed against this dev machine's actual (empty) legacy state.
- **D22 verification**: `ICoreWebView2Settings4::SetIsPasswordAutosaveEnabled`/`SetIsGeneralAutofillEnabled` confirmed to exist in the pinned bindings before implementation.

## 3. Identity Architecture

`BrowserProfileId` — 128-bit `getrandom`-sourced CSPRNG randomness, hex-encoded, `profile-`-prefixed. Never a timestamp, never derived from `display_name`. `tenant_id` reuses the existing plain-`str` convention (no new newtype, matching every other KORTEX identity type). See D19.

**CLAIM**: profile ids are unguessable and collision-free.
**IMPLEMENTATION**: `BrowserProfileId::generate()`, `browser_profile_store.rs`.
**ADVERSARIAL TEST**: `browser_profile_id_generate_never_collides_across_many_calls` (10,000 sequential generations, zero collisions); `browser_profile_id_generate_has_sufficient_length_for_128_bits_of_entropy`.
**OBSERVED RESULT**: PASS.

## 4. Storage Architecture

`<app_data_dir>/browser-profiles/<tenant_id>/<profile_id>/{profile.json, .lock, webview2-data/}`. No separate registry file (D20) — `list_profiles` scans the tenant directory directly, reading each profile's own `profile.json`.

Path resolution (`resolve_child_directory`) ports `PathSandboxValidator`'s canonicalize-then-verify-containment algorithm to Rust: charset sanitization (defense in depth) plus full containment re-verification when something already exists at the resolved path (defeats a pre-planted symlink/junction).

**CLAIM**: a path-traversal or pre-planted-symlink attempt cannot escape the profiles root.
**IMPLEMENTATION**: `resolve_child_directory`, `sanitize_path_component`.
**ADVERSARIAL TEST**: `resolve_tenant_directory_sanitizes_traversal_attempts_in_the_tenant_id`, `a_path_traversal_tenant_id_never_escapes_the_profiles_root`, `resolve_child_directory_rejects_a_preplanted_symlink_escaping_the_root` (a real `symlink_dir` planted ahead of time).
**OBSERVED RESULT**: PASS (all three).

**CLAIM**: a genuine Windows path-canonicalization bug (the `\\?\` verbatim-prefix mismatch between a canonicalized existing root and a non-canonicalized not-yet-existing candidate) was caught before shipping, not after.
**IMPLEMENTATION**: `resolve_child_directory`'s doc comment documents the bug and the fix directly.
**ADVERSARIAL TEST**: the very first version of `resolve_tenant_directory_stays_within_the_root_for_a_normal_tenant_id` failed against the naive implementation.
**OBSERVED RESULT**: caught, fixed, re-verified PASS.

**CLAIM**: profiles created under one tenant are invisible to another.
**ADVERSARIAL TEST**: `profiles_created_under_one_tenant_are_invisible_to_another_tenant`.
**OBSERVED RESULT**: PASS.

## 5. Legacy Migration

`quarantine_legacy_default_profile`: moves (never deletes, never parses) `<profiles_root>/default` to `_legacy-quarantine/default-pre-b3-<timestamp>/` if it exists, idempotent (nothing left to move on a second call).

**CLAIM**: legacy state is quarantined, not deleted or silently assigned to a tenant; idempotent across repeated launches.
**ADVERSARIAL TEST**: `legacy_default_profile_is_quarantined_not_deleted_and_the_quarantine_is_idempotent` (verifies original content intact, README present, second construction doesn't create a second quarantine entry), `a_clean_install_with_no_legacy_directory_is_a_no_op`.
**OBSERVED RESULT**: PASS. Confirmed on this dev machine: no legacy `browser-profiles/default` directory exists (verified directly against `%APPDATA%\com.kortex.desktop`), so this is design-verified rather than observed against real accumulated data on this machine.

## 6. ACL Implementation

Baseline OS-user restriction via `icacls /inheritance:r /grant:r "<user>:(OI)(CI)F"` (D26) — not raw Win32 SID/DACL construction, to avoid the risk of a subtly-wrong unsafe implementation silently producing an insecure result. Fails closed: `create_profile` removes the just-created directory if the ACL step fails.

**CLAIM**: a created profile directory's ACL is actually restricted to the current user, not merely "the icacls call returned success."
**IMPLEMENTATION**: `restrict_to_current_user`, called from `create_profile` before writing metadata.
**ADVERSARIAL TEST**: `create_profile_actually_restricts_the_directory_acl` — a REAL, live, read-only `icacls` listing of the created directory, asserting the current user holds `(F)` and no `Everyone`/`\Users:` grant remains.
**OBSERVED RESULT**: PASS (live evidence, not just a non-erroring function call).

**CLAIM**: an ACL failure fails the whole operation closed.
**ADVERSARIAL TEST**: `restrict_to_current_user_fails_against_a_nonexistent_principal` (a real `icacls` call against a syntactically invalid principal).
**OBSERVED RESULT**: PASS — `icacls` genuinely fails; `create_profile`'s own cleanup path (code-reviewed) removes the directory on any `Err` from this function.

## 7. Locking / Crash Recovery

Per-profile `.lock` file: atomic `create_new(true)` for the uncontended path (single OS call, no TOCTOU window); atomic rename-over for stale-lock recovery. PID liveness via `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`.

**CLAIM**: a live process holding the lock is a genuine, not a re-entrant, contention.
**ADVERSARIAL TEST**: `open_profile_rejects_contention_from_a_genuinely_live_other_process` — a REAL, separately spawned child process's PID is written into the lock file.
**OBSERVED RESULT**: PASS.

**CLAIM**: the same process opening the same profile twice (multi-tab) is never treated as contention.
**ADVERSARIAL TEST**: `open_profile_is_reentrant_for_the_same_process`.
**OBSERVED RESULT**: PASS.

**CLAIM**: a dead process's lock is recovered, not treated as contention.
**ADVERSARIAL TEST**: `open_profile_recovers_a_stale_lock_from_a_genuinely_dead_process` — a REAL child process is spawned and reaped (`wait()`), guaranteeing a genuinely dead PID, then written into the lock file. Retried up to 5× with a fresh dead PID each attempt to guard against a narrow, unrelated Windows PID-recycling race in the TEST'S OWN pid selection (not the production logic) under `cargo test`'s parallel execution.
**OBSERVED RESULT**: PASS, stable across repeated runs.

**CLAIM**: a malformed lock file is recovered, not a permanent wedge.
**ADVERSARIAL TEST**: `open_profile_recovers_a_malformed_lock_file`.
**OBSERVED RESULT**: PASS.

**CLAIM**: deletion is refused while a live process holds the profile open.
**ADVERSARIAL TEST**: `delete_profile_refuses_while_a_live_process_holds_the_lock`.
**OBSERVED RESULT**: PASS.

## 8. WebView2 Integration

`CreateSurfaceRequest` now carries an already-resolved `data_directory: PathBuf` instead of a raw `profile_id: String` — `browser_runtime.rs` gained zero knowledge of tenants/profiles. `browser_create_surface` orchestrates: resolve tenant → `BrowserProfileStore::open_profile` → `BrowserRuntime::create_surface` → record the surface↔profile binding (`ActiveProfileSurfaces`) so `browser_destroy` can release the right lock afterward.

**CLAIM**: distinct profile directories coexist simultaneously in one process, and destroy-then-recreate against the same directory works.
**IMPLEMENTATION**: verified via a temporary, disposable live preflight inside the real `lib.rs` `.setup()` (gated behind an env var, removed after verification — never shipped).
**OBSERVED RESULT**: `step1_create_surface_a: Ok`, `step2_create_surface_b_distinct_dir_while_a_alive: Ok`, `step3_close_surface_a: Ok`, `step4_recreate_same_dir_after_close: Ok`.

## 9. Password / Autofill Hardening

`ICoreWebView2Settings4::SetIsPasswordAutosaveEnabled(false)`/`SetIsGeneralAutofillEnabled(false)`, applied to every surface at creation.

**CLAIM**: the setting actually takes effect on a real WebView2 instance, not merely "the setter call didn't error."
**IMPLEMENTATION**: verified via a temporary, disposable live preflight that read the settings back immediately after setting them (removed after verification — never shipped).
**OBSERVED RESULT**: `Ok((false, false))` — both settings confirmed `false` via a live read-back.

## 10. Frontend UX

`useBrowserProfiles` (list/create/rename/delete/switch, auto-creates a "Default" profile on first use if none exist) composed with `useBrowserTabs(activeProfileId)` (reacts to profile changes: first profile opens one tab; subsequent changes close every tab then open exactly one fresh tab against the new profile — decision D24). `ProfileSwitcher.tsx` — a compact `radiogroup`/`radio`-based selector (not `tablist`/`tab`, to avoid an ARIA-role collision with `BrowserTabBar`'s own genuine tabs), with locked/corrupted badges and a delete-confirmation dialog.

**CLAIM**: switching profiles closes every existing tab and opens exactly one fresh tab against the new profile.
**ADVERSARIAL TEST**: `switching profiles closes every existing tab and opens exactly one fresh tab against the new profile` (`BrowserApp.test.tsx`) — two tabs opened against profile A, switch to profile B, assert both A-surfaces destroyed and exactly one new B-surface created.
**OBSERVED RESULT**: PASS.

## 11. Audit Architecture

Interim local, append-only, JSON-Lines log (`<profiles_root>/audit.log`) — `PROFILE_CREATED`, `PROFILE_OPENED`, `PROFILE_CLOSED`, `PROFILE_DELETED`, `PROFILE_LOCK_FAILED`, `PROFILE_CORRUPTION_DETECTED`, `PROFILE_RECOVERY`, `PROFILE_ACCESS_DENIED` (D23). `PROFILE_ACCESS_DENIED` never fabricates a tenant id — logged with `tenant_id: "(unresolved)"`.

**CLAIM**: the audit log never contains secrets.
**ADVERSARIAL TEST**: `audit_log_entries_never_contain_secret_like_field_names` (checks for "password"/"cookie"/"token"/"secret"/"credential" substrings after a full create/open/close/rename/delete cycle).
**OBSERVED RESULT**: PASS.

Nine total audit tests, one per required event type plus the negative secrecy check — all PASS.

## 12. Security Boundary

`capabilities/browser.json` still scopes via `"webviews": ["main"]` (never `"windows"`), still declares no `remote` field. Four new permission entries added to the same file/scoping; no existing permission widened. No command anywhere accepts a tenant id as a parameter (`resolve_tenant_or_deny` is the single, shared resolution point across all 5 tenant-scoped commands).

## 13. Adversarial Review

Systematic pass against the full required checklist (tenant confusion, caller-supplied tenant ids, profile enumeration, path traversal, junction/symlink/reparse attacks, TOCTOU, stale/live locks, corrupted metadata, ACL failure, cross-process access, cross-profile leakage, frontend privilege escalation, command overexposure, audit/credential leakage, shutdown cleanup, crash recovery, legacy migration) — findings below.

| Finding | Severity | Outcome |
|---|---|---|
| `resolve_child_directory`'s original draft canonicalized an existing root but not a not-yet-existing candidate, producing a Windows `\\?\`-prefix mismatch that would fail closed on every legitimate first-time directory creation | HIGH | Fixed before shipping; caught by the function's own test |
| `BrowserProfileError`'s enum `rename_all` did not cascade to struct-variant fields (a real serde behavior) — would have shipped `profile_id` instead of `profileId` on the wire | MEDIUM | Fixed before shipping (explicit per-field `#[serde(rename = "profileId")]`); caught by the type's own test |
| `AuditLogEntry` struct was missing `#[serde(rename_all = "camelCase")]` entirely | MEDIUM | Fixed before shipping; caught by the audit tests |
| A pre-existing, identical field-casing defect in the already-shipped `browser_runtime::BrowserRuntimeError` (`surface_id` vs `surfaceId`) | LOW (cosmetic — an error-message field, not a security issue) | Documented (D27), NOT fixed (out of B3's scope) — flagged as a follow-up task |
| ProfileSwitcher's initial `role="tab"` collided with `BrowserTabBar`'s own genuine tab role, breaking `getAllByRole("tab")` queries in existing B2 tests | MEDIUM (test-correctness, not production security) | Fixed before shipping (`radiogroup`/`radio`) |
| No Windows ACL restriction on profile directories at all | HIGH (explicit B3 requirement) | Implemented (D26), live-verified |
| No Tauri capability/permission entries for the 4 new commands | HIGH (would have left new commands unreachable or inconsistently scoped) | Implemented |
| `cargo fmt` (run crate-wide) reformatted an unrelated file (`secure_keys.rs`) as a side effect | LOW (scope hygiene) | Reverted; confirmed pre-existing, unrelated to B3 |

No BLOCKER-severity findings remained unresolved at the end of this review.

## 14. Tests

- Rust: `cargo test --lib` — 107 passed, 0 failed, 2 ignored (pre-existing). `cargo clippy --lib --tests` — 0 new warnings (1 pre-existing, confirmed via A/B diff against the pre-B3 commit). `cargo fmt --check` — clean for every B3-touched file. `git diff --check` — clean.
- Frontend: `pnpm typecheck` — clean. `pnpm test` (full suite) — 839 passed across 101 files, 0 failed.

## 15. Documentation

Updated: `browser_architecture.md` (§1 diagram, new §2.7), `browser_security_model.md` (§10), `browser_decision_log.md` (D18–D27, OD-B7/OD-B9 marked resolved, OD-B10/B11/B12 opened), `browser_known_limitations.md` (new "As of Browser-B3" section, B2 entry annotated resolved), `browser_roadmap_b0_b10.md` (B3 marked COMPLETE with full evidence), this report.

## 16. Changed Files

New: `apps/desktop/src-tauri/src/browser_profile_store.rs`, `apps/desktop/src/features/browser/hooks/useBrowserProfiles.ts`, `apps/desktop/src/features/browser/components/ProfileSwitcher.tsx`, `docs/architecture/browser_b3_implementation_report.md`.
Modified: `apps/desktop/src-tauri/src/{browser_runtime.rs, ipc.rs, lib.rs}`, `apps/desktop/src-tauri/capabilities/browser.json`, `apps/desktop/src-tauri/permissions/browser-runtime.toml`, `apps/desktop/src/features/browser/{api.ts, components/BrowserApp.tsx, components/BrowserApp.test.tsx, hooks/useBrowserTabs.ts}`, `docs/architecture/{browser_architecture.md, browser_security_model.md, browser_decision_log.md, browser_known_limitations.md, browser_roadmap_b0_b10.md}`.

## 17. Known Limitations

See `browser_known_limitations.md`'s "As of Browser-B3" section for the full, honest list (local-only audit logging, baseline-not-AppContainer ACL, no disk quotas, no encryption-at-rest, a narrow residual stale-lock-recovery TOCTOU window backstopped by WebView2's own folder-exclusivity, `USERNAME`-based ACL not independently verified on domain-joined configurations, OD-B5's live-lifecycle test gap continuing).

## 18. Remaining Risks

OD-B5 (tauri::test crash, unresolved since B1) and OD-B6 (Linux `#[cfg(not(windows))]` fallback, unresolved since B2, now larger given B3's own new non-Windows fallbacks) both carry forward. OD-B10 (disk quotas) and OD-B11 (encryption-at-rest) are newly opened, explicitly deferred. The pre-existing `BrowserRuntimeError` field-casing defect (D27) remains unfixed pending the flagged follow-up task.

## 19. Git Status

At completion (before any commit):
```
 M .kortex/roadmap.md
 M CHANGELOG.md
 M apps/desktop/src-tauri/capabilities/browser.json
 M apps/desktop/src-tauri/permissions/browser-runtime.toml
 M apps/desktop/src-tauri/src/browser_runtime.rs
 M apps/desktop/src-tauri/src/ipc.rs
 M apps/desktop/src-tauri/src/lib.rs
 M apps/desktop/src/features/browser/api.ts
 M apps/desktop/src/features/browser/components/BrowserApp.test.tsx
 M apps/desktop/src/features/browser/components/BrowserApp.tsx
 M apps/desktop/src/features/browser/hooks/useBrowserTabs.ts
 M docs/release/RELEASE_CANDIDATE_READINESS.md
?? apps/desktop/src-tauri/src/browser_profile_store.rs
?? apps/desktop/src/features/browser/components/ProfileSwitcher.tsx
?? apps/desktop/src/features/browser/hooks/useBrowserProfiles.ts
?? scratch/
```
(Documentation files listed in §15 also modified, added after this snapshot was taken during implementation.) The three protected files carry exactly their pre-existing 58/11/61-line diffs, confirmed unchanged throughout. `scratch/` untouched. `v1.0.0-rc.2` untouched.

## 20. Commit Recommendation

Ready for commit authorization: all tests pass, security review complete with no unresolved BLOCKER findings, protected files untouched, scratch/ untouched, rc.2 untouched, no unrelated scope creep, documentation synchronized. Awaiting explicit owner review and commit authorization per repository governance.
