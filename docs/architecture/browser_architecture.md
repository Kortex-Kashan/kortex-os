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
│   │   └── WebView2RuntimeAdapter  (V1, B1)
│   ├── Profiles / Sessions     (B3)
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
- Navigation: navigate to a URL, go back/forward/reload, report loading state.
- Content access: read the DOM/rendered text, take a screenshot, execute a constrained content read (never raw arbitrary script injection from an untrusted caller — see `browser_security_model.md`).
- Input: dispatch a click/type at a given target.
- Downloads: intercept and report a download request (the runtime reports, Browser Policy decides — see `browser_security_model.md` §5).
- Profile binding: which on-disk profile (cookies/storage/history) this surface reads/writes.

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
