# KORTEX Browser — Architecture

**Status**: Living document — established Browser-B0, updated every phase.

## 1. System placement

KORTEX Browser sits alongside AI Studio and the KORTEX AI Engine as a peer subsystem of KORTEX OS, not beneath either of them:

```
KORTEX OS
│
├── AI Studio
│   └── Provider Registry (backend/src/kortex/engines/ai/registry.py)
│       ├── API Authentication (existing)
│       └── Web Account Authentication (future, B7 — see browser_auth_architecture.md)
│
├── KORTEX Browser  (new — this project)
│   ├── Browser UI              (B2)
│   ├── Browser Runtime
│   │   └── WebView2RuntimeAdapter  (V1, B1) — profile-agnostic, see §2.7
│   ├── Profiles / Sessions     (B3 — implemented; BrowserProfileStore, see §2.7)
│   ├── Browser Policy          (B4 — the security boundary, see browser_security_model.md)
│   ├── Browser Capabilities    (B5 — see browser_capability_model.md)
│   └── AI Browser Agent        (B6)
│
└── KORTEX AI Engine
    ├── OpenRouter / Gemini / OpenAI / Anthropic (existing providers)
    └── Future Providers
```

Human browsing path: `WebView2RuntimeAdapter → Browser UI → Browser Runtime`.
AI-assisted browsing path: `KORTEX AI Engine → AI Browser Agent → Browser Policy → Browser Capabilities → Browser Runtime`. Browser Policy sits between the AI agent and the capabilities/runtime — it is consulted for every capability invocation regardless of whether the caller is a human-driven UI action or an AI tool call (see `browser_security_model.md` §2).

## 2. Runtime abstraction — `IKortexBrowserRuntime`

### 2.1 Why an abstraction

The desktop shell's existing webview is scoped by `apps/desktop/src-tauri/capabilities/default.json:6-15` to render only KORTEX's own first-party UI, with zero `http`/`websocket` grant (`docs/architecture/phase3_desktop_architecture.md:183,446`). Loading arbitrary third-party web content requires an entirely new, separately-scoped surface — the concrete reason a runtime abstraction is introduced now rather than reusing the existing webview (see ADR-0019 §3, Option 3).

### 2.2 Interface responsibility (V1 shape, to be finalized in B1)

`IKortexBrowserRuntime` is responsible for exactly the primitive operations a concrete browser engine must supply — nothing about policy, capability governance, or AI observation belongs in this interface:

- Lifecycle: create/dispose a browsing surface bound to a given profile.
- Navigation: navigate to a URL, go back/forward/reload, report loading state. **Implemented in Browser-B2** (`go_back`/`go_forward`/`BrowserSurfaceState.loading`) via raw `ICoreWebView2` COM calls — wry's stable `Webview` API has no session-history or navigation-event surface; see §2.6.
- Positioning: reposition/resize a surface within the window (`set_bounds`, Browser-B2 — used for both window-resize tracking and tab-switching, see §2.6).
- Content access: read the DOM/rendered text, take a screenshot, execute a constrained content read (never raw arbitrary script injection from an untrusted caller — see `browser_security_model.md`). Not yet implemented — Browser-B5's job.
- Input: dispatch a click/type at a given target. Not yet implemented — Browser-B5's job.
- Downloads: intercept and report a download request (the runtime reports, Browser Policy decides — see `browser_security_model.md` §5). Not yet implemented.
- Profile binding: which on-disk profile (cookies/storage/history) this surface reads/writes. Browser-B2's tabs all share one process-lifetime, non-persisted profile id; real per-tenant persistence is Browser-B3's job.

This interface is deliberately primitive/mechanical. Everything about *whether* an action is allowed (a website's own JS trying to call it vs. a governed AI action vs. a direct human click) is Browser Policy's responsibility, layered above the runtime, never inside it.

### 2.3 V1 adapter: `WebView2RuntimeAdapter` — implemented in Browser-B1

**Status: implemented.** `apps/desktop/src-tauri/src/browser_runtime.rs` defines the `BrowserRuntime` trait (`create_surface`/`navigate`/`reload`/`query_state`/`destroy`/`destroy_all`, all operating on an opaque `BrowserSurfaceId` — no WebView2/wry type crosses the trait boundary) and `WebView2RuntimeAdapter<R: tauri::Runtime>`, its implementation against real `tauri::Window<R>`/`Webview<R>`. Full detail, evidence, and test results: `docs/architecture/browser_b1_implementation_report.md`.

Implements `IKortexBrowserRuntime` against the Microsoft Edge WebView2 runtime. Integration seam, **confirmed against the actual pinned Tauri version** by the Browser-B1 preflight (`docs/architecture/browser_b1_preflight_report.md`) — not assumed from generic Tauri documentation:

- **New Rust module** in the existing Tauri crate (`apps/desktop/src-tauri/src/browser_runtime.rs`), registered in `lib.rs` next to the existing `ipc`/`events` modules (`mod ipc;` / `mod events;` at `lib.rs:18-19`, commands added to the `tauri::generate_handler![...]` list at `lib.rs:94-101`) — following the same pattern already used for every other native capability surface in this shell.
- **A new, narrowly-scoped Tauri capability file** (`apps/desktop/src-tauri/capabilities/browser.json`), mirroring `capabilities/default.json`'s narrow-grant convention, rather than widening `shell:*` or the main window's existing grants.
- **Rendered as an embedded child WebView via `Window::add_child()`, not a second top-level OS window** — confirmed to exist in the pinned `tauri = "=2.11.5"` (`window/mod.rs:1129`), matching the single-primary-window model already declared in `tauri.conf.json` (`"windows": ["main"]`) and the existing persistent-sibling pattern used by `MiniChatHost`. **Prerequisite**: `add_child` is gated behind the `unstable` Cargo feature, which this repo's `Cargo.toml:16` does not currently enable — a required, low-risk, one-line addition before B1 can use it (see `browser_decision_log.md` D7). It also deadlocks if created synchronously inside a Tauri command handler — must be created from an async command or a dedicated thread.
- **Per-webview profile control and raw WebView2 access are already available**: `WebviewBuilder::data_directory()` (`webview/mod.rs:963`, stable, not gated by `unstable`) sets the WebView2 user-data folder per webview; `Webview::with_webview()` → `PlatformWebview.controller()`/`.environment()` (`webview/mod.rs:1668,180-193`, gated only by the default-enabled `wry` feature) gives direct, Rust-side-only access to `ICoreWebView2Controller`/`ICoreWebView2Environment` for anything the stable builder API doesn't cover. See `browser_decision_log.md` D9.
- **Frontend module**: `apps/desktop/src/features/browser/{components,hooks,api.ts}`, following the same feature-module contract every other feature already follows (talks to the backend/native layer only through its own `api.ts`; never imports another feature's internals).
- **Frontend navigation integration point — implemented in Browser-B1**: the `browser` entry was removed from `navConfig.ts`'s disabled `NAV_GROUPS` placeholder list and added to `DEFAULT_APPLICATIONS` in `apps/desktop/src/workspace/defaultApps.ts` (`{ id: "browser", route: "/browser", component: BrowserApp, permissions: ["kortex.browser.view"] }`), exactly mirroring how "AI Studio" and "Marketplace" made the identical move in M2.3. See `browser_decision_log.md` D8.
- **Capability scoping — a load-bearing security finding from Browser-B1 (D10)**: Tauri's `Capability.windows` field grants a capability's permissions to *every webview embedded in that window*, including a child browser surface. `capabilities/browser.json` therefore scopes via `"webviews": ["main"]`, not `"windows": ["main"]`. Independently, `Origin::matches` denies any capability with no declared `remote` field to any `Remote`-origin content (every browser surface, always) regardless of window/webview label matching — Tauri's own built-in local-vs-remote trust boundary. See `browser_security_model.md` §4 and `browser_decision_log.md` D10 for the full, source-verified mechanism.
- **Native communication path** for B1's own runtime primitives (create/navigate/click/type/screenshot) is direct frontend↔Rust Tauri IPC — the same transport as `ipc::has_session`/`.logout` today — with **no backend HTTP hop**, since B1 has no capability/governance layer yet. That hop only exists for B5+'s governed `kortex.browser.*` capabilities, which go through the existing `invoke_capability` → `CapabilityDispatcher.dispatch()` path instead. These are two distinct transports serving two distinct phases, not one.

### 2.4 Future adapters

`CefRuntimeAdapter` and `ChromiumRuntimeAdapter` (or a platform-native equivalent for a future macOS/Linux desktop build) are anticipated but explicitly out of scope until a concrete platform/portability need arises. Any future adapter must satisfy the same `IKortexBrowserRuntime` contract and must not require changes to Browser Policy, Browser Capabilities, or the AI Browser Agent — those layers depend only on the interface.

### 2.5 Platform boundary — raw WebView2 COM (Browser-B2)

Back/forward navigation and real event-driven loading state (§2.2) required going beneath wry's stable `Webview` wrapper to raw `ICoreWebView2` COM calls, reached via `Webview::with_webview()` → `PlatformWebview.controller().CoreWebView2()`. This is Windows-only machinery (`webview2-com`/`windows` crates, promoted from transitive to direct dependencies — `browser_decision_log.md` D15). Every such call is `#[cfg(windows)]`-gated with an explicit, safe `#[cfg(not(windows))]` fallback, so `apps/desktop/src-tauri` keeps compiling on the non-Windows target this repository's own CI (`rust` job) already builds against — confirming the crate compiles there at all was not possible from this Windows development session (`browser_decision_log.md` OD-B6) and is deferred to that CI job's next real run.

### 2.6 Browser-B2 UI: tabs without a new runtime concept

`apps/desktop/src/features/browser/hooks/useBrowserTabs.ts` is the single place tab state lives. Every tab is a real `BrowserSurfaceId` (created via `create_surface`) — there is no frontend-only placeholder tab. Only the active tab's surface is positioned inside the real content-area rect (tracked via `ResizeObserver`, not polling); every other open tab's surface is parked at a fixed off-screen `SurfaceBounds` via the same `set_bounds` primitive used for resize-tracking (`browser_decision_log.md` D16) — deliberately not a second "active"/"visible" concept on `BrowserRuntime`. Switching tabs therefore never reloads or discards a tab's state, and closing a tab calls `destroy` on its real surface. `BrowserToolbar`/`BrowserTabBar` are presentational; all Tauri IPC goes through `features/browser/api.ts`, matching every other feature's convention.

### 2.7 Browser-B3: `BrowserProfileStore` — tenant-scoped, persistent profiles

`apps/desktop/src-tauri/src/browser_profile_store.rs` owns everything about profile identity, storage, locking, and lifecycle — `browser_runtime.rs`'s `BrowserRuntime`/`WebView2RuntimeAdapter` remain completely profile-agnostic, taking only an already-resolved `data_directory: PathBuf` in `CreateSurfaceRequest`. The Tauri command layer (`browser_create_surface`/`browser_destroy`) is the sole orchestration point between the two independent subsystems.

- **Identity**: `BrowserProfileId` is a 128-bit CSPRNG-random, `profile-`-prefixed opaque string — never a timestamp, never derived from `display_name`. `tenant_id` is resolved exclusively from `IpcClientState::current_tenant_id()` (OD-B7, below), never accepted as a frontend parameter anywhere.
- **Storage**: `<app_data_dir>/browser-profiles/<tenant_id>/<profile_id>/{profile.json, .lock, webview2-data/}`. No separate registry/index file — `list_profiles` scans the tenant directory and reads each profile's own `profile.json` directly (`browser_decision_log.md` D20), avoiding a dual-write consistency hazard a registry file would introduce.
- **Path safety**: `resolve_child_directory` ports `backend/.../storage/sandbox.py`'s `PathSandboxValidator` canonicalize-then-verify-containment principle to Rust — charset sanitization plus a second, independent containment check that also defeats a pre-planted symlink/junction at the expected path.
- **Locking**: a per-profile `.lock` file, PID-liveness-checked (`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`), atomic-create-or-atomic-recover — the primary, user-legible control. WebView2's own folder-exclusivity (confirmed live — see OD-B9 below) is an independent second layer, never the sole mechanism relied on.
- **ACL**: each profile directory is restricted to the current OS user via `icacls` (baseline isolation; full AppContainer-SID isolation is explicitly out of scope — `browser_decision_log.md` D26).
- **Legacy migration**: Browser-B1/B2's single, non-tenant-scoped `browser-profiles/default` directory, if one exists on a machine that already ran a live UI session, is quarantined (moved, never deleted or parsed) to `_legacy-quarantine/` on first launch — deterministic, idempotent, never assigned to any tenant.
- **Tenant identity bridge (OD-B7)**: the Tauri/Rust process holds only an opaque, undecoded session-token blob — deliberately, per `token_codec.py`'s own documented "the Tauri/Rust layer must never evaluate business rules" principle. `ipc.rs`'s `IpcClientState` now additionally captures `tenant_id` from the login/refresh response's own already-disclosed `SecurityPrincipal` payload (the exact same response that already carries `sessionToken`) — no backend change, no new capability, no token decoding. See `browser_decision_log.md` D25 for the full mechanism and rejected alternatives.
- **Distinct-profile coexistence (OD-B9)**: confirmed via a temporary, live preflight harness (removed after verification) against the real pinned stack: two distinct profile directories coexist simultaneously in one process, and destroy-then-recreate against the same directory works cleanly. See `browser_decision_log.md` D21/D24.
- **Audit**: an interim, local, structured (JSON-Lines) lifecycle log — not yet routed through the backend's real `AuditManager`, since Browser has no backend hop at all today (`browser_decision_log.md` D23).

### 2.8 Browser-B4: `BrowserPolicyEngine` — local, synchronous navigation/popup/download/permission policy

`apps/desktop/src-tauri/src/browser_policy.rs` owns exactly one decision — is a navigation's destination scheme/host allowed at all — as a pure, synchronous, deterministic function (`evaluate_navigation: &NavigationRequest -> PolicyDecision`), with zero I/O and no backend round-trip. This is architecturally required, not a design preference: `ICoreWebView2NavigationStartingEventArgs` has no `GetDeferral`, confirmed against the pinned `webview2-com-sys-0.38.2` bindings (`browser_decision_log.md` D28) — the allow/deny decision must be returned before the event handler itself returns, or the navigation simply proceeds unchecked by default. `browser_runtime.rs`'s `register_navigation_loading_handlers` is the sole caller: it reads `Uri`/`IsUserInitiated`/`IsRedirected` off the real COM event, calls `evaluate_navigation`, and calls `SetCancel(true)` on `PolicyDecision::Deny` — the only mechanism available.

`browser_policy.rs` remains profile/tenant-agnostic, unlike `browser_profile_store.rs` — `browser_runtime.rs` (where the evaluation must run, inside a raw WebView2 COM callback) has no access to `BrowserProfileStore`'s tenant/profile bindings, and V1's policy does not vary by tenant/profile regardless. It also does not itself implement any popup/download/permission POLICY DECISION beyond "deny all" — those three are wired directly in `browser_runtime.rs`'s own event handlers (`NewWindowRequestedEventHandler`/`DownloadStartingEventHandler`/`PermissionRequestedEventHandler`), reusing `browser_policy.rs` only for their shared denial-audit/frontend-event plumbing (`record_and_emit_policy_denial`, `DenyReason::NotYetSupported`).

- **Navigation policy**: only `http`/`https` schemes are potentially allowed; local/private-network destinations are denied via the first-class `DenyReason::PrivateNetworkAccess`, classified from `Url::host()`'s typed `url::Host` enum (never a re-parsed string — see D30) — covering loopback/RFC1918/link-local/"this network"/carrier-grade-NAT IPv4, unique-local/link-local IPv6, IPv4-mapped-IPv6, and the `localhost` hostname (including the trailing-dot FQDN bypass and numeric-encoding bypass classes closed by adversarial testing — D33). No DNS resolution is performed (OD-B16, disclosed).
- **Popup policy (B4 V1)**: every `NewWindowRequested` event is denied — `SetHandled(true)` without ever calling `SetNewWindow` (confirmed live to actually suppress the popup, not merely get audited — D32). No popup-to-tab conversion yet (OD-B13).
- **Download policy (B4 V1)**: every `DownloadStarting` event is denied — `SetCancel(true)`. This event lives on the versioned `ICoreWebView2_4` interface, not the base `ICoreWebView2`; the cast is best-effort, matching `disable_password_and_autofill`'s existing precedent. Confirmed live that the file never reaches disk (D32). No save-path confirmation yet (OD-B14).
- **Permission policy (B4 V1)**: every `PermissionRequested` event is denied — `SetState(COREWEBVIEW2_PERMISSION_STATE_DENY)`, regardless of `PermissionKind`. `GetDeferral` is confirmed present on this event (unlike `NavigationStarting`), so a future milestone can add real human-confirmation without restructuring this boundary. No confirmation UI yet (OD-B15).
- **Frontend-visible denial**: a single `PolicyDeniedEvent` shape (`surfaceId`, `action`, `reason` — never the actual URI, host, path, query, or fragment) emitted to the trusted `"main"` webview only, via `Webview::emit_to` (confirmed at the type level that a browser surface itself can never receive it) — and the identical shape appended to the same local JSON-Lines audit log Browser-B3 already established (`<profiles_root>/audit.log`), deliberately not tenant/profile-attributed (this runtime layer has no access to that binding — see `browser_policy.rs`'s own doc comment).
- **Live-verified, not merely documented (D32)**: a temporary, disposable preflight (removed after use) confirmed all four denial mechanisms above actually prevent the action against the real pinned WebView2 runtime — not merely that an audit entry gets written while the action still occurs.

## 3. Browser ↔ AI Engine boundary

The AI Browser Agent (B6) is a consumer of KORTEX AI Engine capabilities (model calls, tool orchestration) — it does not become a new AI provider, and it does not bypass Browser Policy. Concretely:

- `Provider`, `authentication method`, and `model` remain three separate concepts today (see `browser_auth_architecture.md` for the current provider/credential architecture this must not collapse).
- The AI Browser Agent issues governed tool calls (`browser.read`, `browser.click`, etc.) through the exact same `ToolDefinition` / `CapabilityDispatcher.dispatch()` / `DurableAIApprovalPolicy` / `TenantConcurrencyThrottler` chain every other governed AI action already uses (`backend/src/kortex/engines/ai/tools.py`, `.../governance.py`, `.../engine.py:2410-2520`) — no new orchestration engine, no second approval mechanism. See `browser_capability_model.md`.
- **AI Browser Agent is NOT the security boundary. Browser Policy is.** This is a hard architectural rule, not a preference: even if the AI Browser Agent is compromised, mis-prompted, or fed adversarial page content (prompt injection), Browser Policy — evaluated independently on every capability invocation, exactly like `SecurityEngine.authorize()` runs independently of the caller's intent today — must still deny access to native capabilities a website or a governed action isn't entitled to.

## 4. Native ↔ web / desktop precedent this design follows

Two existing subsystems set direct precedent for how governed native actions should be structured, and Browser-B0's audit confirms both are reusable *patterns* rather than reusable *transports*:

- **Desktop Automation (`kortex.desktop.*`)**: a closed `oneof` of narrow typed commands (`backend/src/kortex/engines/agent_gateway/protos/agent.proto`), each capability registered via `kernel.register_capability(..., requires_execution_context=True, security_classification="CONFIDENTIAL")` (`desktop_automation/engine.py:119-162`), dispatched over an authenticated (mTLS) channel to a separately-installed .NET Windows Service (`apps/desktop-agent/Program.cs:3`). Browser does **not** need the mTLS/gRPC session-authorization layer — that machinery exists specifically to authenticate a remote, separately-installed machine identity, a problem Browser doesn't have since its runtime is in-process with the Tauri app. Browser should reuse the *governance shape* (closed command set, `register_capability` with `requires_execution_context`, fail-closed target resolution) via a leaner, in-process engine analogous to `DesktopAutomationEngine`.
- **The capability/tool bridge**: every governed action subsystem (Connector, Document, Knowledge, Desktop) follows one pattern — register a plain Kernel capability with a real `parameters_schema`/`is_read_only`, convert it to a `ToolDefinition` via `generate_tool_definition_from_capability()` (`api/capability_tool_bridge.py:63-86`, which derives `is_mutation = not is_read_only` — never from naming), then register it idempotently into the shared `ToolRegistry` via `_register_tool_if_absent()` (`api/kernel_bootstrap.py:381-397`). `kortex.browser.*` should follow this exact pattern (see `browser_capability_model.md`).

## 5. Automation architecture (Playwright/Stagehand — future, out of scope for B0–B1)

Playwright and/or Stagehand may eventually be used for browser automation, testing, or AI-assisted browser interaction against KORTEX Browser — but neither becomes the KORTEX browser lifecycle owner, the primary security boundary, or the source of truth for browser policy. The human browser runtime remains `WebView2RuntimeAdapter` (V1). This is a distinct question from the existing Phase 6 non-goal (`phase5_locked_architecture_spec.md:32`), which rejected Playwright/Puppeteer as the *desktop UI automation engine* (superseded by FlaUI/UIA3) — that decision is about automating KORTEX's own desktop app, not about test-automating a browser widget KORTEX itself now renders. How an automation/test adapter talks to `IKortexBrowserRuntime` without coupling the whole Browser architecture to it is deferred to a later phase (not before B5).
