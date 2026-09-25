# KORTEX Browser — Browser-B2 Implementation Report

## Browser UI

PHASE: B2 — Browser UI
STATUS: **COMPLETE**
Date: 2026-09-25

---

## OBJECTIVE

Turn the Browser-B1 runtime foundation into a usable, human-facing browser application inside KORTEX: a toolbar (back/forward/reload/address bar/loading state), a real tab bar, and a content area that tracks the WebView2 surface's position/size — without becoming an AI-browser, provider-authentication, or automation phase.

---

## USER EXPERIENCE

- **Toolbar**: Back, Forward, Reload, an address field, a Go/submit action, and a real (event-driven) loading indicator — `apps/desktop/src/features/browser/components/BrowserToolbar.tsx`.
- **Tabs**: a tab bar with a "+" new-tab control and a close control per tab — `BrowserTabBar.tsx`. Every tab is a real `BrowserSurfaceId`; there is no frontend-only placeholder tab (task brief requirement).
- **Content area**: the div the active tab's real WebView2 surface is positioned over, tracked via `ResizeObserver` (event-driven, not polling).
- **Application integration**: unchanged from Browser-B1 — still the `browser` entry in `DEFAULT_APPLICATIONS`, following the existing workspace/application convention. No parallel application framework was introduced.

---

## ARCHITECTURE

```
KORTEX
   ↓
Browser Application       (apps/desktop/src/workspace/defaultApps.ts — unchanged from B1)
   ↓
Browser UI                 (BrowserApp → BrowserTabBar + BrowserToolbar + content area)
   ↓
useBrowserTabs             (apps/desktop/src/features/browser/hooks/useBrowserTabs.ts — the only place tab state lives)
   ↓
BrowserRuntime             (trait, extended: + go_back / go_forward / set_bounds)
   ↓
WebView2RuntimeAdapter     (extended: raw ICoreWebView2 COM for back/forward/loading, Windows-only, cfg-gated)
```

`BrowserRuntime` gained exactly three new methods (`go_back`, `go_forward`, `set_bounds`) and `BrowserSurfaceState` gained three new fields (`loading`, `canGoBack`, `canGoForward`) — no other shape change. No WebView2/wry/COM type crosses the trait boundary; every new call is contained inside `WebView2RuntimeAdapter`'s `impl` block and two small `#[cfg(windows)]`-gated free functions (`with_core_webview2`, `navigation_capability`), exactly matching Browser-B1's own module-boundary rule.

**Tabs without a new runtime concept**: rather than adding a `show`/`hide`/`is_active` primitive, an inactive tab's surface is parked at a fixed off-screen `SurfaceBounds` using the *same* `set_bounds` operation the content-area resize tracker uses — see `docs/architecture/browser_decision_log.md` D16.

---

## IMPLEMENTATION

### Rust (`apps/desktop/src-tauri`)
- **`Cargo.toml`**: `webview2-com = "0.38"` and `windows = "0.61"` added under `[target.'cfg(windows)'.dependencies]` — both already present in `Cargo.lock` transitively, at these exact versions, via `tauri`'s own `wry` feature. **No new crate or version entered the dependency graph**; this only promotes existing transitive dependencies to direct ones so this crate's own code can name their types.
- **`src/browser_runtime.rs`** (extended, ~460 → ~640 lines): `SurfaceBounds` (new, `Deserialize`-only — frontend → Rust); `BrowserSurfaceState` gained `loading`/`can_go_back`/`can_go_forward`; `SurfaceEntry<R>` (new — bundles the webview handle with a `loading: Arc<AtomicBool>`, replacing the old bare `Webview<R>` as the registry's map value); `BrowserRuntime::go_back`/`go_forward`/`set_bounds` (new trait methods); `WebView2RuntimeAdapter::cloned_surface` (new helper — clones a webview+loading handle out of the registry, needed because `with_webview`'s closure must be `'static`); `register_navigation_loading_handlers` (new, `#[cfg(windows)]`/`#[cfg(not(windows))]` pair — registers `NavigationStarting`/`NavigationCompleted` COM event handlers at surface-creation time); `with_core_webview2`/`navigation_capability` (new, `#[cfg(windows)]`/`#[cfg(not(windows))]` pairs — the channel-based synchronous COM-call helper, and the `CanGoBack`/`CanGoForward` reader).
- **`src/lib.rs`**: registers 3 new commands (`browser_go_back`, `browser_go_forward`, `browser_set_bounds`) in `generate_handler!`.
- **`capabilities/browser.json`** + **`permissions/browser-runtime.toml`**: 3 new permission entries, same `"webviews": ["main"]` scope, still no `remote` field.

### Frontend (`apps/desktop/src/features/browser`)
- **`api.ts`** (extended): `SurfaceBounds`/`BrowserSurfaceState` (with the 3 new fields) + `goBackBrowserSurface`/`goForwardBrowserSurface`/`setBrowserSurfaceBounds` wrappers, same direct-`invoke()` convention as Browser-B1.
- **`hooks/useBrowserTabs.ts`** (new): tab array + active-tab id state, the `ResizeObserver`-driven bounds tracker, open/close/switch/navigate/back/forward/reload operations, the initial-tab auto-open (guarded against React 18 StrictMode double-invocation), and the unmount cleanup that destroys every open tab's surface.
- **`components/BrowserToolbar.tsx`** (new): the toolbar, plus `normalizeAddress()` — a small, exported, pure address-normalization function (bare host → `https://`; rejects anything that isn't `http`/`https` after normalization, including custom schemes like `kortex-auth://`/`tauri://`/`javascript:`/`file:` — no search-engine fallback, matching the task brief's explicit deferral).
- **`components/BrowserTabBar.tsx`** (new): the tab strip.
- **`components/BrowserApp.tsx`** (rewritten): now a thin orchestrator wiring `useBrowserTabs` to the toolbar/tab bar/content area — no direct API calls of its own.

No repository-wide URL-validation utility was found to reuse (searched first, per the task brief's instruction); `normalizeAddress` is scoped to this feature, not proposed as a shared utility.

---

## SECURITY

Unchanged boundary from Browser-B1, re-verified after this phase's changes:
- `capabilities/browser.json` still scopes via `"webviews": ["main"]`, never `"windows"` — confirmed by direct inspection (zero `"windows"` occurrences in the file).
- No `remote` field was added — every new command remains denied to any `Remote`-origin (externally-loaded) content by Tauri's own Local/Remote ACL check, independent of label matching (Browser-B1 D10's mechanism, unchanged).
- The 3 new permission identifiers in `permissions/browser-runtime.toml` exactly match the 3 new command names, which exactly match the 3 new `#[tauri::command]` definitions and the 3 new entries in `lib.rs`'s `generate_handler!` — cross-checked by direct grep across all four files, not assumed.
- No filesystem, shell, or arbitrary native IPC access was introduced. `set_bounds` only repositions/resizes an already-created surface; it grants no capability over any other surface.
- No JavaScript → native privileged bridge was created — every raw COM call this phase adds lives entirely inside Rust (`with_webview`'s closure runs on the WebView2 COM apartment thread, never inside the page's own JS context).

---

## NAVIGATION BEHAVIOR

- **URL input / Navigate**: `normalizeAddress()` accepts a well-formed `http(s)` URL as typed, or a bare host (normalized to `https://`); anything that doesn't resolve to an `http`/`https` URL — including a custom scheme — is rejected with a visible `aria-invalid` state, never silently guessed at or sent to a search engine.
- **Back / Forward**: real `ICoreWebView2::GoBack`/`GoForward`, gated on `BrowserSurfaceState.canGoBack`/`canGoForward` (also real, read fresh via `ICoreWebView2::CanGoBack`/`CanGoForward` on every `query_state` call).
- **Reload**: unchanged from Browser-B1 (`Webview::reload()`).
- **Loading indicator**: real, event-driven — flips `true` on `NavigationStarting`, `false` on `NavigationCompleted`, never inferred from whether a command was merely dispatched.

---

## RESIZE / LIFECYCLE VERIFICATION

- **Mechanism**: `useBrowserTabs`' `ResizeObserver` on the content-area `<div>`, plus a `window resize` listener as a backstop for position-only shifts the observer wouldn't catch — event-driven, not a polling loop (per the task brief's explicit instruction).
- **Initial dimensions**: applied immediately on mount/tab-open via a direct `applyActiveBounds()` call, not waiting for the first observer callback.
- **Tab switching**: verified via component test — switching tabs calls `set_bounds` for *both* the newly-active surface (into the real rect) and every other open tab (to the off-screen park position), and never calls `destroy` on either.
- **Repeated open/close**: exercised by the "opens a second tab" and "closes a tab" component tests; the Rust-side repeated-create/destroy-cycle test from Browser-B1 (`repeated_create_and_destroy_cycles_leave_no_surfaces_behind`, still present, unaffected by this phase's changes) continues to pass.
- **Close / unmount**: `useBrowserTabs`' unmount effect destroys every open tab's surface — verified by a component test asserting `destroyBrowserSurface` is called for every tab that was ever opened, not just the active one.
- **What was not verified**: real workspace/window resize against an actual OS window, and a real maximize event — both require the live desktop app; `getBoundingClientRect()` in `jsdom` (the test environment) always returns zeros, so the component tests prove *that* `set_bounds` is called with the right surface id at the right moments, not that the resulting coordinates are visually correct on a real screen.

---

## POST-REVIEW FIXES (2026-09-25)

An independent adversarial review of this phase's diff (two parallel tracks: raw WebView2 COM code, and the frontend tab-lifecycle hook) returned **PASS WITH NON-BLOCKING NOTES**. The COM code held up completely — all nine targeted safety questions (event handler lifetime, thread/apartment correctness, `with_core_webview2`'s deadlock/UAF exposure, `#[cfg(windows)]` completeness, error propagation, and the exact binding signatures for `CoreWebView2()`/`GoBack`/`GoForward`/`CanGoBack`/`CanGoForward`/the navigation event handlers) were verified against the actual pinned `webview2-com-sys-0.38.2` bindings and confirmed safe — no code change resulted from that track. The frontend track found two genuine, timing-dependent defects, both now fixed in `apps/desktop/src/features/browser/hooks/useBrowserTabs.ts`; see `docs/architecture/browser_decision_log.md` D17 for the full rationale:

1. **Unmount-during-create orphan** — fixed via a new `isMountedRef`, checked immediately after `createBrowserSurface` resolves.
2. **Destroy-failure untracking** — fixed by moving tab removal in `closeTab` to the success path only.
3. **`refreshTabState`'s bare `catch {}`** — narrowed to swallow only `BrowserRuntimeError`'s existing `surfaceNotFound` kind; any other failure now surfaces normally. No new error taxonomy was introduced — the existing `BrowserRuntimeError` union already supported this distinction.

**Not fixed, by design, per the review-fix task's own scope**: the raw-COM first-navigation `loading` race (Rust-side, cosmetic, first-load only) the same review round identified — remains a documented known limitation, not touched.

## TESTS

### Rust (`apps/desktop/src-tauri`) — `cargo test --lib`
**68 passed, 0 failed, 2 ignored** (56 from before Browser-B1 + 10 from Browser-B1 + 2 new: `surface_bounds_deserializes_from_the_frontends_camel_case_shape`, `browser_surface_state_serializes_every_field_in_camel_case`). Zero regressions at any point.

**Honest gap, larger than Browser-B1's**: the new raw WebView2 COM code (`GoBack`/`GoForward`/`CanGoBack`/`CanGoForward`, the `NavigationStarting`/`NavigationCompleted` handlers) has **no automated test** — same OD-B5 environment limitation (`tauri::test`'s `MockRuntime` crashes alongside `unstable`), compounded by the fact that even a working mock runtime would not provide a real WebView2 COM object graph to call `GoBack` against. This code was verified by: (1) `cargo check`/`cargo build` succeeding against the *actual* generated `webview2-com-sys` bindings for this exact pinned version (not guessed signatures — `CanGoBack`/`CanGoForward`'s out-pointer parameter shape, `CoreWebView2()`'s getter, `add_NavigationStarting`/`add_NavigationCompleted`'s generic-parameter shape were all confirmed against `webview2-com-sys-0.38.2/src/bindings.rs` before writing the calling code), (2) `cargo clippy` producing zero new warnings despite the `unsafe` blocks, (3) manual review. It was **not** verified by executing any of it.

### Rust — `#[cfg(not(windows))]` fallback path
Attempted `cargo check --target x86_64-unknown-linux-gnu` from this Windows development machine. Failed before reaching this crate's own code, on a pre-existing, unrelated system-library gap (`glib-sys`/`gobject-sys` — transitive `webkit2gtk` build-script dependencies — require `pkg-config`/Linux system headers this Windows machine does not have; the same gap `desktop-ci.yml`'s `rust` job resolves by installing `libwebkit2gtk-4.1-dev` etc. on a real Ubuntu runner). The fallback functions were reviewed manually instead — each is a few lines (a no-op, or a function returning a fixed safe default) — and are believed correct, but this was **not** confirmed by an actual compiler run on that target. See `browser_decision_log.md` OD-B6.

### Rust — `cargo clippy --lib`
1 pre-existing warning (`sidecar.rs`, unrelated, unchanged since Browser-B1); 0 new.

### Frontend (`apps/desktop`) — `pnpm typecheck` and `pnpm test`
- `tsc --noEmit`: clean.
- `vitest run` (full suite): **834 passed, 0 failed, across 101 test files** (was 809/99 before this phase's initial implementation, 830/101 before the post-review fixes below). New since the initial implementation: `BrowserToolbar.test.tsx` (11 tests, including 6 direct `normalizeAddress` unit tests), `BrowserTabBar.test.tsx` (7 tests), and a rewrite plus 4 additional tests in `BrowserApp.test.tsx` (13 tests total) covering initial-tab auto-open, navigation, back/forward/reload, new-tab, tab-switching's off-screen-parking behavior, tab-close, error display, unmount cleanup, and — added in the post-review fix pass — the two orphan-surface regression tests (defects 1 and 2) plus the narrowed-error-handling tests (defect 3).
- **Two real test-authoring bugs were found and fixed while writing the initial tests** (not component defects): a `BrowserTabBar` test that forgot to give a second mock tab a distinct URL, and a `BrowserApp` test that typed into the address field before the initial `queryBrowserSurfaceState` round-trip settled — racing `BrowserToolbar`'s own URL-sync effect and having the typed value overwritten, exactly as a real (much narrower) race window would in production. Both were fixed by waiting for the settled state before interacting, matching realistic user timing.

---

## EVIDENCE

- `cargo check --lib`: clean.
- `cargo test --lib`: `test result: ok. 68 passed; 0 failed; 2 ignored;`
- `cargo clippy --lib`: 1 pre-existing, unrelated warning; 0 new.
- `cargo check --target x86_64-unknown-linux-gnu`: failed on `glib-sys`/`gobject-sys`, a pre-existing environment gap unrelated to this phase's code; this crate's own code was never reached.
- `pnpm typecheck`: clean.
- `pnpm test` (full suite, post-fix): `Test Files 101 passed (101)`, `Tests 834 passed (834)`.
- `git status`/`git diff --stat`: see the chat report for the exact file list (this phase has not been committed — the task explicitly withholds commit authorization).

---

## KNOWN LIMITATIONS

See `docs/architecture/browser_known_limitations.md`'s "As of Browser-B2" section for the full list: no automated test for the new COM code; no Linux-target compile confirmation (OD-B6); no visual/interactive confirmation of real back/forward/loading/resize/tab-switching; all tabs share one non-persisted profile; tab parking relies on a fixed off-screen coordinate rather than a dedicated "hide" primitive; no Browser Policy enforcement point yet.

---

## DECISIONS

See `docs/architecture/browser_decision_log.md` D15 (raw WebView2 COM for back/forward/real loading state, cross-platform `#[cfg(windows)]` gating) and D16 (tab switching reuses `set_bounds` rather than a new runtime concept), plus OD-B6 (Linux-target compile confirmation deferred to real CI).

---

## DEFERRED FUNCTIONALITY (explicitly not implemented, per the task brief)

Browser profiles, persistent sessions, account/session persistence, provider web authentication (Gemini/OpenAI/Claude web login), cookie/session-token extraction, CAPTCHA/Turnstile bypass, AI Browser Agent, page-content extraction for LLM context, `browser.navigate`/`.click`/`.type` AI capabilities, Playwright, Stagehand, Copilot integration, workflow/automation integration, downloads, uploads, bookmarks, browsing history (the persisted kind — distinct from the session back/forward this phase does implement), extensions. None of these were touched.

---

## NEXT PHASE

Browser-B3 — Profiles + Persistent Sessions. Recommended prerequisites: resolve OD-B5 (restore automated live-webview testing) and OD-B6 (confirm the non-Windows fallback path compiles on real CI) before B3 adds meaningfully more runtime logic; a human should manually verify real back/forward/loading/resize/tab-switching behavior in the actual running app.
