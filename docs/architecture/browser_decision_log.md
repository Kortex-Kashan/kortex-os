# KORTEX Browser — Decision Log

**Status**: Living document — append-only across every phase. Established Browser-B0.

Format: one entry per decision, in chronological order. Do not delete or rewrite past entries; append corrections as new entries.

---

## 2026-09-25 — D1: Runtime engine and abstraction boundary

**Decision**: KORTEX Browser V1 uses WebView2 + Chromium, hidden entirely behind a new `IKortexBrowserRuntime` interface; the rest of KORTEX depends only on the interface, never on WebView2 directly.
**Recorded in**: `docs/adr/ADR-0019-kortex-browser-runtime-webview2-chromium.md` (formal ADR), `browser_architecture.md` §2.
**Status**: ACCEPTED (issued by the project owner in the Browser-B0 task brief).

## 2026-09-25 — D2: "B0–B10" numbering is Browser-scoped, not repository-global

**Decision**: This project's phase labels (B0 through B10) are always written fully qualified ("Browser-B0" or "KORTEX Browser Phase B0") outside this document set.
**Why**: The Browser-B0 audit found this repository already uses overlapping, non-sequential phase labels elsewhere — canonical `Phase 1`–`Phase 7` in `.kortex/roadmap.md`, a separately-tracked `M7.x` "Application Completion" line, and an unrelated post-RC section literally titled "Phase A Authentication Completion" (`.kortex/roadmap.md`, added by the September 2026 RC reconciliation pass — a pass this audit found already in progress as uncommitted changes and deliberately did not touch). A bare "B0" or "Phase A" reference risks being misread as referring to that unrelated existing section.
**Status**: ACCEPTED.

## 2026-09-25 — D3: KORTEX Browser is the same feature as the already-deferred "Built-in Browser" line item, not a new one

**Decision**: This project activates, rather than reopens or renames, the "Built-in Browser" item `.kortex/roadmap.md:383` already records as deferred and explicitly not an RC blocker. Browser-B0 does not change that status — Browser remains post-RC, non-blocking work.
**Why**: Avoids the false impression that a new, undocumented feature was silently added to release scope. Consistent with `docs/release/RELEASE_CANDIDATE_READINESS.md` §19's own explicit statement that deferred items are not promoted into release blockers by documentation passes.
**Status**: ACCEPTED.

## 2026-09-25 — D4: KORTEX Browser is distinct from the existing "browser automation" Phase 6 non-goal

**Decision**: `phase5_locked_architecture_spec.md:32,681,717`'s rejection of "Browser automation (Playwright, Puppeteer)" as the Phase 6 *desktop UI automation* engine (superseded by FlaUI/UIA3 via the .NET Desktop Agent) is unrelated to, and unaffected by, this project. Playwright/Stagehand may still be used later purely as test/automation adapters against KORTEX Browser itself, per the task brief's own Automation Architecture section.
**Why**: Prevents this project from being read as silently reversing an existing, deliberate architectural non-goal.
**Status**: ACCEPTED. Documented in `browser_vision.md` and `browser_architecture.md` §5.

## 2026-09-25 — D5: Browser capabilities reuse the existing governance chain; the transport does not need to be gRPC/mTLS

**Decision**: `kortex.browser.*` capabilities register through the same `CapabilityDescriptor`/tool-bridge/approval/throttling chain as every other governed action, but do **not** need a second `AgentGatewayEngine`-style mTLS session-authorization layer like Desktop Automation's.
**Why**: That layer exists specifically to authenticate a remote, separately-installed machine identity (the .NET Desktop Agent, running as its own Windows Service) — a problem Browser doesn't have, since its runtime is in-process with the Tauri app.
**Status**: ACCEPTED. See `browser_architecture.md` §4, `browser_capability_model.md` §7.

## 2026-09-25 — D6: `auth_method`, not `credential_requirement`, is where "Web Account" auth will be recorded

**Decision**: A future `auth_method` field on `AIProviderConfig` (not a repurposed `credential_requirement`, which is fixed provider-level metadata) will carry the per-tenant choice between `"api_key"` (default) and a future `"web_account"`.
**Why**: `credential_requirement` on `AIProviderMetadata` is declarative, provider-level, and fixed at construction for every current provider — reusing it for a per-tenant runtime choice would conflate two different concepts the audit found are currently, correctly, kept apart.
**Status**: ACCEPTED (design intent for B7; not implemented in B0/B1). See `browser_auth_architecture.md` §3.

## 2026-09-25 — D7: `unstable` Cargo feature must be enabled on `tauri` before B1 can use `add_child`

**Decision**: Browser-B1's first concrete step is adding `features = ["unstable"]` to the `tauri` dependency in `apps/desktop/src-tauri/Cargo.toml:16`.
**Why**: The Browser-B1 preflight verified directly against the cached `tauri-2.11.5` crate source that `Window::add_child()` (`window/mod.rs:1129`) — the exact API the embedded-child-webview architecture requires — is gated behind `#[cfg(any(test, all(desktop, feature = "unstable")))]`, and this repository's current manifest does not enable it. `tauri-runtime-wry`'s own `unstable = []` feature is empty (no new transitive dependencies), so this is a low-risk, one-line prerequisite, not a new dependency or new attack surface.
**Status**: ACCEPTED. Resolves OD-B3 (below). See `docs/architecture/browser_b1_preflight_report.md` §3, §10.

## 2026-09-25 — D8: Frontend integration point corrected — `defaultApps.ts`, not just a route behind the existing nav placeholder

**Decision**: Browser-B2 will remove the `browser` entry from `apps/desktop/src/shell/navigation/navConfig.ts`'s `business` group and add a corresponding entry to `DEFAULT_APPLICATIONS` in `apps/desktop/src/workspace/defaultApps.ts`, not merely wire a route to the existing disabled placeholder in place.
**Why**: The preflight found every `NAV_GROUPS` item is unconditionally rendered `disabled` in `AppSidebar.tsx:59` with no per-item enable flag; the live, routed application list is the separate `DEFAULT_APPLICATIONS` array, and `navConfig.ts`'s own header comment confirms "AI Studio" and "Marketplace" already made exactly this migration in M2.3.
**Status**: ACCEPTED. Supersedes `browser_architecture.md` §2.3's original B0-era wording. See `docs/architecture/browser_b1_preflight_report.md` §6.

## 2026-09-25 — D9: Raw WebView2 controller/environment access is already available without `unstable`

**Decision**: `Webview::with_webview()` → `PlatformWebview.controller()`/`.environment()` (gated only by the default-enabled `wry` feature, not `unstable`) is the mechanism Browser Policy and future permission-handling code should use for anything requiring direct `ICoreWebView2Controller`/`ICoreWebView2Environment` access, rather than adding a new native dependency.
**Why**: Verified directly against `tauri-2.11.5`'s source (`webview/mod.rs:180-193,1668`); this access happens Rust-side only and is never injected into a page's own JS context, so it does not by itself create a new bridge between web content and native code.
**Status**: ACCEPTED. See `docs/architecture/browser_b1_preflight_report.md` §3, §11.

## 2026-09-25 — D10: `browser.json` scopes via `webviews`, not `windows` — a load-bearing security finding

**Decision**: Browser-B1's new Tauri capability (`capabilities/browser.json`) grants its five `browser_*` commands via `"webviews": ["main"]`, not `"windows": ["main"]`.
**Why**: Direct inspection of `tauri_utils::acl::capability::Capability`'s own doc comment and `tauri::ipc::authority::RuntimeAuthority::resolve_access`'s actual matching logic confirmed that a `"windows"` match grants a capability's permissions to **every webview embedded in that window** — including any child browser surface `Window::add_child` creates there — regardless of the child's own distinct label. Using `"webviews"` instead scopes strictly to the webview literally labeled `"main"` (KORTEX's own trusted frontend), so a child surface (labeled `"browser-surface-<id>"`) cannot match by label at all.
**Independent second layer, verified separately**: `Origin::matches(&ExecutionContext)` (`tauri::ipc::authority`) unconditionally returns `false` for `(Origin::Remote{..}, ExecutionContext::Local)`. Since neither `default.json` nor `browser.json` declares a `remote` field, both resolve to `ExecutionContext::Local`, so **any** webview whose content came from `WebviewUrl::External` (a Remote origin — exactly what every browser surface always loads) is denied every command in either capability file regardless of window/webview label matching. This is Tauri's own built-in local-vs-remote trust boundary, not something Browser-B1 built.
**Consequence for `browser_security_model.md` §4**: this closes a meaningful part of what that section flagged as "must be designed fresh in B4" — the base native-bridge default-deny for arbitrary web content is already provided by Tauri's own ACL model, verified by direct source inspection, not merely assumed. What B4 still owns: per-origin/per-profile *allow-listing* for any future capability a page legitimately needs, and the Browser Policy enforcement point for `kortex.browser.*` governed actions (a separate system from this static ACL).
**Status**: ACCEPTED. See `docs/architecture/browser_b1_implementation_report.md` §Security.

## 2026-09-25 — D11: Browser-B1's `BrowserRuntime` trait omits back/forward navigation

**Decision**: The `BrowserRuntime` trait implemented in B1 has `create_surface`/`navigate`/`reload`/`query_state`/`destroy`/`destroy_all` — no `go_back`/`go_forward`.
**Why**: The task brief's own list of "possible responsibilities" explicitly permits omitting methods the real architecture doesn't support well; direct inspection of `tauri::Webview`'s stable API (`webview/mod.rs`) confirmed it exposes `navigate`/`reload`/`url`/`close`/`eval` but no session-history navigation at all. Implementing real back/forward would require raw `ICoreWebView2::GoBack`/`GoForward` COM calls via `with_webview()`, which is unjustified complexity for a "prove the runtime" milestone, and the task brief explicitly lists "history" under work to strictly defer.
**Status**: ACCEPTED. Revisit in Browser-B2 if the UI genuinely needs it before then.

## 2026-09-25 — D12: `tauri::test`/`MockRuntime` is not usable for automated Window/Webview tests in this environment

**Decision**: `browser_runtime.rs`'s tests cover pure logic only (profile-path sanitization, id/error serialization shapes) — no automated test constructs a live `Window`/`Webview`, mocked or real.
**Why**: Enabling `tauri`'s `test` Cargo feature (needed for `tauri::test::{mock_builder, MockRuntime}`) alongside the `unstable` feature this crate already requires reproducibly crashes every test binary in this crate at process startup (`STATUS_ENTRYPOINT_NOT_FOUND` / `0xC0000139`), confirmed via bisection: with only `unstable` enabled (no `test` feature), all 56 pre-existing tests in this crate pass unchanged, and the real, non-mock production binary (`cargo build`, no `test` feature at all) both compiles and launches successfully. The failure is therefore specific to the `test`+`unstable` feature combination in this environment; its exact root cause (a specific missing/mismatched Windows DLL export) was not further isolated within Browser-B1's scope.
**Status**: ACCEPTED as a known, explicitly-documented environment limitation — not silently worked around. See `docs/architecture/browser_b1_implementation_report.md` §Tests for the full accounting of what was and wasn't verified as a result, and `browser_known_limitations.md`.

## 2026-09-25 — D14: Post-adversarial-review fix — collision-resistant surface ids, plus a refuted deadlock claim

**Decision**: `BrowserSurfaceId::generate()` now appends a process-lifetime-monotonic `AtomicU64` sequence to its nanosecond timestamp, making an id collision unreachable within one process's lifetime regardless of clock granularity. `create_surface`'s `AlreadyExists` branch (now normally unreachable) still closes the orphaned webview before returning, as defense-in-depth.
**Why**: an independent adversarial review of the Browser-B1 diff found that a colliding id would previously have dropped the newly-created, already-embedded webview without ever closing it — never inserted into `surfaces`, so `destroy`/`destroy_all` could never reach it either. A real, if previously practically-unreachable, resource leak.
**Also investigated and refuted by the same review round**: a concern that `destroy_all()` — called synchronously from `CloseRequested`/`ExitRequested`, both confirmed to run on the main/event-loop thread — might deadlock the same way `WebviewBuilder`'s creation path can. Direct inspection of `tauri-runtime-wry-2.11.4 src/lib.rs:1712-1721,235-255` shows `Webview::close()` routes through `send_user_message`, which executes inline and returns immediately when already on the main thread — architecturally distinct from `add_child`'s blocking `run_on_main_thread` pattern, which exists specifically for WebView2's asynchronous COM controller-creation callback. No deadlock risk exists in the shutdown path; no code change was made there.
**Status**: ACCEPTED. Two new tests (`browser_surface_id_generate_never_collides_across_many_sequential_calls`, `..._across_concurrent_threads`) verify the id-generation fix directly, without requiring the `tauri::test` harness that crashes in this environment (D12). `cargo test --lib`: 66 passed (was 64), 0 failed, 2 ignored — zero regressions.

## 2026-09-25 — D15: Back/forward and real loading state require raw WebView2 COM, gated behind `#[cfg(windows)]`

**Decision**: `BrowserRuntime::go_back`/`go_forward` and `BrowserSurfaceState::loading`/`can_go_back`/`can_go_forward` are implemented via direct `ICoreWebView2` COM calls (`GoBack`/`GoForward`/`CanGoBack`/`CanGoForward`, and `NavigationStarting`/`NavigationCompleted` event handlers for `loading`), reached through `Webview::with_webview()`. `webview2-com` and `windows` were promoted from transitive to direct dependencies (same already-resolved versions, no new crate/version enters `Cargo.lock`) so this module's own code can name their types. Every use is `#[cfg(windows)]`-gated with a `#[cfg(not(windows))]` fallback (`go_back`/`go_forward` return an explicit "not supported" error; `loading`/`can_go_back`/`can_go_forward` stay at their safe defaults) so the crate keeps compiling on the non-Windows targets this repository's own CI (`rust` job, `ubuntu-latest`) already builds against.
**Why**: Browser-B2's toolbar requires real Back/Forward/loading-indicator behavior (explicitly listed as "Required"), and wry's stable `Webview` API (used for `navigate`/`reload`/`url`/`close` since Browser-B1) has no session-history or navigation-event surface at all — confirmed by direct inspection of the pinned `tauri-2.11.5` source, not assumed. `loading` is deliberately event-driven (flipped by real `NavigationStarting`/`NavigationCompleted` callbacks), not guessed from whether a command was merely dispatched, since dispatch success says nothing about whether the resulting page load has finished.
**Status**: ACCEPTED. This is Browser-B2's highest-risk, least-verified new code — see `docs/architecture/browser_b2_implementation_report.md` TESTS section for the honest accounting (compiles and passes `cargo check`/`clippy` on Windows; cross-compilation to the Linux target CI actually uses could not be exercised from this Windows development machine — see OD-B6).

## 2026-09-25 — D16: Tab switching reuses `set_bounds`, not a new "active surface" concept

**Decision**: An inactive Browser-B2 tab's surface is parked at a fixed off-screen `SurfaceBounds` (not destroyed, not hidden via a new runtime primitive) by calling the same `set_bounds` operation used for window-resize tracking; the active tab's surface is moved into the real content-area rect the same way.
**Why**: Satisfies the task brief's "every visible tab must correspond to a real browser runtime surface, do not fake browser functionality" requirement without introducing a second concept (`show`/`hide`/`is_active`) into `BrowserRuntime` — `set_bounds` already does everything needed. Switching tabs never reloads or loses a tab's state, unlike destroy-and-recreate would.
**Status**: ACCEPTED. See `browser_capability_model.md`/`browser_architecture.md` for the updated abstraction surface.

## 2026-09-25 — D17: Post-adversarial-review fixes to `useBrowserTabs` — two real orphan-surface defects closed, one error-swallowing narrowed

**Decision**: Three fixes to `apps/desktop/src/features/browser/hooks/useBrowserTabs.ts`, all localized, none touching `BrowserRuntime`/WebView2/COM architecture:
1. **Unmount-during-create**: `openTab` now checks a new `isMountedRef` immediately after `createBrowserSurface` resolves; if the component has already unmounted, the just-created surface is destroyed immediately instead of being silently untracked. `isMountedRef.current = false` is set synchronously inside the existing unmount-cleanup effect, before that effect destroys everything already tracked — ordering that's guaranteed correct because a synchronous React commit-phase cleanup always completes before any pending promise's continuation gets a turn on the microtask queue.
2. **Destroy-failure untracking**: `closeTab` only removes a tab from `tabs`/`tabsRef.current` on `destroyBrowserSurface`'s *success* path now — a failed destroy leaves the tab (and its real `BrowserSurfaceId`) tracked and visible, so the user can retry Close rather than the surface becoming permanently unreachable.
3. **`refreshTabState`'s error swallowing**: narrowed from a bare `catch {}` to checking `isBrowserRuntimeError(err) && err.kind === "surfaceNotFound"` — the one case that should stay silent (the surface was already destroyed). Any other failure now surfaces via the existing `error` state, the same path every other operation's failure already uses. No new error taxonomy was introduced: `BrowserRuntimeError`'s existing `surfaceNotFound`/`platform`/`alreadyExists` union already made this distinction possible.
**Why**: found by an independent adversarial review of the Browser-B2 diff (frontend agent) — both (1) and (2) are real, timing-dependent ways to break the "no orphaned browser runtime" invariant this project has stated since Browser-B1; (3) was correctly scoped as fixable *with the existing error model*, per the review task's own explicit instruction not to invent a larger error hierarchy for B2.
**Status**: ACCEPTED. Four new tests added (`BrowserApp.test.tsx`) reproduce each scenario directly: unmount-before-create-resolves → verify destroy is called; destroy-rejects → verify the tab stays tracked, then a retried destroy succeeds normally; a genuine refresh failure surfaces an error; a `surfaceNotFound` refresh failure does not. `cargo test --lib` unaffected (68 passed, unchanged — this was a frontend-only fix); full frontend suite re-run after the fix.
**Not fixed, by design**: the raw-COM first-navigation `loading` race the same review round identified (Rust-side, cosmetic, first-load-only) — explicitly out of scope for this fix pass per the task brief, remains a documented known limitation.

## Open items pending owner decision

- **OD-B1**: Whether `IKortexBrowserRuntime`'s content-read primitive permits any form of constrained script execution, or must be strictly declarative (DOM/text read only) — affects both B1's interface shape and B4's native-bridge policy. Not decided in B0.
- **OD-B2**: Whether Browser-B10 readiness folds into the main KORTEX `RELEASE_CANDIDATE_READINESS.md` process or remains an independently-versioned track. Deferred explicitly to Browser-B10 (see `browser_roadmap_b0_b10.md`).
- ~~**OD-B3**: Whether the Tauri version currently pinned in this repository supports true multi-webview/child-webview embedding...~~ **RESOLVED by the Browser-B1 preflight (2026-09-25) — see D7.** Confirmed supported, pending the `unstable` feature flag.
- ~~**OD-B4**: Whether Tauri v2's capability-file ACL schema can scope permissions to a specific child-webview label...~~ **RESOLVED by Browser-B1 implementation (2026-09-25) — see D10.** Confirmed: `"webviews"` scopes to a specific webview label independently of `"windows"`.
- **OD-B5**: Root-cause the `STATUS_ENTRYPOINT_NOT_FOUND` crash that blocks `tauri`'s `test` feature + `unstable` feature from coexisting in an automated test binary on this development machine (D12), and restore automated `Window`/`Webview` lifecycle test coverage. Not resolved in Browser-B1; recommended before Browser-B3 adds meaningfully more runtime logic that would benefit from the same kind of test.
- **OD-B6**: Confirm Browser-B2's new `#[cfg(not(windows))]` fallback code (`browser_runtime.rs`'s non-Windows `go_back`/`go_forward`/`navigation_capability`/`register_navigation_loading_handlers`) actually compiles under `cargo check` on the Linux target this repository's own `rust` CI job uses. Attempted from this Windows development machine via `cargo check --target x86_64-unknown-linux-gnu`; the attempt failed before even reaching this crate's own code, on a pre-existing, unrelated system-library gap (`glib-sys`/`gobject-sys` build scripts require `pkg-config`/Linux system headers not present on Windows — the same dependency chain `desktop-ci.yml`'s `rust` job installs via `apt-get install libwebkit2gtk-4.1-dev ...` on its real Ubuntu runner). The fallback code was reviewed manually instead (it is deliberately trivial — a no-op and two functions returning fixed safe defaults) and is believed correct, but this was not confirmed by an actual compiler run on that target. First real confirmation will be this repository's own CI, the next time this branch is pushed.
