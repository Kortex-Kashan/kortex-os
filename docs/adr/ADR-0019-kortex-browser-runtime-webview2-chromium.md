# ADR-0019: KORTEX Browser Runtime — WebView2 + Chromium Behind an IKortexBrowserRuntime Abstraction

Status: ACCEPTED
Date: 2026-09-25
Author: Claude Code (KORTEX Browser Phase B0 architecture audit), recording the architectural decision issued by the project owner (Chief Architect, KASHAN) in the B0 task brief for this session
Authority: Chief Architect (KASHAN)
Reference Architecture: KORTEX OS Architecture Version 1.0.0; `docs/architecture/phase3_desktop_architecture.md` (Tauri desktop shell); `docs/architecture/browser_architecture.md` (companion living architecture document)

---

## 1. Context & Problem Statement

KORTEX OS is starting a new capability: an embedded, built-in web browser inside the desktop shell. This activates a long-deferred roadmap line item (`.kortex/roadmap.md:383` — "the Built-in Browser... remain[s] exactly as previously deferred elsewhere in this repository's documentation and [is] not promoted into release blockers" by the most recent RC reconciliation pass) and is distinct from the unrelated "browser automation" (Playwright/Puppeteer) concept already declared out of scope for *desktop UI automation* in Phase 6 (`docs/architecture/phase5_locked_architecture_spec.md:32,681,717`).

The desktop shell today (`apps/desktop/src-tauri`) is a single-window Tauri application with a deny-by-default webview capability model — the main webview has no `http`/`websocket` grant (`apps/desktop/src-tauri/capabilities/default.json:6-15`; `docs/architecture/phase3_desktop_architecture.md:183,446`). It has never embedded external, untrusted web content. A decision is needed on the runtime engine and the abstraction boundary between it and the rest of KORTEX before any implementation (Browser-B1 onward) begins.

---

## 2. Decision Drivers

- Preservation of the existing deny-by-default, Rust-mediated network/IPC isolation model.
- Local-first/offline-first principle: the runtime should be embeddable without a heavy new bundled dependency wherever possible.
- Long-term portability: a future macOS/Linux desktop build is already an open item in `docs/release/RELEASE_CANDIDATE_READINESS.md`; the runtime must not be hard-wired into the rest of KORTEX.
- Browser is a KORTEX capability/runtime, not an AI provider — no architectural coupling to the Provider Registry.

---

## 3. Considered Options

- **Option 1 (chosen)**: WebView2 (Chromium-based, Microsoft Edge WebView2 runtime) as the V1 concrete runtime, embedded via a new Rust adapter in the existing Tauri shell, behind a new `IKortexBrowserRuntime` abstraction that the rest of KORTEX depends on instead of any WebView2-specific API.
- **Option 2**: Embed Chromium Embedded Framework (CEF) directly. Rejected for V1 — heavier distribution footprint than relying on the OS-shared WebView2 runtime, no existing precedent in this codebase, larger installer-size impact (relevant given the existing MSI/NSIS installer smoke tests in `desktop-ci.yml`).
- **Option 3**: Reuse Tauri's own existing webview (already rendering the KORTEX UI) to also render arbitrary third-party pages. Rejected — that webview is scoped by `capabilities/default.json` to KORTEX's own first-party renderer; loading untrusted content into that same principal would either force widening its capability grants (unacceptable) or require retrofitting an entirely separate isolation mechanism onto it, strictly harder than starting a new, separately-scoped runtime.
- **Option 4**: Open all "browse the web" needs in the OS's default external browser — the existing pattern for OAuth sign-in (`apps/desktop/src/auth/OAuthLoginButtons.tsx:57`). Rejected as the general solution — it cannot support an embedded, governed, AI-observable browsing surface inside KORTEX, which is the feature being requested.

---

## 4. Decision Outcome

**Chosen Option**: Option 1 — WebView2 + Chromium as the V1 concrete runtime, never referenced directly outside a new `IKortexBrowserRuntime` abstraction; V1's concrete implementation is `WebView2RuntimeAdapter`. Future adapters (`CefRuntimeAdapter`, `ChromiumRuntimeAdapter`, or a Linux/macOS-native equivalent) are explicitly anticipated and must implement the same interface without requiring changes to any KORTEX code that depends on it.

### Decision Rationale

WebView2 is the lowest-friction Chromium-class runtime on the platform KORTEX ships on first (Windows, per the existing MSI/NSIS installer pipeline), and can be embedded as a new, separately-scoped Tauri child webview rather than widening the trust boundary of the existing first-party renderer (see `docs/architecture/browser_architecture.md` §2). Hiding it behind `IKortexBrowserRuntime` from day one avoids repeating the pattern this repository already had to work around once: hard-coding an automation/rendering engine into the rest of the system before a clean abstraction existed (cf. Phase 6's rejection of Playwright/Puppeteer as the desktop-automation engine, in favor of FlaUI/UIA3 behind the Desktop Agent's own contract).

---

## 5. Architectural Impacts & Consequences

### Positive Consequences

- The rest of KORTEX (capability layer, AI agent, policy engine) is written once against `IKortexBrowserRuntime` and never needs to change if the concrete runtime changes later.
- V1 ships on Windows without bundling a full browser-engine binary.
- Keeps the existing webview's deny-by-default capability grant completely untouched — the new runtime is additive, not a widening of an existing trust boundary.

### Negative Consequences / Trade-offs

- WebView2 is a Windows-only mechanism; a future macOS/Linux desktop build will require a second adapter before the Browser feature is available cross-platform. Accepted as a scoped, known V1 gap, not a defect.
- Introduces a second embedded web-rendering surface in the process, with its own memory footprint and its own on-disk profile-storage requirement (see `docs/architecture/browser_security_model.md` §10).

---

## 6. Compliance & Audit Verification

- `docs/architecture/browser_architecture.md`, `browser_security_model.md`, `browser_capability_model.md`, `browser_auth_architecture.md` are the living specification files this ADR is verified against.
- No code implementing `IKortexBrowserRuntime` or `WebView2RuntimeAdapter` exists yet; Browser-B1 is the first phase authorized to add it, and must conform to the interface and isolation boundary this ADR and its companion architecture document establish.
- Automated test coverage requirements are specified per-phase in `docs/architecture/browser_roadmap_b0_b10.md`.
