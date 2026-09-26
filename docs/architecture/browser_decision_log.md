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

## 2026-09-26 — D18: `BrowserProfileStore` is a new construct, alongside (not inside) the four existing storage facades

**Decision**: Browser-B3's tenant-scoped profile identity/storage/locking lives entirely in a new module, `browser_profile_store.rs`, never folded into `IDataStore`/`IFileStore`/`IObjectStore`/`ICacheStore`.
**Why**: direct source inspection during the B3 architecture gate (confirmed again during implementation) found none of the four existing facades has any per-tenant or exclusive-directory concept in its contract — `IFileStore`/`IObjectStore` are keyed by a single shared `base_directory`/bucket, `ICacheStore` is a flat keyspace. Forcing this problem into one of them would contort an abstraction designed for a different shape of problem.
**Status**: ACCEPTED. Implemented.

## 2026-09-26 — D19: Profile identity — `BrowserProfileId` is CSPRNG-random; `tenant_id` reuses the existing plain-`str` convention

**Decision**: `BrowserProfileId::generate()` produces 128 bits of `getrandom`-sourced randomness (hex-encoded, `profile-` prefixed) — never a timestamp, never derived from `display_name`. `tenant_id` is passed and stored as a plain `String`/`&str` throughout, matching every existing KORTEX identity type (`SecurityPrincipal.tenant_id`, `CapabilityExecutionContext.tenant_id`) — no new `TenantId`/`UserId` newtype was introduced, since none exists anywhere else in this codebase to be consistent with.
**Why**: `BrowserSurfaceId`'s existing nanosecond-timestamp-plus-counter scheme is sufficient only because that id is in-memory-only for one process's lifetime; a PERSISTED, tenant-boundary-adjacent id needs real unguessability. A grep across `backend/src/kortex` confirmed zero existing `TenantId`/`UserId` newtype to match.
**Status**: ACCEPTED. Implemented; unit-tested for uniqueness (10,000 sequential + 16,000 concurrent generations, zero collisions) and entropy (exact expected length).

## 2026-09-26 — D20: Storage hierarchy — no separate `registry.json`; `list_profiles` scans the tenant directory directly

**Decision** (deviates from the architecture gate's originally-sketched layout): the final on-disk shape is `<profiles_root>/<tenant_id>/<profile_id>/{profile.json, .lock, webview2-data/}` — with **no** tenant-level `registry.json` index file. `list_profiles` reads the tenant directory's immediate subdirectories and each one's own `profile.json` directly.
**Why**: a separate index file duplicating what's already in each profile's own `profile.json` creates a real dual-write consistency hazard (registry says X, that profile's own file disagrees, after a partial write) for a negligible performance win at the realistic scale of "a handful to a few dozen profiles per tenant." Eliminating it removes an entire class of bug the architecture gate's own adversarial checklist worried about, at essentially zero cost.
**Status**: ACCEPTED. Implemented and tested (`create_profile_makes_it_visible_and_available_via_list_profiles`, `profiles_created_under_one_tenant_are_invisible_to_another_tenant`, etc.).

## 2026-09-26 — D21: Profile locking — KORTEX's own PID-liveness lock is primary; WebView2's own folder-exclusivity is defense in depth

**Decision**: `acquire_profile_lock` uses `OpenOptions::create_new(true)` for the uncontended path (a single atomic OS call, no TOCTOU window) and an atomic rename-over for stale-lock recovery (a dead PID, confirmed via `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`, or a malformed lock file). A live-process OD-B9 preflight (below) confirmed WebView2 itself also refuses a second live process opening an already-open user-data folder — that native behavior is treated as a second, independent backstop, never the primary mechanism, since it produces an opaque COM error rather than a clean, typed `ProfileLocked`.
**Status**: ACCEPTED. Implemented and tested, including a genuine cross-process contention test (a real spawned child process holding the lock) and a genuine stale-lock recovery test (a real spawned-then-reaped child process's now-dead PID).

## 2026-09-26 — D22 (verified + implemented): WebView2 password/autofill hardening

**Decision**: every browser surface calls `ICoreWebView2Settings4::SetIsPasswordAutosaveEnabled(false)`/`SetIsGeneralAutofillEnabled(false)` at creation, reached via `ICoreWebView2::Settings().cast::<ICoreWebView2Settings4>()`.
**Verification**: confirmed against the pinned `webview2-com-sys-0.38.2` bindings before implementation (both methods are real, generated bindings — not guessed), and confirmed LIVE via a temporary preflight harness that read the settings back immediately after setting them on a real WebView2 instance: `Ok((false, false))` — proving the setting actually took effect, not merely that the setter call didn't error. The harness was removed after verification; it never entered the final implementation.
**Status**: ACCEPTED. Implemented (`browser_runtime.rs::disable_password_and_autofill`).

## 2026-09-26 — D23: Interim local structured audit logging, not a new backend capability

**Decision**: Browser-B3's required `PROFILE_*` lifecycle audit events are written to a local, append-only, JSON-Lines file (`<profiles_root>/audit.log`) — lifecycle metadata only (event, timestamp, tenant id, profile id, result, a short reason), never secrets/cookies/tokens/page content. No new `kortex.browser.*` backend capability was added.
**Why**: Browser IPC commands have no backend hop at all today (`browser_architecture.md` §2.3) — adding one now, even narrowly scoped to audit-only, would mean registering the first-ever `kortex.browser.*` backend capability ahead of schedule, which is explicitly Browser-B5's job. A narrow one-off capability now risks needing reconciliation with B5's real design later. This decision was surfaced to, and approved by, the project owner before implementation.
**Status**: ACCEPTED as an explicit interim mechanism, to be superseded when Browser gains a real capability-dispatcher hop. Implemented and tested (9 dedicated tests, including a negative test confirming no secret-like field names ever appear in the log).

## 2026-09-26 — D24: Profile switching closes every tab, then opens exactly one fresh tab against the new profile

**Decision**: `useBrowserTabs(profileId)` reacts to `profileId` changing (after the very first profile, which just opens one initial tab) by closing every currently-open tab, then opening exactly one new tab against the newly active profile. No mixed-profile tab set exists in the UI at any point.
**Why**: WebView2 gives no way to re-point an already-created surface's `data_directory` at a different profile — confirmed live in the OD-B9 preflight (`step3_close_surface_a`/`step4_recreate_same_dir_after_close`, both `Ok`) — so "switch profile" can only mean "new tabs from here on use the new profile." Matches D16's own precedent of reusing existing primitives over inventing a new runtime concept.
**Status**: ACCEPTED. Implemented and tested (`switching profiles closes every existing tab and opens exactly one fresh tab against the new profile`).

## 2026-09-26 — D25: OD-B7 resolved — tenant identity captured from the login/refresh response's own already-disclosed payload

**Decision**: `ipc.rs`'s `IpcClientState` gained an in-memory-only `tenant_id: Mutex<Option<String>>`, populated exclusively inside `forward_capability_request`'s existing "was a session token just minted" branch — reading `payload.result.tenant_id` from the SAME response that already carries `sessionToken` (confirmed via direct inspection of `backend/src/kortex/api/main.py::_invoke`: a minted token always accompanies a `SecurityPrincipal` in `payload.result`). Never gated on `capability_name` (this module's own stated design rule). Replaced unconditionally on every mint — never merged — so a session replacement can never leave a stale tenant_id behind. Cleared whenever `clear_token()` runs (the sole path both call sites of which are genuine logout/session-end points).
**Why this, not client-side token decoding**: `backend/src/kortex/api/token_codec.py`'s own doc comment states, citing `phase3_desktop_architecture.md` §3 principle 2, that "the Tauri/Rust layer must never evaluate business rules" — ruling out having Rust decode/verify the session token itself to extract claims, even though the token IS a real Ed25519-signed credential. The chosen bridge reads a field the backend ALREADY discloses to the (less-trusted) frontend in the same response, mirroring exactly how this module already custodies `sessionToken`/`refreshToken` — no new trust mechanism, no backend change.
**Why not a new backend capability**: considered and explicitly rejected by the project owner in favor of this option — a dedicated `whoami`-style capability would also have worked, but the payload already contains the needed field, making a new capability unnecessary surface area.
**Status**: ACCEPTED. Implemented and tested (6 dedicated tests: capture, replacement/no-staleness, logout clearing, untouched-by-ordinary-calls, cleared-not-stale-on-malformed-payload, and proof that a caller-supplied `tenant_id` in request parameters has zero effect).

## 2026-09-26 — D26: Windows ACL restriction via `icacls`, not raw Win32 SID/DACL construction

**Decision**: `create_profile` shells out to `icacls.exe` (`/inheritance:r /grant:r "<user>:(OI)(CI)F"`) to restrict each new profile directory to the current OS user, rather than hand-rolling `SetNamedSecurityInfoW`/SID construction via the already-available `windows-sys` `Win32_Security` bindings.
**Why**: this is an explicit BASELINE requirement (full AppContainer-SID isolation, matching `python_exec/windows_boundary.py`'s stronger model, is explicitly out of scope for B3). A subtly-wrong hand-rolled unsafe ACL construction could silently produce an INSECURE result — worse than no custom ACL code at all — while `icacls` is a standard, battle-tested Windows tool doing exactly this one job. Fails closed: `create_profile` removes the just-created directory if the ACL step fails, never leaving an unprotected profile directory behind.
**Status**: ACCEPTED. Implemented and verified LIVE (not just "the call returned Ok") — a real, subsequent read-only `icacls` listing of a created profile directory confirms the current user holds full control and no inherited broad grant (`Everyone`/`\Users:`) remains.

## 2026-09-26 — D27: A pre-existing, unrelated defect was found and flagged, not fixed, in this session

**Finding**: while building `BrowserProfileError` (whose `#[serde(tag = "kind", rename_all = "camelCase")]` enum attribute was empirically confirmed NOT to cascade into a struct-variant's own field names — a real serde behavior, not a KORTEX bug), the identical pattern was recognized in the already-shipped `browser_runtime::BrowserRuntimeError` (`SurfaceNotFound { surface_id }`/`AlreadyExists { surface_id }`), meaning those fields likely serialize as `surface_id` (snake_case) rather than the `surfaceId` the frontend's TypeScript type expects.
**Action taken**: NOT fixed in this session (out of Browser-B3's scope — `browser_runtime.rs`'s existing, already-shipped error type is unrelated to profile work). Flagged as a follow-up task (`task_535b1698`, "Fix BrowserRuntimeError field-casing bug"). `BrowserProfileError` itself was fixed correctly before shipping (explicit `#[serde(rename = "profileId")]` per field).
**Status**: DOCUMENTED, not remediated — a deliberate scope decision, not an oversight.

## Open items pending owner decision

- **OD-B1**: Whether `IKortexBrowserRuntime`'s content-read primitive permits any form of constrained script execution, or must be strictly declarative (DOM/text read only) — affects both B1's interface shape and B4's native-bridge policy. Not decided in B0.
- **OD-B2**: Whether Browser-B10 readiness folds into the main KORTEX `RELEASE_CANDIDATE_READINESS.md` process or remains an independently-versioned track. Deferred explicitly to Browser-B10 (see `browser_roadmap_b0_b10.md`).
- ~~**OD-B3**: Whether the Tauri version currently pinned in this repository supports true multi-webview/child-webview embedding...~~ **RESOLVED by the Browser-B1 preflight (2026-09-25) — see D7.** Confirmed supported, pending the `unstable` feature flag.
- ~~**OD-B4**: Whether Tauri v2's capability-file ACL schema can scope permissions to a specific child-webview label...~~ **RESOLVED by Browser-B1 implementation (2026-09-25) — see D10.** Confirmed: `"webviews"` scopes to a specific webview label independently of `"windows"`.
- **OD-B5**: Root-cause the `STATUS_ENTRYPOINT_NOT_FOUND` crash that blocks `tauri`'s `test` feature + `unstable` feature from coexisting in an automated test binary on this development machine (D12), and restore automated `Window`/`Webview` lifecycle test coverage. Not resolved in Browser-B1, B2, or B3 (most of B3's new logic is pure-Rust-testable and doesn't need it, but full live-profile-lifecycle coverage still does). Recommended before Browser-B4 adds meaningfully more runtime logic.
- **OD-B6**: Confirm Browser-B2's new `#[cfg(not(windows))]` fallback code (`browser_runtime.rs`'s non-Windows `go_back`/`go_forward`/`navigation_capability`/`register_navigation_loading_handlers`) actually compiles under `cargo check` on the Linux target this repository's own `rust` CI job uses. Attempted from this Windows development machine via `cargo check --target x86_64-unknown-linux-gnu`; the attempt failed before even reaching this crate's own code, on a pre-existing, unrelated system-library gap (`glib-sys`/`gobject-sys` build scripts require `pkg-config`/Linux system headers not present on Windows — the same dependency chain `desktop-ci.yml`'s `rust` job installs via `apt-get install libwebkit2gtk-4.1-dev ...` on its real Ubuntu runner). The fallback code was reviewed manually instead (it is deliberately trivial — a no-op and two functions returning fixed safe defaults) and is believed correct, but this was not confirmed by an actual compiler run on that target. Still not resolved as of Browser-B3, whose own new `#[cfg(not(windows))]` code (`pid_is_alive`, `restrict_to_current_user`) carries the identical caveat. First real confirmation will be this repository's own CI, the next time this branch is pushed.
- **OD-B7 (Browser-B3)**: ~~Whether a plain-Tauri-IPC Rust process can obtain a trustworthy current tenant_id...~~ **RESOLVED — see D25.**
- **OD-B9 (Browser-B3)**: ~~Whether distinct WebView2 profile directories can coexist simultaneously in one process, and whether destroy-then-recreate against the same directory works...~~ **RESOLVED via a live preflight — see D21/D24.** All four steps (`create A`, `create B (distinct dir, A still alive)`, `close A`, `recreate against A's directory`) returned `Ok`.
- **OD-B10 (Browser-B3, new)**: Disk-quota/exhaustion protection against a hostile page filling a profile's WebView2 storage has no existing KORTEX precedent anywhere and was not implemented in B3 (explicitly out of scope — the architecture gate flagged this as a high-residual-risk deferral, not a B3 deliverable). Remains open for a future phase.
- **OD-B11 (Browser-B3, new)**: Encryption-at-rest for profile directories has no existing precedent for an opaque WebView2 folder and was not implemented in B3 (explicitly out of scope per the architecture gate). Remains open for a future phase if ever required.
- **OD-B12 (Browser-B3, new)**: Full AppContainer-SID isolation for profile directories (matching `python_exec/windows_boundary.py`'s stronger model) was explicitly scoped OUT of B3 in favor of the baseline `icacls`-based OS-user restriction (D26). Remains open as a future hardening pass if this risk class ever warrants it.
