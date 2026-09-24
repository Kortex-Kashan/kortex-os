# KORTEX Browser — Browser-B1 Preflight Report

## WebView2 Integration Validation (pre-implementation)

Status: COMPLETE — read-only preflight, no production code implemented.
Date: 2026-09-25

## 1. Purpose

Resolve `browser_decision_log.md` OD-B3 (whether the pinned Tauri version supports the proposed embedded-child-webview architecture) before Browser-B1 implementation begins, per the project owner's explicit preflight request. This report supersedes, on the specific points below, the corresponding assumptions in `browser_architecture.md` and `browser_known_limitations.md` as they stood after Browser-B0 — those documents have been updated accordingly (§12).

## 2. Exact Dependency/Version Findings

Confirmed directly from `apps/desktop/src-tauri/Cargo.toml` and `Cargo.lock` (pinned exact versions, not ranges):

| Crate | Version | Role |
|---|---|---|
| `tauri` | `=2.11.5` (exact pin) | Application framework |
| `tauri-build` | `=2.6.3` | Build-time codegen |
| `tauri-runtime` | `2.11.3` | Runtime abstraction |
| `tauri-runtime-wry` | `2.11.4` | wry-backed runtime impl |
| `tao` | `0.35.3` | Window/event-loop backend |
| `wry` | `0.55.1` | Webview backend |
| `webview2-com` | `0.38.2` | WebView2 COM bindings |
| `webview2-com-sys` | `0.38.2` | WebView2 raw COM bindings |
| `windows` | `0.61.3` | Win32/COM bindings |
| `tauri-plugin-deep-link` | `2.x` | `kortex-auth://` scheme |
| `tauri-plugin-single-instance` | `2.x` | Single-instance + deep-link forwarding |
| `tauri-plugin-shell` | `2.x` | Scoped `shell:allow-open` only |

This repository's `tauri` dependency is on the `wry` default feature (`default = ["wry", "compression", "common-controls-v6", "dynamic-acl", "x11", "dbus"]` in `tauri`'s own `Cargo.toml`) — confirmed active, since the repo's own manifest (`features = []`) does not set `default-features = false`. `webview2-com`/`windows` are therefore already compiled into the current build, with zero new dependencies required for the core runtime primitives (§9).

## 3. WebView2 Integration Feasibility — Verified Against Actual Crate Source

Verified directly against the locally cached `tauri-2.11.5` source (`~/.cargo/registry/src/.../tauri-2.11.5/src/`), not general documentation, per the task's explicit requirement:

- **`Window::add_child<P, S>(&self, webview_builder: WebviewBuilder<R>, position: P, size: S) -> Result<Webview<R>>`** exists at `window/mod.rs:1129` — the exact API the proposed architecture (embedded child webview) requires, and it exists in this pinned version.
- **It is feature-gated**: `#[cfg(any(test, all(desktop, feature = "unstable")))]` (`window/mod.rs:1127-1128`). This repository's current `tauri = { version = "=2.11.5", features = [] }` (`Cargo.toml:16`) does **not** enable `unstable`, and `unstable` is not part of `tauri`'s own `default` feature set. **`add_child` is therefore not currently compilable in this repository as configured.**
- Enabling it is low-risk: `tauri-runtime-wry`'s own `unstable = []` (its `Cargo.toml:60`) is an empty feature — it gates already-compiled code behind a cfg flag and pulls in **no new transitive dependencies**. The fix is a one-line change: `tauri = { version = "=2.11.5", features = ["unstable"] }`.
- **Known operational constraint** (from the crate's own doc comment, `webview/mod.rs:287-290`): "On Windows, this function deadlocks when used in a synchronous command or event handlers... You should use `async` commands and separate threads when creating webviews." B1 must create the child webview from an async command or a dedicated thread, never synchronously inside a Tauri command handler.
- **Per-webview profile control is already supported, unconditionally**: `WebviewBuilder::data_directory(mut self, data_directory: PathBuf) -> Self` (`webview/mod.rs:963`) is a stable builder option, not gated behind `unstable`. This directly answers preflight objective 8 — WebView2's user-data folder can be set per-webview through Tauri's own API, no bypass of any KORTEX security boundary required.
- **Raw WebView2 controller/environment access is already available today, unconditionally**: `Webview::with_webview(f: FnOnce(PlatformWebview))` (`webview/mod.rs:1668`) is gated only by `#[cfg(feature = "wry")]` — a default-enabled feature, not `unstable`. On Windows, the resulting `PlatformWebview` exposes `.controller() -> ICoreWebView2Controller` and `.environment() -> ICoreWebView2Environment` directly (`webview/mod.rs:180-193`). Fine-grained WebView2-native settings (permission handlers, zoom, etc.) are therefore reachable **today**, without `unstable` and without any custom native bridge.

**Conclusion**: the proposed architecture (Tauri Desktop → Browser feature → `IKortexBrowserRuntime` → `WebView2RuntimeAdapter` → embedded WebView2 child webview) is supported by the actual pinned Tauri version. Option A (embedded child webview) is confirmed feasible from real crate source, not assumed from generic docs. One explicit, low-risk prerequisite gates it.

## 4. Existing Tauri Window/Webview Creation APIs Already Used by KORTEX

None. Confirmed by direct search of `apps/desktop/src-tauri/src/`: no `Webview`, `WindowBuilder`, `add_child`, or `get_window`/`get_webview` usage exists anywhere in this repository's own Rust code today. The single window (`"main"`) is created purely declaratively via `tauri.conf.json`'s `app.windows` array (`tauri.conf.json:12-22`). **B1 will be the first code in this repository to programmatically create a webview.**

## 5. Recommended B1 Architecture (Objective 5)

**A — embedded child webview inside the existing application window**, confirmed supported (§3). Rejected alternatives:
- **B (dedicated browser window)**: not required — `add_child` avoids the second-top-level-window model this repo's `tauri.conf.json` (`"windows": ["main"]`) and its persistent-sibling UI convention (`MiniChatHost`) were already designed around. Retained only as a documented fallback if the `unstable` feature proves unacceptable for a reason not yet discovered.
- **C (other architecture forced by the pinned version)**: not applicable — the pinned version supports Option A directly.

## 6. Frontend Navigation Slot — Corrected Finding

Browser-B0 identified `apps/desktop/src/shell/navigation/navConfig.ts:40` (`{ id: "browser", label: "Browser" }`) as the integration point. This preflight found the **exact** mechanism is more specific than "wire a route to this slot":

- `AppSidebar.tsx:53-66` renders **every** `NAV_GROUPS` item unconditionally `disabled` (`<SidebarMenuButton disabled title="Coming soon">`, `AppSidebar.tsx:59`) — there is no per-item flag to flip to "enabled."
- The **live**, routed application list is a separate array, `DEFAULT_APPLICATIONS` in `apps/desktop/src/workspace/defaultApps.ts:19-83`, each entry `{ id, name, description, icon, route, component, permissions }`, rendered in `AppSidebar.tsx`'s own "Applications" group via `useWorkspace()`.
- `navConfig.ts`'s own header comment (`navConfig.ts:16-22`) confirms this exact migration has already happened twice: "AI Studio and Marketplace were removed from here in M2.3: those two labels now have a real, live destination in AppSidebar's Applications group."

**Exact B2 integration point** (documented now for B2, not implemented in this preflight): remove the `browser` entry from `navConfig.ts`'s `business` group, and add a new entry to `DEFAULT_APPLICATIONS` in `defaultApps.ts` (e.g. `{ id: "browser", name: "Browser", route: "/browser", component: BrowserApp, permissions: ["kortex.browser.view"] }`), exactly mirroring the AI Studio/Marketplace precedent.

Note: `WorkspaceApplication.permissions` is a declared-but-**not-yet-enforced** field — `workspaceTypes.ts:5-8` states plainly "nothing in M2.2 checks them" pending the Security Engine being reachable via the IPC bridge. A `kortex.browser.view` permission string should still be declared for consistency, but does not itself gate visibility yet.

## 7. Native Communication Path Browser Will Use (Objective 7)

Two distinct, non-overlapping paths, not one — this preflight clarifies a distinction `browser_architecture.md` now makes explicit:

1. **B1 runtime primitives** (`browser_create_surface`, `.navigate`, `.dispatch_click`, etc.): plain new Tauri commands, registered exactly like `ipc::has_session`/`ipc::logout` today (`lib.rs:94-101`, `tauri::generate_handler![...]`) — direct frontend↔Rust IPC, **no backend HTTP hop**, since B1 has no capability/governance layer yet.
2. **B5+ governed capabilities** (`kortex.browser.navigate` as an AI-invocable, approval-gated action): go through the existing `invoke_capability` → loopback HTTP → `CapabilityDispatcher.dispatch()` path, exactly like every other governed capability today.

B1 only implements path 1. Path 2 is unaffected by B1 and remains B5's responsibility.

## 8. WebView2 Profile/User-Data-Directory Control vs. Existing Security Boundaries (Objective 8)

Confirmed feasible without weakening any existing boundary: `WebviewBuilder::data_directory()` (§3) is a **Rust-side-only** builder call — never a value the frontend passes as a raw filesystem path across the Tauri IPC boundary. Design constraint added to `browser_security_model.md` §10: the frontend's future `browser/api.ts` must pass only an opaque profile/tenant identifier; the Rust side resolves that identifier to an actual `PathBuf` internally (mirroring `PathSandboxValidator`'s existing validate-before-use discipline), so a compromised or buggy frontend can never direct WebView2's profile storage to an arbitrary path.

## 9. `IKortexBrowserRuntime` Abstraction vs. Actual Rust/Tauri Architecture (Objective 9)

The interface shape proposed in `browser_architecture.md` §2.2 (lifecycle, navigation, content access, input, downloads, profile binding) maps cleanly onto confirmed, real APIs:

| Interface responsibility | Backing Tauri/wry API (confirmed) |
|---|---|
| Lifecycle (create/dispose) | `Window::add_child()` / `Webview::close()` (`window/mod.rs:1129`, `webview/mod.rs:1502`) |
| Navigation | Standard wry/tauri webview navigation methods |
| Content access (read/screenshot) | `Webview::with_webview()` → `PlatformWebview` (`webview/mod.rs:1668`) for anything beyond the stable API |
| Input (click/type) | Via the same `with_webview()`/controller access, or standard webview scripting where sufficient |
| Downloads | wry's existing download-interception hooks (exact API not yet inspected in this preflight — defer to B1 implementation) |
| Profile binding | `WebviewBuilder::data_directory()` (`webview/mod.rs:963`) |
| Positioning/lifecycle management | `Webview::set_bounds()`, `.set_size()`, `.set_position()`, `.reparent()`, `.bounds()` (`webview/mod.rs:1509-1574`) — exactly what a docked, resizable child webview needs |

No part of the proposed abstraction requires an API this pinned version lacks.

## 10. Architectural Changes Required Before B1 (Objective 10)

1. **Required**: add `features = ["unstable"]` to the `tauri` dependency in `apps/desktop/src-tauri/Cargo.toml:16`. Low-risk (empty feature, zero new transitive dependencies), but is a source-code change and was **not** made during this preflight per its inspection-only scope — it is the first concrete action of Browser-B1 itself.
2. **Not required**: no new Cargo dependencies. `webview2-com`, `webview2-com-sys`, and `windows` are already transitively present via `tauri`'s own `wry` default feature.
3. **Not required**: no changes to `tauri.conf.json`'s window model — the single `"main"` window remains the parent for the new child webview.
4. **Recommended, not required**: decide during B1 whether Tauri v2's capability-file schema can scope permissions to a specific **webview label** (as opposed to only a **window label**, which is what `capabilities/default.json:5` currently does with `"windows": ["main"]`) — this preflight did not verify that exact ACL schema nuance and flags it as an open item for B1 rather than asserting it either way.

## 11. Security Implications

- No existing capability grant needs to be widened. The new child webview gets its own, separately-scoped capability file (per `browser_architecture.md` §2.3), following `capabilities/default.json`'s exact narrow-grant convention.
- No privileged KORTEX command is exposed to page JavaScript by anything found in this preflight — `with_webview()`/`PlatformWebview` access happens Rust-side only, never injected into the page's own JS context.
- `data_directory()`'s path must always be Rust-computed from a validated profile/tenant identifier (§8) — never accepted as a raw path from the frontend.
- Enabling `unstable` does not itself weaken any capability boundary — it is a compile-time cfg flag unlocking already-audited-upstream Tauri code, not a new dependency or a new attack surface by itself. The security review this unlocks (of Browser Policy itself) remains fully owned by Browser-B4, unaffected by this finding.

## 12. Documentation Updated

- `browser_decision_log.md` — OD-B3 resolved; new decisions D7 (enable `unstable`), D8 (corrected frontend integration point), D9 (native WebView2 controller/environment access already available).
- `browser_architecture.md` §2.3 — corrected frontend integration point, added the exact command-registration location, the `unstable` prerequisite, and the `with_webview`/`PlatformWebview` finding.
- `browser_security_model.md` §10 — refined: `BrowserProfileStore` is a path-allocation/tenant-mapping layer over Tauri's own `data_directory()` hook, not a from-scratch mechanism; added the Rust-side-only path-resolution constraint.
- `browser_known_limitations.md` — resolved the "Tauri multi-webview support is unverified" item; replaced with the precise `unstable`-feature prerequisite.
- `browser_roadmap_b0_b10.md` — Browser-B1 section's ARCHITECTURE/IMPLEMENTATION updated with the confirmed plan and the explicit first step (enable `unstable`).

Not modified: `.kortex/roadmap.md`, `CHANGELOG.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md` (pre-existing unrelated uncommitted changes, untouched), `scratch/` (untouched), `browser_vision.md`, `browser_capability_model.md`, `browser_auth_architecture.md`, `browser_b0_architecture_audit_report.md` (a historical phase record, not rewritten after the fact), ADR-0019 (its decision stands; this preflight validates rather than changes it).

## 13. Git Status

No commit, no push. No production source file was modified — every finding in this report was obtained by reading `apps/desktop/src-tauri/{Cargo.toml,Cargo.lock,tauri.conf.json,capabilities/default.json,src/lib.rs}`, `apps/desktop/src/shell/navigation/{navConfig.ts,AppSidebar.tsx}`, `apps/desktop/src/workspace/{defaultApps.ts,workspaceTypes.ts}`, and the locally cached `tauri-2.11.5`/`tauri-runtime-wry-2.11.4` crate source under the Cargo registry — no disposable/probe code change was necessary.

## 14. Confirmation

No production Browser code was implemented, no provider authentication, no browser automation, no AI Browser Agent functionality, no Playwright/Stagehand — nothing beyond this read-only preflight. Browser-B1 implementation itself begins only once the project owner authorizes it, starting with the one-line `Cargo.toml` feature-flag change identified in §10.
