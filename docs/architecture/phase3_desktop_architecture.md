# KORTEX OS — Phase 3 Desktop Architecture Specification

Status: **ACCEPTED — Ratified by ADR-0002**
Version: 0.1.0
Author: Claude Code (Implementation Agent)
Authority: KORTEX OS Engineering Constitution (`AGENTS.md`) & Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`). This document is a **proposal**. It becomes binding architecture only after ratification by the Chief Architect (KASHAN) via the Change Management Policy defined in `ARCHITECTURE_VERSION_1.0.md` §22. Until ratified, no implementation work may cite this document as authorization to bypass `AGENTS.md` or existing ratified specs.
Target Release: KORTEX OS Phase 3: Desktop Container & UI System
Target File: `docs/architecture/phase3_desktop_architecture.md`

Depends On:
- KORTEX OS AI Engineering Constitution (`AGENTS.md`)
- KORTEX OS Architecture Version 1.0.0 (`docs/architecture/ARCHITECTURE_VERSION_1.0.md`)
- KORTEX OS Project Definition (`.kortex/project.md`)
- KORTEX OS Technology Stack (`.kortex/stack.md`)
- KORTEX OS Architecture Reference (`.kortex/architecture.md`)
- KORTEX OS Coding Rules (`.kortex/coding_rules.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)
- Capability Registry Architecture (`docs/architecture/capability_registry.md`)
- Event Bus Architecture (`docs/architecture/event_bus.md`)
- Security Engine Implementation Specification (`docs/architecture/security_engine_implementation_spec.md`)
- KORTEX Desktop intent (`apps/desktop/README.md`)
- KORTEX Server intent (`apps/server/README.md`)
- KORTEX API Layer intent (`backend/src/kortex/api/README.md`)

---

## 1. Executive Summary

Phase 3 delivers the **Desktop Container & UI System**: the first user-facing surface of KORTEX OS. It wraps the Phase 1/2 backend (Kernel, Registry Engine, Event Engine, Storage Engine, Workflow Engine, Recipe Engine, Document Engine, Connector Engine, Security Engine) in a Tauri v2 desktop shell hosting a React + TypeScript application, communicating exclusively through capability-mediated IPC.

As of this specification's authoring, Phase 3 has **zero implementation**: `apps/desktop/`, `apps/server/`, and `design-system/` each contain only a `README.md` stating intent, and `backend/src/kortex/api/` contains only a `README.md` and an empty `__init__.py`. No `package.json`, `Cargo.toml`, `tauri.conf.json`, `tsconfig.json`, or frontend source file exists anywhere in the repository. This document defines the target architecture those implementations must conform to; it does not itself constitute implementation.

Phase 3 is architecturally significant beyond "building a UI": it is the point at which KORTEX OS's capability-driven backend discipline (Article 6/7, `platform_service_contracts.md`, `capability_registry.md`) must survive contact with an external, un-trusted presentation surface. The central engineering risk this document manages is **boundary discipline** — ensuring the desktop shell remains a thin container and the React application remains a thin capability consumer, with zero business logic, zero direct engine access, and zero independent authority leaking into either layer.

---

## 2. Phase 3 Goals

1. Stand up a Tauri v2 desktop shell that manages window lifecycle and supervises the Python backend as a local sidecar process.
2. Stand up a React 18+ / TypeScript 5+ frontend application, styled with TailwindCSS, capable of rendering real backend-driven screens.
3. Establish a single, capability-contract-compliant IPC bridge between the frontend and the backend's Kernel Capability Dispatcher — no parallel, ad hoc communication path.
4. Establish a reusable KORTEX Design System (tokens, themes, foundation components) shared across `apps/desktop` and future KORTEX applications (`apps/server`-adjacent admin tools, future mobile/web shells).
5. Preserve every invariant already ratified in Architecture Version 1.0.0: capability-only invocation, Kernel authorization on every call, zero direct module/engine coupling, local-first/offline-first operation.
6. Produce a decision record for every open technology choice ("TBD" in `apps/desktop/README.md`) so Phase 3 implementation begins with zero unresolved architectural ambiguity.

---

## 3. Architectural Principles

These principles are non-negotiable for all Phase 3 implementation work (mirrors Architecture Rules in the task brief; restated here as binding spec language):

1. **UI contains no business logic.** Screens render state and dispatch capability requests. Validation beyond basic input shape (required fields, type coercion) belongs to the backend; the frontend performs only user-experience-level validation (e.g. disabling a submit button), never authoritative validation.
2. **The UI cannot directly communicate with engines.** There is no code path, in Rust or TypeScript, that imports or calls an engine directly. Every action reaches an engine only via the Kernel Capability Dispatcher.
3. **All actions flow through capability contracts.** Every backend-bound action is a named capability (`kortex.<domain>.<resource>.<action>`) invoked via `CapabilityRequest` / `UniversalResult`, per `platform_service_contracts.md`. The IPC layer does not invent a parallel request shape.
4. **Security boundaries remain backend-owned.** RBAC/ABAC evaluation happens exclusively in the Security Engine. The frontend may reflect a permission (to hide/disable UI), but a hidden control is never the enforcement mechanism — the backend rejects the call independently.
5. **The desktop shell is a container, not the application brain.** Rust code performs window management, process supervision, secure IPC transport, and OS integration. Rust never evaluates business rules, never renders business logic, and never calls an engine directly.
6. **The design system is reusable across future KORTEX applications.** It ships as an independent workspace package (`design-system/`), consumed by `apps/desktop` and any future KORTEX frontend, never duplicated per-app.
7. **Components must be deterministic and testable.** Foundation and feature components are pure functions of props/state wherever possible; side effects (IPC calls, event subscriptions) are isolated behind typed hooks that can be replaced with test doubles.

---

## 4. System Architecture Diagram

```
┌───────────────────────────────────────────────────────────────────────────┐
│                         Tauri Webview (React + TS)                        │
│  Screens → Feature Hooks → TanStack Query / Zustand → IPC Client (typed)  │
│                                                                             │
│  NO network capability granted to this process (see §11 Security Model)   │
└───────────────────────────────┬───────────────────────┬───────────────────┘
                 invoke("capability_call")     listen("kortex://event")
                                 │                       ▲
                                 ▼                       │
┌───────────────────────────────────────────────────────────────────────────┐
│                       Tauri Rust Shell (src-tauri)                        │
│  Window Lifecycle │ Sidecar Supervisor │ IPC Command Handlers            │
│  Session Token Custody (OS keychain) │ Event Relay │ Crash/Restart Logic │
└───────────────────────────────┬───────────────────────┬───────────────────┘
                    HTTP POST /capabilities/invoke   WS /events/stream
                          (loopback only, 127.0.0.1)
                                 ▼                       ▲
┌───────────────────────────────────────────────────────────────────────────┐
│                 FastAPI Presentation Layer (kortex.api)                   │
│         Thin routers: validate transport shape, delegate, format          │
└───────────────────────────────┬───────────────────────┬───────────────────┘
                                 ▼                       │
┌───────────────────────────────────────────────────────────────────────────┐
│                    Kernel Capability Dispatcher                          │
│      Resolution → Authorization (Security Engine) → Dispatch → Result    │
└───────────────────────────────┬───────────────────────┬───────────────────┘
                                 ▼                       │
┌───────────────────────────────────────────────────────────────────────────┐
│   System Engines: Security │ Storage │ Event │ Workflow │ Recipe │ ...    │
└───────────────────────────────────────────────────────────────────────────┘
                                                          │
                                          Event Engine emits KortexEvent →
                                          WS /events/stream (filtered per
                                          caller's tenant + permissions)
```

This diagram is the canonical reference for every subsequent section. No component in this diagram may be bypassed.

---

## 5. Desktop Runtime Architecture

The desktop application is a single OS process tree with two runtimes:

| Runtime | Process | Responsibility |
|---|---|---|
| Rust Shell | Parent process (Tauri) | Window/lifecycle, sidecar supervision, secure IPC transport, OS integration |
| Webview | Child renderer (OS-native WebView2/WebKit/GTK WebKit) | React application execution |
| Python Sidecar | Child process, spawned by Rust | FastAPI app (`kortex.api.main:app`) + Kernel + Engines |

Startup sequence:

1. Rust shell process starts; reads `tauri.conf.json` and capability files.
2. Rust spawns the Python sidecar as a child process bound to `127.0.0.1:<port>` (port selected from a configured range or OS-assigned ephemeral port, then communicated to the webview only through Rust-mediated IPC — the webview never learns the port directly, since it has no network capability to use it).
3. Rust polls a lightweight readiness endpoint (`GET /health`, mapped to `IEngineDiagnostics.status()` aggregated across Boot Engine) until the backend reports `READY`, with a bounded timeout.
4. Rust creates the application window and loads the webview (dev server URL in development, bundled static assets in production).
5. React application mounts, requests an initial session via an `invoke("authenticate", …)` command routed to the Security Engine's `AuthenticationManager`.
6. On success, the webview establishes its event-stream subscription (relayed through Rust; see §13) and renders the primary application shell.

Shutdown sequence:

1. Window close request triggers Rust's `on_window_event(CloseRequested)` handler.
2. Rust sends a graceful shutdown signal to the sidecar (aligned with the 30-second graceful shutdown window already ratified in `platform_runtime.md` §9 of `ARCHITECTURE_VERSION_1.0.md`).
3. If the sidecar does not exit within the graceful window, Rust force-terminates the child process.
4. Rust exits only after sidecar termination is confirmed, preventing orphaned backend processes.

---

## 6. Tauri Shell Design

### 6.1 Allowed vs. Not Allowed

| Allowed (Rust Shell) | Not Allowed (Rust Shell) |
|---|---|
| Window management (create, resize, minimize, close, multi-window if ever needed) | Business logic of any kind |
| IPC transport (command handlers that forward payloads verbatim) | Direct engine, database, or Storage Engine access |
| Application lifecycle (startup, shutdown, crash recovery) | Independent authorization decisions (Rust never decides "is this allowed") |
| Sidecar process supervision (spawn, health-check, restart, terminate) | Persisting business data (business data lives only behind Storage Engine) |
| Secure OS integration (keychain-backed token storage, native menus, notifications, file/save dialogs invoked by explicit user action) | Arbitrary shell execution or unscoped filesystem access |
| Relaying already-authorized event-stream payloads to the webview | Making authorization or business-rule decisions about relayed data |

### 6.2 Application Lifecycle

Standard Tauri v2 lifecycle hooks are used: `setup()` for sidecar spawn and readiness polling, `on_window_event` for close/focus handling, `RunEvent::ExitRequested` for coordinated shutdown. No custom lifecycle framework is introduced.

### 6.3 Backend Process Ownership & Sidecar Strategy

The Python backend is packaged as a Tauri **sidecar binary** (Tauri's external-binary bundling mechanism), not invoked via a bare `python` interpreter call in production. Packaging into a single frozen executable (tool choice — PyInstaller vs. Nuitka — is an **open item**, see §21) removes the runtime dependency on a system Python installation, consistent with Local-First/offline distribution.

- **Ownership**: The Rust process is the sole owner of the sidecar's lifecycle. No other process (including the webview) may start, stop, or signal it.
- **Binding**: The sidecar binds exclusively to `127.0.0.1` (loopback). It is never bound to `0.0.0.0` or any externally routable interface in desktop mode. This is distinct from `apps/server`'s headless enterprise mode, which intentionally binds to a routable interface under separate deployment controls outside Phase 3 scope.
- **Port selection**: An ephemeral, OS-assigned local port is preferred over a fixed port to avoid collisions with a second KORTEX instance or unrelated local services; Rust discovers and uses this port internally.

### 6.4 Crash Handling

1. Rust monitors the sidecar's exit status via the child process handle.
2. On unexpected exit (non-zero code, or process death outside graceful shutdown), Rust attempts up to 3 restarts with exponential backoff (100ms, 2× multiplier — matching the retry backoff convention already ratified in `platform_service_contracts.md` §15), re-running the readiness probe after each restart.
3. If restarts are exhausted, the webview is shown a "Backend Unavailable" recovery screen (a UI state, not a silent failure) with a manual retry action.
4. Webview-side, any in-flight IPC calls that fail with `SERVICE_UNAVAILABLE` during a crash window follow the retry rules in `platform_service_contracts.md` §15 (only idempotent capabilities auto-retry).

### 6.5 Update Strategy

- The desktop shell (Rust binary + bundled webview assets + bundled Python sidecar) updates as a single signed unit via Tauri v2's official updater plugin.
- Update manifests are verified using the same Ed25519 signature scheme already ratified for platform assets (`asset_system.md`), for consistency of trust model rather than introducing a second signing mechanism.
- The headless enterprise server (`apps/server`) is updated independently through standard deployment pipelines (Docker image rebuild/redeploy) — it is out of scope for the desktop updater, since it does not run inside a Tauri shell.
- Full installer packaging (`.msi`/`.exe`/`.dmg` polish, code-signing certificates, staged rollout) is explicitly deferred to Phase 7 ("Desktop installers") per `.kortex/roadmap.md`; Phase 3 need only prove the updater mechanism functions, not productionize distribution.

### 6.6 Desktop Security Capabilities & Permission Model

Tauri v2's capabilities/permissions system (`src-tauri/capabilities/*.json`) is **deny-by-default**. Phase 3 defines exactly one capability set (`default.json`) scoped to the minimum required surface:

| Permission | Scope | Rationale |
|---|---|---|
| `core:window:*` (subset) | Create/close/focus/resize the single primary window | Required for basic shell operation |
| `core:app:*` (subset) | Lifecycle events, exit handling | Required for graceful shutdown |
| `shell:allow-execute` | **Scoped to the exact bundled sidecar binary path only** — no arbitrary command execution | Required to spawn the backend; must not become a general shell-exec grant |
| `fs` (scoped) | Read/write restricted to the Tauri app-data directory only (e.g. cached window state, non-business local preferences) | Business data never touches frontend-owned filesystem; it lives behind Storage Engine |
| `os` / `path` (info only) | Read-only OS/path metadata needed for sidecar path resolution | No behavior change from grant |
| `http`, `websocket` (webview-facing plugins) | **Not granted** | See §11 — all network egress to the backend is Rust-mediated; the webview has no standalone network capability |
| `dialog` (save/open, explicit user action only) | For user-initiated export/import flows (e.g. "Save Report As…") | Never used for silent/background I/O |

Any additional permission requires an explicit addition to this table in a future revision of this document — permissions are never granted ad hoc during implementation.

### 6.7 Environment Separation

| Concern | Development | Production |
|---|---|---|
| Webview content source | Vite dev server (`devUrl`, default `http://localhost:1420`) with HMR | Bundled static assets from `dist/`, loaded from disk |
| Sidecar | Backend run directly from the local Python virtual environment (`uvicorn kortex.api.main:app`), pointed at SQLite embedded local mode | Frozen sidecar binary, bundled inside the app package |
| DevTools | Enabled | Disabled by default; enabled only in an explicit "debug build" configuration, never in the distributed production build |
| Content Security Policy | Relaxed only as required for HMR websocket | Strict CSP: no remote script/style origins, no `unsafe-inline` beyond what Tailwind's build output requires |
| Logging | Verbose, console-visible | File-based, rotating, redacted of secrets/tokens |

---

## 7. React Application Architecture

### 7.1 Stack Decisions (binding)

| Concern | Decision | Version Target |
|---|---|---|
| UI Library | React | 18+ |
| Language | TypeScript | 5+ |
| Build tool | Vite | latest stable at implementation time |
| Styling | TailwindCSS | 3+ |
| Package manager | pnpm (workspace mode) | latest stable |
| Test runner | Vitest + React Testing Library | latest stable |

Vite is selected over Webpack because it is the Tauri-recommended, zero-config-friendly bundler with fast HMR; introducing Webpack alongside it would add configuration surface with no offsetting benefit. pnpm is selected over npm/yarn specifically because the Design System (§10) must be a **shared workspace package** consumed by `apps/desktop` and future KORTEX frontends (§3 principle 6) — pnpm's workspace protocol (`workspace:*`) is the simplest mechanism for this without publishing to a registry, which would violate offline-first/local-first distribution.

### 7.2 Routing Strategy

**Decision: React Router (in-memory/hash mode), not TanStack Router.**

The desktop shell has no browser address bar, no SEO requirement, and no deep-linking-from-the-web requirement — it is a single always-local window. React Router's declarative `<Routes>`/`<Route>` model is sufficient and is the more widely understood, lower-ceremony choice. TanStack Router is rejected: it would be a second "TanStack" dependency alongside TanStack Query with overlapping data-loading concepts, adding configuration surface (route trees, loaders that duplicate what TanStack Query already owns) without a corresponding desktop-specific benefit. This follows the explicit instruction to avoid unnecessary dependencies.

### 7.3 State Management Strategy — see §12 for the full decision record.

Summary for this section: local/UI/ephemeral state → Zustand; server-derived state (anything that originates from a capability call) → TanStack Query. Components never hold server data in local component state or in a global Zustand store — that would create a second source of truth and drift from the backend.

### 7.4 Component Organization

**Decision: feature-based organization for the application; atomic/foundation organization only inside the shared Design System package.**

```
apps/desktop/src/
├── app/            # Root providers (QueryClient, ThemeProvider, Router), shell layout, navigation shell
├── features/       # One folder per feature: <feature>/components, <feature>/hooks, <feature>/api
│   └── <feature>/
│       ├── components/
│       ├── hooks/
│       └── api.ts          # typed capability-call wrappers for this feature only
├── ipc/            # Thin, typed wrapper around Tauri invoke()/listen() — see §8
└── main.tsx
```

Rationale: KORTEX's own module contract (`.kortex/architecture.md` — "Module Contract") already groups a business concern's Data/UI/AI/Recipes/etc. facets together rather than by technical layer. Feature-based frontend organization mirrors that convention and keeps a screen's UI, hooks, and capability bindings co-located and independently testable (Architectural Principle 7). Pure atomic design (atoms/molecules/organisms folders spanning the whole app) is rejected at the application level because it fragments feature ownership across parallel folder trees; it is, however, the correct model **inside** the Design System package, where components genuinely are context-free foundation primitives (§10).

### 7.5 Error Handling & Loading States

- Every capability-call hook (built on TanStack Query) exposes `{ data, error, isLoading, isFetching }` per TanStack Query convention — no bespoke loading/error state machine is invented.
- `error` is always a typed `IpcError` (§8.3), never a raw string or unknown exception — components pattern-match on `error.category` to render category-appropriate UI (inline validation, permission-denied notice, offline banner, generic toast).
- A single top-level `ErrorBoundary` catches unhandled render exceptions and reports them to Rust-side logging via a dedicated `report_render_error` IPC command; it never silently swallows errors.
- Loading states use the Design System's skeleton/spinner primitives (§10.3) — no per-feature bespoke spinners.

### 7.6 UI Composition Rules

1. Feature components may compose Design System foundation components; they may not redefine visual primitives (no ad hoc buttons/inputs outside the Design System).
2. Feature components call the backend only through their feature's `api.ts` capability-call hooks — never call `invoke()` directly from a component body.
3. No feature imports from another feature's internals; shared logic is promoted to `app/` or the Design System, mirroring the "modules never import from other modules" rule already ratified for the backend (`.kortex/architecture.md` Communication Rules).

---

## 8. IPC Bridge Contract Architecture

This is the load-bearing contract of Phase 3. It exists to guarantee that the capability-only invocation discipline already ratified for the backend (`platform_service_contracts.md`, `capability_registry.md`) is not silently broken the moment a UI is introduced.

### 8.1 Governing Rule

The IPC layer is a **transport**, not a second contract system. It does not define its own request/response shape; it carries the existing `CapabilityRequest` / `UniversalResult` contract across the Tauri boundary, unchanged in meaning.

### 8.2 Request Format

```typescript
// Specification example — illustrative contract shape only, not implementation.
interface IpcCapabilityRequest {
  requestId: string;            // UUID — maps to CapabilityRequest.request_id
  capabilityName: string;       // "kortex.<domain>.<resource>.<action>"
  parameters: Record<string, unknown>;
  correlationId?: string;       // generated by the IPC client if omitted
  idempotencyKey?: string;      // required by the caller only for idempotent capabilities
  timeoutMs?: number;           // defaults per platform_service_contracts.md §14
}
```

```rust
// Specification example — Tauri command signature, illustrative only.
#[tauri::command]
async fn invoke_capability(
    request: IpcCapabilityRequest,
    state: tauri::State<'_, SidecarClient>,
) -> Result<IpcResultEnvelope, IpcErrorDto> {
    // Forwards verbatim to POST /capabilities/invoke on the local sidecar.
    // No business logic, no parameter interpretation, no capability whitelisting
    // beyond what the backend's own authorization layer already enforces.
}
```

The Rust command handler performs exactly three things: attach the session token held in OS-keychain custody (§11), forward the request body verbatim over loopback HTTP to `POST /capabilities/invoke` (§9), and return the response verbatim. It must not branch on `capabilityName`.

### 8.3 Response & Error Contract

```typescript
// Specification example — illustrative contract shape only.
interface IpcResultEnvelope {
  requestId: string;
  correlationId: string;
  status: "SUCCESS" | "FAILURE" | "PARTIAL_SUCCESS" | "CANCELLED";
  payload: Record<string, unknown> | null;
  errors: IpcError[];
  warnings: IpcError[];
  executionDurationMs: number;
}

interface IpcError {
  category:
    | "CAPABILITY_NOT_FOUND"
    | "PERMISSION_DENIED"
    | "VALIDATION_FAILED"
    | "TIMEOUT_EXCEEDED"
    | "SERVICE_UNAVAILABLE"
    | "EXECUTION_FAILED";
  message: string;
  details?: Record<string, unknown>;
  correlationId: string;
}
```

`IpcError.category` is a 1:1 mirror of the `UniversalError` categories already ratified in `platform_service_contracts.md` §7 — no new error taxonomy is introduced at the frontend boundary. Components match on `category`, never on `message` string content (message text is for display/logging only and is not a stable contract).

### 8.4 Authentication & Session Propagation

1. On first launch (or session expiry), the webview invokes an `authenticate` IPC command carrying user-entered credentials or an existing local session artifact.
2. Rust forwards this to the Security Engine's `AuthenticationManager` (`kortex.security.auth.authenticate`) exactly as any other capability call.
3. On success, Rust — **not the webview** — receives and stores the resulting session token, persisted via the OS-native secure credential store (Tauri's `keyring`-backed storage), never in `localStorage`, `sessionStorage`, or a plain file.
4. Every subsequent `invoke_capability` call has the session token attached by Rust before the HTTP forward. The webview never sees, holds, or transmits the raw token.
5. Session expiry is surfaced to the webview as a normal `PERMISSION_DENIED`-shaped (or a dedicated `SESSION_EXPIRED`, an additive, backward-compatible extension subject to backend confirmation) response, prompting re-authentication — never a silent retry with stale credentials.

### 8.5 Permission Validation

Permission checks happen exactly once, in the Security Engine, during Kernel dispatch (already ratified in `capability_registry.md` §8). The frontend may **read** a capability's `required_permissions` (surfaced via capability discovery metadata) purely to decide whether to render/enable a control — this is a UX optimization, never the security boundary. If a hidden or disabled control were somehow triggered anyway, the backend must independently reject it.

### 8.6 Capability Invocation Flow

1. Feature hook calls `ipc.invoke(capabilityName, parameters)`.
2. IPC client (`apps/desktop/src/ipc/`) assigns `requestId`/`correlationId` if absent, wraps as `IpcCapabilityRequest`.
3. `invoke("invoke_capability", request)` crosses into Rust.
4. Rust attaches the session token, POSTs to `http://127.0.0.1:<port>/capabilities/invoke`.
5. FastAPI router validates the transport-level shape and forwards to the Kernel Capability Dispatcher (§9).
6. Kernel resolves → authorizes → dispatches → engine executes → `UniversalResult` returned.
7. FastAPI serializes `UniversalResult` back as the HTTP response body.
8. Rust returns the body verbatim as the `invoke_capability` result.
9. TanStack Query resolves the promise; the typed hook exposes `data`/`error` to the component.

### 8.7 Correlation IDs & Logging/Tracing

- Every `IpcCapabilityRequest` carries a `correlationId`; if the frontend omits one, Rust generates it before forwarding (mirroring the Kernel's own fallback behavior in `platform_service_contracts.md` §18) so a trace ID exists even if the webview layer has a bug.
- The same `correlationId` is used for the event-stream subscription tied to that user action (§13), enabling end-to-end tracing from a single user click through to any resulting event broadcast.
- Rust logs `{command, correlationId, durationMs, statusCategory}` (never raw parameters or tokens) to a rotating local log file in the app-data directory.
- The frontend logs only to the browser devtools console in development; production builds surface failures only as user-facing UI state, not console noise, and never log tokens or full request payloads.

---

## 9. Backend Presentation Layer Requirements

`backend/src/kortex/api/` currently contains only a `README.md` and an empty `__init__.py` — this section defines the minimum required for M3 to close, and is a **direct dependency** Phase 3 places on the backend codebase (flagged as an open item requiring scope confirmation — see §18).

### 9.1 Clarifying Decision: One Uniform Presentation Surface

`apps/desktop/README.md` and `backend/src/kortex/api/README.md` both reference a distinct "Tauri IPC Adapter" concept on the Python side. This specification clarifies: **no such distinct adapter exists in Python.** The Rust shell is the only Tauri-aware component in the system (§6.1). The FastAPI presentation layer exposes one uniform REST/WebSocket surface consumed identically by:
- the desktop shell (via Rust-mediated loopback HTTP/WS), and
- the headless enterprise server mode (`apps/server`, same FastAPI app, routable network binding, no Tauri involved).

This keeps the backend deployment-target-agnostic, per `apps/server/README.md`'s own stated purpose.

### 9.2 Required Surface

| Endpoint | Method | Purpose |
|---|---|---|
| `/capabilities/invoke` | `POST` | The single generic entry point accepting an `IpcCapabilityRequest`-shaped body, forwarding to the Kernel Capability Dispatcher, returning `UniversalResult`. Required baseline for M3. |
| `/events/stream` | `WS` (upgrade) | Authenticated event-stream subscription, scoped server-side by `tenant_id` and the caller's granted topic permissions (§13). |
| `/health` | `GET` | Aggregated readiness probe used by Rust's sidecar startup polling (§5) — backed by `IEngineDiagnostics.status()` across booted engines, not a bespoke health model. |

Domain-specific convenience REST routes (e.g. `/hr/employees`) that internally still delegate to the same Kernel dispatcher are explicitly **optional** and out of scope for Phase 3 closure — they add no new capability, only ergonomics, and would violate the routers' own documented design rule ("routers are thin") if implemented as anything other than a thin wrapper over the same dispatch call.

### 9.3 Router Design Rules (restated, binding)

Per `backend/src/kortex/api/README.md`'s own already-stated design rules: routers validate transport-level input shape, delegate to the Kernel dispatcher, and format `UniversalResult` as the HTTP/WS payload. No business logic in routers. No Pydantic model in `kortex.api` may duplicate a domain model already defined by an engine — routers reuse engine/shared models directly.

### 9.4 Entry Point

`apps/server/README.md` already references `uvicorn kortex.api.main:app`. Phase 3 requires this module and app instance to exist; it does not currently exist anywhere in `backend/src`. Creating it is backend implementation work, not desktop/frontend work, but it is a hard blocking dependency for M3 (§18).

---

## 10. Design System Architecture

### 10.1 Philosophy

KORTEX is an enterprise business operating system, not a consumer or marketing product. The design system optimizes for:
- **Consistency over decoration** — every screen composed from the same finite token/component vocabulary.
- **Density-tolerant, professional desktop aesthetics** — data tables, forms, and command-driven workflows are first-class citizens, not afterthoughts bolted onto a marketing-site aesthetic.
- **Reusability across future KORTEX applications** — the system is a standalone workspace package, never app-specific.

### 10.2 Design Tokens

Tokens are defined once, as the single source of truth consumed by Tailwind's theme configuration (no parallel token system; Tailwind config *is* the token layer):

| Token Category | Examples |
|---|---|
| Color | Semantic roles (`background`, `foreground`, `primary`, `destructive`, `muted`, `border`) defined per theme, not raw hex references in component code |
| Typography | A constrained type scale (display/heading/body/caption) and a single font stack with system-font fallback |
| Spacing | A single spacing scale (Tailwind's default 4px-based scale, not a custom one, to avoid unnecessary divergence) |
| Shadows | A small elevation scale (flat, low, medium, high) — no per-component bespoke shadows |
| Borders / Radius | A constrained radius scale (sharp, subtle, rounded) applied consistently across all foundation components |
| Animation | Duration/easing tokens (fast/base/slow, standard easing curve) consumed by the single adopted animation library (§10.5) — no per-component ad hoc timing values |

### 10.3 Component System — Foundation Components

Foundation components live in `design-system/src/components/`, built on shadcn/ui primitives (§10.5) and Tailwind, exposed as the atomic layer referenced in §7.4:

Button, Input, Select, Modal (Dialog), Card, Table, Navigation (top-level nav), Sidebar, Command Palette, Toast, Loading States (Spinner, Skeleton).

Each foundation component:
- Consumes tokens only — no component hardcodes a color, spacing, or shadow value outside the token set.
- Exposes a fully typed prop contract (TypeScript), documented via TSDoc comments on the exported component, not a separate documentation site.
- Ships with a co-located Vitest + React Testing Library test asserting rendering and key interaction behavior (open/close, disabled state, keyboard activation).

### 10.4 Theme System & Dark Mode

- Dark mode is a first-class theme, not an afterthought toggle: both light and dark token sets are defined from day one, driven by a CSS-variable-based theme layer (Tailwind's `dark:` variant driven by a `class` strategy, not `prefers-color-scheme`-only, so the user can explicitly choose a theme independent of OS setting).
- Theme selection is persisted via a small piece of local UI state (Zustand, §12) — not business data, so it does not go through the capability bridge.

### 10.5 Technology Evaluation for the Design System

See the consolidated Technology Evaluation Matrix in §21. Summary as applied here: **shadcn/ui is adopted** as the foundation primitive layer (vendored source, not a runtime npm dependency — components are copied into `design-system/` and owned/modified directly, avoiding third-party supply-chain risk and fitting the existing "Technology Independence... hidden behind sandboxed adapters" principle from `ARCHITECTURE_VERSION_1.0.md` §5). **Motion.dev is adopted** as the single animation library for meaningful, purposeful transitions (route transitions, modal/toast enter-exit, command palette open). **Anime.js is rejected** as a second animation runtime — introducing two animation libraries has no offsetting benefit and directly contradicts the "avoid unnecessary dependencies" instruction. **21st.dev, Aceternity UI, and Skiper UI are reference-only** — used during design ideation for visual/interaction inspiration, never imported as live runtime dependencies; any adopted pattern is reimplemented inside the vendored `design-system/` package under KORTEX's own token and accessibility rules. **Haikei, Grainient, and Blobmaker are reference-only, design-time asset tools** — used to generate static SVG/PNG background assets checked into `design-system/assets/` once, never called at runtime (consistent with offline-first — no live third-party API calls from the running application).

### 10.6 Accessibility Requirements

- All foundation components meet WCAG 2.1 AA for color contrast (validated against the token palette, not per-instance).
- All interactive foundation components are keyboard-operable and expose correct ARIA roles/states (shadcn/ui's Radix UI foundation already provides this baseline; KORTEX must not regress it when re-skinning with tokens).
- Formal accessibility audit/certification is explicitly deferred (see §20 Non-Goals) — Phase 3 establishes the baseline, not a certified compliance report.

### 10.7 Component Documentation Approach

**Decision: no Storybook.** Storybook is rejected as an unnecessary dependency for this phase's scale — it duplicates what an in-app, dev-build-only "component gallery" route can provide at near-zero cost. A `/dev/components` route (compiled only into non-production builds via a Vite environment flag) renders every foundation component in its documented states, serving the same visual-QA purpose without adding a second build pipeline/tooling surface. This can be revisited if the component count grows to a scale that genuinely outgrows this approach — that revisit is out of scope for Phase 3.

---

## 11. Security Model

1. **Network egress isolation.** The webview holds no `http` or `websocket` capability grant (§6.6). All communication with the backend — request/response and streaming alike — is mediated by the Rust process. This is the single most important security property of this architecture: a compromised or malicious script running in the webview (e.g. via a supply-chain-compromised npm dependency) cannot reach the backend, or any network, on its own.
2. **Session token custody.** Session tokens issued by the Security Engine never enter JavaScript-reachable storage. They are held exclusively by the Rust process via OS-native secure credential storage (§8.4).
3. **Authorization is backend-owned, always.** RBAC/ABAC evaluation happens exclusively inside the Security Engine during Kernel dispatch. No permission decision is ever made in Rust or TypeScript (§8.5, §3 principle 4).
4. **Filesystem scope.** The webview/Rust pair may only touch the Tauri app-data directory (window state, theme preference, non-business local cache). All business data — files, objects, cached queries — flows exclusively through the Storage Engine's `IFileStore`/`IObjectStore`/`ICacheStore` abstractions on the backend side, per the already-ratified Storage Engine boundary (`storage_strategy.md`).
5. **Shell execution scope.** The only permitted `shell:execute` grant is spawning the exact bundled sidecar binary. No general-purpose shell execution capability is ever granted to the frontend or to Rust command handlers reachable from the frontend.
6. **Content Security Policy.** Production builds run under a strict CSP: no remote script/style origins, no `eval`, minimal `unsafe-inline` (only what the Tailwind build output strictly requires).
7. **Secrets never cross into the webview.** API keys, database credentials, and cryptographic material remain inside the Security Engine's `SecretStore` (already ratified) and are never serialized into any `UniversalResult` payload returned to the frontend.
8. **Audit trail continuity.** Every capability call originating from the desktop shell produces the same `UniversalAuditEntry` records the Security Engine's `AuditManager` already generates for any other caller — the desktop UI is not a special, less-audited code path.

---

## 12. State Management Strategy

### 12.1 Decision: Zustand for local/UI state; TanStack Query for server state. Redux is rejected.

| Concern | Chosen | Rejected | Why |
|---|---|---|---|
| Ephemeral/local UI state (theme choice, sidebar collapsed, active command-palette query, in-progress form draft before submission) | **Zustand** | Redux, Context API alone, Jotai | Zustand needs no boilerplate (no actions/reducers/middleware ceremony), no provider wrapping required, and its selector-based subscriptions avoid the re-render cost of a bare Context. Redux's centralized-store-plus-middleware model is unjustified ceremony here — Phase 3's UI-only state is small and does not need time-travel debugging or a middleware pipeline. Context API alone is rejected because it lacks selector-based partial subscriptions, causing broad re-renders as the app grows. Jotai's atomic model is rejected because it fits fine-grained, independently-composed state better than KORTEX's relatively small, cohesive UI-state surface — it would add conceptual overhead without a matching benefit at this scale. |
| Server-derived state (anything originating from a capability call: lists, records, capability metadata) | **TanStack Query** | Hand-rolled fetch + useState/useEffect, SWR | TanStack Query's request deduplication, background refetch, and cache invalidation map directly onto the retry/idempotency/timeout rules already ratified in `platform_service_contracts.md` §14–16 — the frontend doesn't need to reinvent them. SWR is a reasonable alternative but has a smaller ecosystem for the mutation-heavy, capability-invocation shape this app needs (optimistic updates keyed by `idempotencyKey`); TanStack Query's mutation API is a closer fit. |

### 12.2 The Hard Rule

Server-derived data is **never** copied into a Zustand store. A component that needs both "is the sidebar open" (Zustand) and "the current employee record" (TanStack Query) reads from two different systems by design — this is intentional, not an inconsistency, and prevents the single-source-of-truth violation that would occur if server data were mirrored into a second store.

### 12.3 Cache Invalidation on Events

When a `KortexEvent` relayed via §13 indicates a mutation relevant to cached query data (e.g. `kortex.event.hr.employee.updated`), the event-stream handler calls TanStack Query's `queryClient.invalidateQueries` for the matching query key — this is the single mechanism keeping the UI's cached view of server state consistent with backend reality without manual polling.

---

## 13. Event Streaming Strategy

### 13.1 Transport Decision

All real-time/streaming communication is relayed through Rust, mirroring the request/response decision in §11.1 — the webview never opens its own WebSocket connection.

1. Rust maintains a single persistent WebSocket connection to the backend's `/events/stream` endpoint (§9.2), authenticated with the same Rust-held session token (§8.4).
2. The backend, at handshake time, subscribes the connection to Event Engine topics scoped server-side by the caller's `tenant_id` and granted permissions — the client never supplies a trusted topic filter; any client-requested filter is treated as a hint, re-validated server-side.
3. On receiving a `KortexEvent`, Rust re-emits it into the webview via Tauri's event system: `app_handle.emit_all("kortex://event", payload)`.
4. The frontend subscribes once, centrally (in `app/`, not per-feature), via `listen("kortex://event", handler)`, and fans out to interested features by topic pattern matching — mirroring the wildcard subscription model already ratified in `event_bus.md` §4.

### 13.2 Reconnection

If the Rust-to-backend WebSocket connection drops (e.g. during a sidecar crash/restart per §6.4), Rust attempts reconnection with the same exponential backoff policy used for sidecar restarts. The frontend is informed of connection state (`connected`/`reconnecting`/`disconnected`) via a dedicated lightweight status event, and may show a small "reconnecting" indicator — this is UI feedback only, not a functional dependency (queued capability calls still work independently once the sidecar itself is healthy; only live event delivery is affected during the gap).

### 13.3 Correlation

Every relayed event carries its original `correlation_id` (already mandatory per `event_bus.md` §6), enabling the frontend to associate a background event notification (e.g. a long-running document render completing) with the user action that triggered it, per the `LongRunningOperation` protocol already ratified in `platform_service_contracts.md` §8.

---

## 14. Testing Strategy

| Layer | Tooling | Scope |
|---|---|---|
| Rust unit tests | `cargo test` | IPC command handlers (mocked sidecar HTTP client), sidecar process manager (spawn/health-check/restart/terminate logic), capability/permission file structure validation |
| Frontend unit/component tests | Vitest + React Testing Library | Foundation components (§10.3), feature hooks (with a test-double IPC client implementing the same typed contract as §8.2/§8.3, so component tests never touch a real Tauri runtime or network) |
| Contract consistency | OpenAPI-schema-driven type generation | Backend Pydantic models for `CapabilityRequest`/`UniversalResult`/`UniversalError` are exported as an OpenAPI/JSON-Schema document; frontend `IpcCapabilityRequest`/`IpcResultEnvelope`/`IpcError` TypeScript types are generated from that schema (tool choice open — e.g. `openapi-typescript`), not hand-duplicated, preventing contract drift between the two languages |
| E2E (smoke) | Tauri's WebDriver-based E2E tooling (`tauri-driver`) | A minimal smoke path: app launches → authenticates → performs one real capability round trip → renders result. Deliberately minimal at Phase 3 close, expanded incrementally afterward — the existing `backend/tests/e2e/` directory is currently empty and this establishes its first real content |
| Linting/type-checking | ESLint + `tsc --noEmit` (frontend), `cargo clippy` (Rust), existing Ruff/mypy (backend, unchanged) | Enforced in the same pre-commit/CI discipline already used for the backend |

No Jest is introduced alongside Vitest — Vite is already the bundler, and Vitest is Vite-native, so a second test runner would be a redundant dependency.

---

## 15. Development Environment

- **Monorepo layout addition**: a `pnpm-workspace.yaml` at the repository root, listing `apps/desktop` and `design-system` as workspace packages (this file does not exist yet; its creation is Phase 3 implementation work, not part of this specification).
- **Package manager**: pnpm, for workspace-protocol support and disk-efficient, strict dependency resolution (§7.1).
- **Local dev loop**: `tauri dev` launches the Rust shell, which starts the Vite dev server (HMR) for the webview and the Python backend directly from the local virtual environment (not the frozen sidecar binary) pointed at SQLite embedded local mode, per §6.7.
- **Editor/type-checking**: `tsconfig.json` (to be created) targets strict mode, consistent with the backend's own strict-typing discipline (`mypy`, Pydantic v2).
- **Pre-commit**: the existing `.pre-commit-config.yaml` is extended (implementation-time work, not this document) with frontend lint/format hooks (ESLint, Prettier or Tailwind's own class-sorting plugin) mirroring the rigor already applied to Python via Ruff/mypy.

---

## 16. Production Build Architecture

1. `vite build` produces static webview assets (`dist/`) with production optimizations (minification, tree-shaking, code-splitting per route).
2. The Python backend is frozen into a single sidecar executable (packaging tool choice open, §21) targeting each supported OS (Windows primary, given the observed development environment; macOS/Linux per the stack's stated cross-platform intent).
3. `tauri build` bundles the Rust binary, the built webview assets, and the frozen sidecar executable into a single signed application package per target OS (`.msi`/`.exe` for Windows in Phase 3; `.dmg`/AppImage packaging deferred to Phase 7 polish per `.kortex/roadmap.md`).
4. Production builds enforce: DevTools disabled, strict CSP, no webview network capability grants (§6.6), release-mode Rust compilation (`cargo build --release`).
5. Build reproducibility: the exact Tauri, Rust toolchain, Node, and pnpm versions used for a given release are pinned and recorded (mechanism — lockfiles plus a version manifest — is implementation detail, not specified further here).

---

## 17. Milestone Breakdown

### M1 — Tauri v2 Desktop Shell

- **Purpose**: Provide the desktop container: window lifecycle, sidecar supervision, secure IPC transport, OS integration — nothing else.
- **Existing**: Stated intent only (`apps/desktop/README.md`, `.kortex/stack.md`). No `src-tauri/`, no `Cargo.toml`, no `tauri.conf.json`.
- **Missing**: Full `src-tauri/` Rust project; `capabilities/default.json` per §6.6; sidecar spawn/health-check/restart logic (§6.3–6.4); window lifecycle wiring; updater plugin integration (§6.5); environment separation config (§6.7).
- **Dependencies**: None blocking to start Rust scaffolding itself; sidecar supervision requires a runnable backend entry point (§9.4, currently missing) before it can supervise anything real.
- **Completion Criteria**: Tauri app launches an empty window in dev and production modes; spawns and supervises the (initially minimal) Python sidecar; crash/restart logic verified by deliberately killing the sidecar process; permission file contains exactly the grants in §6.6, nothing more; graceful shutdown leaves no orphaned processes.

### M2 — React + TypeScript UI

- **Purpose**: Establish the frontend application shell, routing, and state-management foundation the eventual real screens will be built on.
- **Existing**: Stated intent only; state-management explicitly marked "TBD" in `apps/desktop/README.md` (resolved by this document, §12).
- **Missing**: Project scaffold (`package.json`, `tsconfig.json`, `vite.config.ts`); `apps/desktop/src/app/` root providers (QueryClient, ThemeProvider, Router per §7.2); `features/` structure; the typed IPC client (§8, shared work with M3); Vitest setup.
- **Dependencies**: M1 (a shell to render inside, at least in dev mode against the Vite dev server); the Design System (M4) should exist in at least token form before real screens are styled, though the app scaffold itself can proceed in parallel.
- **Completion Criteria**: TypeScript React app builds and renders inside the Tauri shell (dev and production); routing, Zustand, and TanStack Query are wired and demonstrated with at least one real end-to-end capability call (shared completion criterion with M3); ESLint/`tsc --noEmit` pass in CI-equivalent local checks.
- **Note**: Redux vs. Zustand and TanStack Router vs. React Router are resolved in §7.2/§12 — these are no longer open decisions requiring engineering judgment calls during implementation.

### M3 — IPC Bridge

- **Purpose**: Connect the desktop UI to the backend through the single capability-contract-compliant transport defined in §8.
- **Existing**: `backend/src/kortex/api/` contains only a `README.md` and empty `__init__.py` — zero implementation. Platform-level contracts this bridge must conform to (`platform_service_contracts.md`, `capability_registry.md`) are already ratified.
- **Missing**: Backend — `kortex.api.main:app` FastAPI entry point, `/capabilities/invoke`, `/events/stream`, `/health` (§9, flagged as a cross-cutting dependency in §18). Frontend/Rust — `invoke_capability` command handler, session-token custody wiring, event relay (§13), the typed `ipc/` client module, generated TypeScript contract types (§14).
- **Dependencies**: M1 (Rust shell to host the command handlers and sidecar), and the backend presentation layer described in §9 (does not currently exist — this is the hardest blocking dependency in the entire Phase 3 plan).
- **Completion Criteria**: At least one full round trip succeeds end-to-end (real screen → `invoke_capability` → FastAPI → Kernel dispatcher → a real engine → `UniversalResult` → rendered in the UI); at least one event-stream scenario works (a backend-originated event updates the UI without a manual refresh); session token never observably present in webview-accessible storage (verified by inspection); a permission-denied scenario is demonstrated to fail correctly end-to-end (not just mocked).

### M4 — KORTEX Design System

- **Purpose**: Provide the shared, reusable token/theme/component foundation consumed by the desktop UI and future KORTEX frontends.
- **Existing**: A 3-line placeholder `README.md`; no tokens, no components, no build configuration.
- **Missing**: `design-system/` package scaffold (workspace package, §15); Tailwind + token configuration (§10.2); vendored shadcn/ui foundation components (§10.3); dark-mode theme system (§10.4); Motion.dev integration for the animation set adopted in §10.5; the in-app `/dev/components` gallery route (§10.7, technically lives in `apps/desktop` but documents/exercises this package).
- **Dependencies**: Should exist in at least token + a handful of foundation components (Button, Input, Card) before M2's real screens are styled, to avoid rework; can otherwise proceed independently of M1/M3.
- **Completion Criteria**: Tailwind + token configuration committed; the eleven foundation components listed in §10.3 implemented, each with a co-located test and demonstrated reuse across at least two different screens; dark mode verified visually and via automated contrast checks against the token palette; the package is consumed by `apps/desktop` via the pnpm workspace protocol, not a copy-pasted duplicate.

---

## 18. Dependencies

1. **Cross-cutting hard blocker**: The backend presentation layer (§9) does not exist. M3 cannot reach its completion criteria until `kortex.api.main:app`, `/capabilities/invoke`, `/events/stream`, and `/health` exist. **This needs explicit scope confirmation**: is implementing this backend presentation layer inside the Phase 3 milestone boundary (as this document assumes, since it is the direct backend counterpart of the IPC bridge and is not one of Phase 2's five named engines), or does it belong to a separate backend-track milestone that must land before Phase 3 UI work starts? See §21 unresolved decisions.
2. M2 (styled real screens) depends on M4 having at least token-level output.
3. M3 depends on M1 (a Rust host for command handlers) and on the backend presentation layer (#1 above).
4. The sidecar packaging tool choice (PyInstaller vs. Nuitka vs. other, §21) blocks M1's production-mode completion criteria (dev-mode sidecar supervision does not require freezing).
5. Phase 3 has no dependency on Phase 4 (AI Engine) or later phases — no AI chat UI, business module screens, or marketplace UI are in scope (§20).

---

## 19. Risks

1. **Backend presentation layer does not exist yet** (§9, §18#1) — the single largest schedule risk for M3.
2. **Sidecar packaging is unproven in this repository.** Freezing a FastAPI + SQLAlchemy + async stack into a single executable (PyInstaller/Nuitka) carries known friction (hidden imports, async runtime quirks, binary size) that has not been validated here.
3. **No CI pipeline exists** (`.github/workflows` is absent repository-wide) — Phase 3's cross-platform build/test matrix (Rust + Node + Python together) has no automated safety net today; this risk predates Phase 3 but Phase 3 is the first phase where a broken cross-platform build becomes user-visible.
4. **Tauri v2 is a comparatively young major version** — API surface and plugin ecosystem (updater, keychain-backed storage) may still be evolving; pin exact versions and track upstream changelogs during implementation.
5. **Network-egress-isolation (§11.1) is a security property that must be verified, not assumed.** A misconfigured `capabilities/*.json` that accidentally grants `http`/`websocket` to the webview would silently defeat the architecture's central security guarantee. This must be an explicit review gate before M1/M3 sign-off, not an implicit assumption.
6. **Two independently-versioned deployment targets (desktop sidecar vs. headless server) sharing one FastAPI app** (§9.1) creates a risk of divergent behavior between modes if not tested against both from the start.

---

## 20. Non-Goals

Phase 3 explicitly does **not** include:

1. Any business module UI screens (Finance, HR & Payroll, Operations) — these belong to Phase 6.
2. Any AI chat / AI-assisted UI surface — depends on the Phase 4 AI Engine, which does not exist yet.
3. Multi-window or multi-monitor session management beyond a single primary application window.
4. Marketplace or plugin-installation UI.
5. A user-facing theme marketplace or theme editor (dark/light theme switching only, §10.4).
6. Production-grade installer polish, code-signing certificate provisioning, or staged rollout tooling — deferred to Phase 7 per `.kortex/roadmap.md`.
7. Conflict-resolution UI for offline/sync scenarios.
8. Formal accessibility certification (a WCAG AA-aligned baseline is established, §10.6, but not audited/certified).
9. Localization / internationalization — English-only baseline for Phase 3.
10. Telemetry/analytics dashboards — belongs to the Phase 7 Monitoring Engine.
11. A generic domain-specific REST API surface beyond the single `/capabilities/invoke` endpoint (§9.2) — no per-domain REST routes are required for Phase 3 closure.

---

## 21. Final Architecture Decisions

### 21.1 Technology Evaluation Matrix

| Technology | Category | Classification | Reason |
|---|---|---|---|
| Tauri | Foundation | **Adopt** | Already ratified in `.kortex/stack.md`; lightweight, Rust-based, fits local-first distribution |
| React | Foundation | **Adopt** | Already ratified in `.kortex/stack.md` |
| TypeScript | Foundation | **Adopt** | Already ratified in `.kortex/stack.md` |
| Tailwind CSS | Foundation | **Adopt** | Already ratified in `.kortex/stack.md`; doubles as the token system (§10.2) |
| shadcn/ui | Foundation | **Adopt** | Vendored source, not a runtime dependency — full ownership, zero supply-chain surface, fits "technology hidden behind sandboxed adapters" (§10.5) |
| Motion.dev | Animation | **Adopt** | Idiomatic React bindings; single animation library policy |
| Anime.js | Animation | **Reject** | Redundant second animation runtime; no offsetting benefit over Motion.dev |
| 21st.dev | UI Inspiration | **Reference only** | Design-time inspiration; never imported as a runtime dependency |
| Aceternity UI | UI Inspiration | **Reference only** | Same as above |
| Skiper UI | UI Inspiration | **Reference only** | Same as above |
| "UI UX Pro Max Skill" | Design Intelligence | **Reference only** | A design-ideation aid, not a runtime dependency or architectural component |
| Haikei | Asset Tools | **Reference only** | Design-time static asset generation only; no runtime/API calls (offline-first) |
| Grainient | Asset Tools | **Reference only** | Same as above |
| Blobmaker | Asset Tools | **Reference only** | Same as above |
| Zustand | State Management | **Adopt** | §12.1 |
| Redux | State Management | **Reject** | §12.1 |
| TanStack Query | Server State | **Adopt** | §12.1 |
| React Router | Routing | **Adopt** | §7.2 |
| TanStack Router | Routing | **Reject** | §7.2 — redundant with TanStack Query, unnecessary ceremony for a non-URL-driven desktop app |
| pnpm (workspaces) | Tooling | **Adopt** | §7.1, §15 — required for Design System reusability |
| Vitest | Testing | **Adopt** | §14 — Vite-native, avoids a second test runner |
| Jest | Testing | **Reject** | §14 — redundant given Vitest |
| Storybook | Documentation | **Reject** | §10.7 — an in-app dev-only component gallery route suffices at this scale |

### 21.2 Summary of Binding Decisions

1. Network egress to the backend is exclusively Rust-mediated; the webview holds no `http`/`websocket` capability (§6.6, §11, §13).
2. The IPC layer transports the existing `CapabilityRequest`/`UniversalResult` contract unchanged — it does not define a parallel contract system (§8.1).
3. Zustand for local UI state, TanStack Query for server state; the two are never mixed (§12).
4. React Router, not TanStack Router (§7.2).
5. Feature-based organization for the application; atomic/foundation organization only inside the Design System package (§7.4).
6. shadcn/ui vendored (not installed) as the foundation component base; Motion.dev as the sole animation library (§10.5).
7. No Storybook; an in-app dev-only component gallery instead (§10.7).
8. pnpm workspaces, with `design-system` as a shared package consumed by `apps/desktop` (§7.1, §15).
9. One uniform FastAPI presentation surface serves both desktop (via Rust) and headless server modes — no separate Python-side "Tauri adapter" (§9.1).

### 21.3 Unresolved Decisions Requiring Chief Architect / User Approval

1. **Ratification status of this document itself** — it is a proposal per the header; it requires explicit Chief Architect approval before Phase 3 implementation may cite it as authoritative, per the Change Management Policy already ratified in `ARCHITECTURE_VERSION_1.0.md` §22.
2. **Scope boundary of the backend presentation layer (§9, §18#1)** — whether implementing `kortex.api.main:app` and its required endpoints falls inside Phase 3's milestone boundary (as this document assumes) or should be sequenced as backend-track work landing before Phase 3 UI implementation begins.
3. **Sidecar packaging tool** (PyInstaller vs. Nuitka vs. another option) for freezing the Python backend — needs a spike/evaluation before M1's production completion criteria can be finalized (§6.3, §16, §19#2).
4. **Session-expiry error taxonomy addition** (`SESSION_EXPIRED` as an additive category alongside the existing `UniversalError` categories, §8.4) — should be confirmed against `platform_service_contracts.md`'s error model before implementation, since that document is itself a protected, ratified spec this document must not silently extend without sign-off.
5. **Whether `docs/architecture/README.md`'s index and `.kortex/roadmap.md` should be updated to reference this new document** — deliberately left untouched per this task's explicit constraint not to modify existing architecture documents; a follow-up action item once this document is approved.
