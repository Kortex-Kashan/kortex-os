# KORTEX Browser — Browser-B1 Implementation Report

## WebView2 Browser Runtime Foundation

PHASE: B1 — WebView2 Browser Runtime
STATUS: **COMPLETE**
Date: 2026-09-25

---

## OBJECTIVE

Prove that KORTEX can create, own, display, navigate, and safely destroy a WebView2 child webview through a KORTEX-owned runtime abstraction (`BrowserRuntime`/`WebView2RuntimeAdapter`), with no capability/governance layer, no tabs UI, no history, no downloads, no authentication, and no AI involvement — all explicitly deferred to later phases.

---

## ARCHITECTURE

Implements the approved V1 architecture confirmed by the Browser-B1 preflight (`docs/architecture/browser_b1_preflight_report.md`):

```
KORTEX Desktop
    ↓
Browser Application        (apps/desktop/src/features/browser, wired into DEFAULT_APPLICATIONS)
    ↓
BrowserRuntime              (trait — apps/desktop/src-tauri/src/browser_runtime.rs)
    ↓
WebView2RuntimeAdapter<R>   (implements BrowserRuntime against real tauri::Window<R>/Webview<R>)
    ↓
Embedded WebView2 child webview  (via Window::add_child, confirmed supported in pinned tauri = "=2.11.5")
```

`BrowserRuntime` exposes only `create_surface` / `navigate` / `reload` / `query_state` / `destroy` / `destroy_all`, all operating on an opaque `BrowserSurfaceId` — no WebView2/wry type crosses the trait boundary, satisfying the task brief's "must not expose WebView2-specific types to callers" requirement. `create_surface` deliberately combines what the task brief lists as separate "create" and "attach" responsibilities into one call, because Tauri's own primitive (`WebviewBuilder` + `Window::add_child`) does not separate them either — see `browser_decision_log.md`. `go_back`/`go_forward` were deliberately omitted (D11) — wry's stable API has no session-history navigation, and the task brief explicitly defers "history."

---

## IMPLEMENTATION

### Rust (`apps/desktop/src-tauri`)
- **`Cargo.toml`**: `tauri`'s `unstable` feature enabled (`[dependencies]`) to unlock `Window::add_child`. No new Cargo dependency was added — `webview2-com`/`windows` were already transitively present via `tauri`'s own `wry` default feature, confirmed in the Browser-B1 preflight.
- **`src/browser_runtime.rs`** (new, ~380 lines): `BrowserSurfaceId`, `CreateSurfaceRequest`, `BrowserSurfaceState`, `BrowserRuntimeError` (plain types, all `Serialize`/most `Deserialize`); the `BrowserRuntime` trait; `WebView2RuntimeAdapter<R: tauri::Runtime>` (holds the main `Window<R>`, a `profile_root: PathBuf`, and a `Mutex<HashMap<BrowserSurfaceId, Webview<R>>>` surface registry); `sanitize_profile_id`/`resolve_profile_directory` (free functions — see SECURITY); `default_profile_root`; the five `#[tauri::command]` wrappers (`browser_create_surface`/`.navigate`/`.reload`/`.query_state`/`.destroy`) plus `BrowserRuntimeState` app-state wrapper.
- **`src/lib.rs`**: registers `mod browser_runtime;`, adds the five new commands to `tauri::generate_handler!`, constructs one `WebView2RuntimeAdapter` bound to the `"main"` window in `.setup()` (resolving the profile root via `app.path().app_data_dir()`, degrading to `.` rather than failing app startup if unresolvable — matching this file's existing degrade-not-fail posture), and calls `BrowserRuntimeState::destroy_all()` from both the existing `CloseRequested` and `ExitRequested` shutdown handlers, alongside `SidecarSupervision::shutdown()` — Browser-B1's explicit "no orphaned browser runtime" requirement.
- **`capabilities/browser.json`** + **`permissions/browser-runtime.toml`** (new): see SECURITY.

### Frontend (`apps/desktop/src`)
- **`features/browser/api.ts`** (new): thin `invoke()` wrapper for the five commands, mirroring `ipc/session.ts`'s convention — direct Tauri IPC, not routed through `invokeCapability`/the backend (no capability layer exists yet).
- **`features/browser/components/BrowserApp.tsx`** (new): a single-surface proof-of-concept UI (URL input, Open/Navigate/Reload/Close), with an unmount effect that destroys any live surface — the frontend-side half of the "no orphaned runtime" requirement (the Rust shutdown handlers are the second, independent half, covering whole-app-quit).
- **`workspace/icons.tsx`**: added `BrowserIcon`.
- **`workspace/defaultApps.ts`**: added the `browser` entry to `DEFAULT_APPLICATIONS` (route `/browser`, `permissions: ["kortex.browser.view"]` — declared, not yet enforced, matching every other entry's current state per `workspaceTypes.ts`'s own documented M2.2 scope).
- **`shell/navigation/navConfig.ts`**: removed the disabled `{ id: "browser" }` placeholder from `NAV_GROUPS`, per the corrected integration point the Browser-B1 preflight identified (D8) — mirroring exactly how "AI Studio" and "Marketplace" made this same move in M2.3.

---

## SECURITY

### The core requirement
"Website → WebView2 → Browser Runtime → KORTEX native boundary" — no path from page-loaded JavaScript to any privileged KORTEX command. Verified, not merely asserted:

1. **No native bridge is exposed to page JavaScript anywhere in this module.** `WebView2RuntimeAdapter` only ever calls `with_webview`-style APIs from the Rust side; nothing injects a KORTEX-aware bridge into page content.
2. **A load-bearing capability-scoping finding (D10)**: direct inspection of `tauri_utils::acl::capability::Capability`'s own doc comment confirmed that a capability's `"windows"` field grants its permissions to **every webview embedded in that window** — including a child surface `add_child` creates there — regardless of the child's own label. `capabilities/browser.json` therefore uses `"webviews": ["main"]`, not `"windows": ["main"]`, so a child browser surface (labeled `browser-surface-<id>`) cannot match by label.
3. **A second, independent layer, also verified by direct source inspection**: `tauri::ipc::authority::Origin::matches` unconditionally returns `false` for a `Remote` origin against a capability resolved to `ExecutionContext::Local` (the default when no `remote` field is declared). Neither `default.json` nor `browser.json` declares `remote`. Every browser surface always loads `WebviewUrl::External(...)` (a Remote origin). So even independent of (2), no browser surface's content can ever satisfy either capability file's grant.
4. **Profile-directory boundary**: `CreateSurfaceRequest::profile_id` is an opaque string; `resolve_profile_directory` (a pure, unit-tested free function) sanitizes it (ASCII alphanumeric/`-`/`_` only, length-capped, falls back to `"default"` if empty) before joining it under a fixed `profile_root` — the frontend can never steer WebView2's on-disk user-data folder outside that root. This is a minimal stand-in for `BrowserProfileStore` (Browser-B3's job), not the full design.
5. **No existing capability was widened.** `default.json` is untouched. `browser.json` is new, narrow, and — per (2)/(3) — inert against any remote/untrusted content by construction.
6. **No provider authentication, no browser automation, no AI Browser Agent functionality, no Playwright/Stagehand** — none of these exist anywhere in this change.

---

## ADVERSARIAL REVIEW AND POST-REVIEW FIX (2026-09-25)

An independent adversarial review of this phase's diff was performed before commit authorization. Findings and disposition:

- **Confirmed, fixed**: `create_surface`'s id-collision path could drop a newly-created, already-embedded webview without ever calling `.close()` on it (the webview was never inserted into the `surfaces` map, so `destroy`/`destroy_all` could never reach it either) — a real, if previously practically-unreachable, resource leak. **Fixed** by (1) making `BrowserSurfaceId::generate()` collision-resistant via a process-lifetime-monotonic `AtomicU64` sequence appended to the nanosecond timestamp (so a collision can no longer occur within one process's lifetime, regardless of system clock granularity), and (2) retaining the `AlreadyExists` branch as belt-and-suspenders defense-in-depth, now closing the orphaned webview before returning the error. Two new tests (`browser_surface_id_generate_never_collides_across_many_sequential_calls`, `..._across_concurrent_threads`) directly verify the id-generation fix without requiring the crashing `tauri::test` harness (D12) — they target the root cause, not the harness-dependent cleanup path, which remains unverified by automated test for the same reason as the rest of the live-webview lifecycle.
- **Investigated, refuted**: the review separately raised a concern that `destroy_all()` — called synchronously from the `CloseRequested`/`ExitRequested` handlers, which run on the main/event-loop thread — might deadlock if `Webview::close()` shares `WebviewBuilder`'s documented "deadlocks synchronously on the main thread" warning. Direct inspection of `tauri-runtime-wry-2.11.4`'s actual dispatcher (`src/lib.rs:1712-1721,235-255`) shows `close()` routes through `send_user_message`, whose body is `if current_thread == main_thread_id { handle_user_message(...) } else { proxy.send_event(...) }` — a same-thread call executes the close handler inline and returns immediately, with no channel wait. This is architecturally different from `add_child`'s blocking `run_on_main_thread` pattern (documented in `browser_b1_preflight_report.md` §3), which exists specifically because WebView2 controller creation is asynchronous COM machinery requiring the message loop to be pumped. **No deadlock risk was found in the shutdown path; no code change was made or needed here.**
- **Confirmed, correctly deferred to Browser-B3, not fixed now**: `sanitize_profile_id` doesn't guard against Windows-reserved device names (`con`, `nul`, etc.) or a case-insensitive collision with the `"default"` fallback directory. Not a traversal vector — a tenant-isolation nuance for the real `BrowserProfileStore` design.

## TESTS

### Rust (`apps/desktop/src-tauri`) — `cargo test --lib`
**Post-fix: 66 passed, 0 failed, 2 ignored** (pre-existing `#[ignore]`d real-Windows-keyring tests, unrelated). 56 pre-existing tests unchanged throughout (zero regressions at any point); 10 tests in `browser_runtime::tests`, all pure logic (no live `Window`/`Webview` construction — see below):
- `sanitize_profile_id_keeps_only_safe_characters`
- `resolve_profile_directory_never_escapes_the_profile_root`
- `resolve_profile_directory_is_stable_for_the_same_profile_id`
- `resolve_profile_directory_keeps_distinct_tenants_in_distinct_directories`
- `default_profile_root_is_a_dedicated_subdirectory_of_the_app_data_dir`
- `browser_surface_id_generate_never_collides_across_many_sequential_calls` (new, post-review fix)
- `browser_surface_id_generate_never_collides_across_concurrent_threads` (new, post-review fix)
- `browser_surface_id_generate_produces_a_stable_serializable_value`
- `surface_not_found_error_serializes_with_the_offending_id`
- `create_surface_request_deserializes_from_the_frontends_camel_case_shape`

**Honest gap, documented per the task's own instruction not to weaken a test rather than explain a real limitation (D12)**: `tauri::test::{mock_builder, MockRuntime}` — which would let a test exercise the real `add_child`/`WebviewBuilder`/`Webview` code path in-process — was evaluated and attempted. Enabling `tauri`'s `test` Cargo feature alongside the `unstable` feature this crate requires reproducibly crashed every test binary in this crate at process startup (`STATUS_ENTRYPOINT_NOT_FOUND` / `0xC0000139`), before any test filter or even `--list` could run. Root-caused via bisection to the `test`+`unstable` feature combination specifically (not `unstable` alone — verified by temporarily removing the `test` feature: all 56 pre-existing tests still passed with `unstable` enabled and the new module compiled in). The exact failing DLL/symbol was not further isolated within Browser-B1's scope (see `browser_decision_log.md` OD-B5). **Consequence**: the full create → navigate → reload → query → destroy lifecycle against a real or mocked `Window`/`Webview` has no automated test in this environment.

**What substitutes for it, and what it does and doesn't prove**:
- `cargo build` (the real production configuration, no `test` feature) compiled and linked successfully.
- The real, compiled `kortex-desktop.exe` was launched directly (not via `cargo test`) and observed to stay running for 6+ seconds with no crash and no new entry in the app's own crash log — meaning `WebView2RuntimeAdapter::new(main_window, profile_root)` was constructed successfully during real `.setup()`, against a real Tauri window, in the real production runtime. This proves the setup-time wiring is sound.
- **This does not prove** that `create_surface`/`navigate`/`reload`/`destroy` were actually invoked or that a WebView2 surface rendered visible content — no tool available in this session can interactively drive the resulting native OS window (this session's browser-pane tooling renders web content in its own managed tab, not arbitrary native application windows). **A human running `pnpm tauri dev` (or the built app) and clicking through the Browser application's Open/Navigate/Reload/Close buttons is the remaining verification step**, exactly the case the task brief's TESTING section anticipates ("where a test cannot run in the current environment, document exactly why").

### Rust — `cargo clippy --lib`
One pre-existing warning, in `sidecar.rs` (`large_enum_variant`), unrelated to this change and not touched by it. Zero warnings from `browser_runtime.rs` or any file this phase modified.

### Frontend (`apps/desktop`) — `pnpm typecheck` and `pnpm test`
- `tsc --noEmit`: clean, zero errors.
- `vitest run` (full suite, not just the affected files — see below): **809 passed, 0 failed, across 99 test files.** This includes: 8 new tests in `BrowserApp.test.tsx` (open/navigate/reload/close, an error-path render, and — specifically proving the "no orphaned runtime" requirement from the frontend side — a test that unmounting the component while a surface is open calls `destroyBrowserSurface`, and a test that unmounting with no surface ever opened does not); `defaultApps.test.ts` updated for 8 applications (was 7) and a new "wires the Browser application to the real BrowserApp" assertion; `AppSidebar.test.tsx` unchanged and still passing (its "every other nav group item is a disabled placeholder" assertion iterates `NAV_GROUPS` directly, so it automatically stopped covering `browser` once removed from that list — no edit needed there).

---

## EVIDENCE

- `cargo check --lib`: clean (both pre-fix and post-fix).
- `cargo build`: clean, `Finished dev profile ... in 1m 06s`.
- `cargo test --lib`: pre-fix `test result: ok. 64 passed; 0 failed; 2 ignored;`; **post-fix `test result: ok. 66 passed; 0 failed; 2 ignored;`**.
- `cargo clippy --lib`: 1 pre-existing, unrelated warning (`sidecar.rs`); 0 new — unchanged pre- and post-fix.
- Real binary launch: ran 6+ seconds, no crash, no crash-log entry, backend-unreachable message only (expected — the Python backend source isn't present in this working copy; matches `backend_process::spawn_and_monitor`'s documented non-fatal degradation, unrelated to this change).
- `pnpm typecheck`: clean (both pre-fix and post-fix — the fix touched Rust only).
- `pnpm test` (full suite): `Test Files 99 passed (99)`, `Tests 809 passed (809)` — confirmed independently twice (once during implementation, once during the adversarial-review round), unaffected by the Rust-only post-review fix.
- `git status`/`git diff --stat`: see §Git Status below.

---

## KNOWN LIMITATIONS

- Windows-only (WebView2). No macOS/Linux adapter (unchanged from Browser-B0/ADR-0019).
- No automated test exercises a real or mocked live `Window`/`Webview` in this environment (D12/OD-B5) — see TESTS.
- No interactive/visual confirmation that a WebView2 surface actually renders content — requires a human running the real app (see TESTS).
- Fixed placement/size for the single proof-of-concept surface (`DEFAULT_SURFACE_WIDTH`/`HEIGHT`, position `(0,0)`) — dynamic docking to the eventual Browser UI's DOM layout is Browser-B2's job.
- No back/forward navigation (D11) — deferred, not forgotten.
- No persistent profiles yet — `profile_root`/`resolve_profile_directory` are a minimal stand-in; Browser-B3 owns the real per-tenant `BrowserProfileStore` allocation/lifecycle.
- No Browser Policy enforcement point yet (Browser-B4) — today's security posture rests entirely on Tauri's own static ACL (D10), which is real and verified but is not the dynamic, origin/profile-aware policy layer `browser_security_model.md` describes for B4.
- `kortex.browser.view` is a declared-but-unenforced permission string, matching every other `DEFAULT_APPLICATIONS` entry's current state (not a Browser-B1-specific gap).

---

## DECISIONS

See `docs/architecture/browser_decision_log.md` D10 (capability scoping via `webviews`, not `windows`, plus the independent Local/Remote origin finding), D11 (no back/forward in B1), D12 (MockRuntime environment limitation), and OD-B5 (recommend root-causing the test-environment crash before B2/B3).

---

## NEXT PHASE

Browser-B2 — Browser UI (tabs, address/navigation bar, loading state, back/forward/reload, basic browser controls), building on this phase's runtime and the now-corrected `DEFAULT_APPLICATIONS` integration point. Recommended prerequisite: resolve OD-B5 (restore automated Window/Webview testing) before B2/B3 add meaningfully more runtime logic.

---

## Git Status

No commit, no push, performed at any point during this phase. `.kortex/roadmap.md`, `CHANGELOG.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md` (pre-existing, unrelated uncommitted reconciliation-pass changes) and `scratch/` remain untouched. See the chat report for the exact file list.
