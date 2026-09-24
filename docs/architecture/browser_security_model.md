# KORTEX Browser — Security Model

**Status**: Living document — established Browser-B0 (principles and enforcement-point placement only; no implementation yet). Fully enforced starting Browser-B4.

## 1. The core rule

An arbitrary website loaded in KORTEX Browser must never automatically gain access to native KORTEX capabilities merely because it is loaded inside WebView2. **Browser Policy is the security boundary — the AI Browser Agent is not.** Every native-capability request that originates from page content, from a human click, or from a governed AI action must pass through the same enforcement point.

## 2. Recommended enforcement point (from Browser-B0 audit)

KORTEX's existing security architecture has exactly one enforcement chokepoint for every capability invocation today: `CapabilityDispatcher.dispatch()` (`backend/src/kortex/core/dispatch.py:352-542`), which derives tenant identity only from a verified `SecurityPrincipal` (never a caller-supplied field), freezes it into an immutable `CapabilityExecutionContext` built exactly once per request, and calls `SecurityEngine.authorize()` before anything executes.

Browser Policy should be modeled as a **fifth, independent enforcement point analogous to `SecurityEngine`/`CapabilityProjection`** — not logic bolted onto the AI agent, and not logic bolted onto the WebView2 host object itself:

- The loaded page's origin/profile identity must be a dispatcher-constructed, immutable context value — never something the page or the AI agent can assert about itself.
- Any native-capability request whose origin traces back to page content is routed through the same `CapabilityDispatcher.dispatch()` path used everywhere else, with Browser Policy acting as an additional, mandatory authorization check that runs before capability projection — **default-deny for page-initiated calls**, allow-listed per profile/origin only.
- This mirrors, and reuses, `SecurityEngine.authorize()`'s existing role — it does not replace or duplicate it.

## 3. Domain & navigation policy

Deferred to B4 design, but must define: which domains a given profile/tab may navigate to by default, how a governed AI action's `browser.navigate` differs in allowed scope from a human-driven navigation, and how redirects/cross-origin navigations inside a single page load are treated (a page redirecting itself is not a new "navigation policy decision" in the same sense as a fresh `browser.navigate` call).

## 4. JavaScript / native bridge policy

The existing `devBrowserBridge` (`apps/desktop/src/ipc/devBrowserBridge.ts`) is **not** prior art for this boundary — the Browser-B0 audit confirmed it is dev-only tooling that lets `pnpm dev` run in a plain browser tab by mocking Tauri's own `invoke()`, environment-gated to `DEV` builds, and does no capability allowlisting of its own (all authorization happens server-side). It is useful precedent for *credential compartmentalization* (it keeps session and refresh tokens in separate storage slots, per its "Phase F security correction," and never attaches a refresh token as a Bearer credential) but not for the "untrusted third-party page must not reach native capabilities" problem, which has no existing implementation to point to.

**Update from Browser-B1**: part of this base default-deny turns out to already be provided by Tauri's own ACL model, verified by direct source inspection (not assumed) when implementing `capabilities/browser.json`:
- A capability's `Capability.windows` field grants its permissions to **every webview embedded in that window** (per `tauri_utils::acl::capability::Capability`'s own doc comment) — so a capability must scope via `Capability.webviews` (a specific webview label) rather than `windows` whenever a window can contain an untrusted child webview, or every child surface silently inherits the parent window's full grant. `browser.json` uses `"webviews": ["main"]`.
- Independently, `tauri::ipc::authority::Origin::matches` unconditionally denies a `Remote`-origin caller against any capability resolved to `ExecutionContext::Local` (the default, when no `remote` field is declared). Every browser surface always loads via `WebviewUrl::External` — a `Remote` origin — so it can never satisfy `default.json` or `browser.json`'s grants regardless of window/webview label matching, as long as neither file ever declares a `remote` field.

This means the *base* "page-loaded JavaScript has no bridge to any native capability" default is real and already verified as of Browser-B1, not merely designed-but-unbuilt. What Browser-B4 still owns: any *legitimate* per-origin/per-profile allow-listing a future capability might need (this static ACL has no concept of "which profile" or "which tenant"), and the dynamic Browser Policy enforcement point for governed `kortex.browser.*` AI actions (§2) — a separate system from this static, build-time ACL. See `docs/architecture/browser_b1_implementation_report.md` §Security and `browser_decision_log.md` D10 for the full mechanism.

## 5. Download / upload policy

Deferred to B4. The runtime (`IKortexBrowserRuntime`) only *reports* a download/upload request; Browser Policy decides whether it proceeds, at what destination, and whether it requires the durable human-approval flow (§7) rather than a simple client-side confirm dialog.

## 6. AI action policy

Governed browser actions (`browser.navigate/read/click/type/extract/download/screenshot`) reuse the existing `is_mutation`-gated approval chain (`ToolGovernanceEvaluator`, `DurableAIApprovalPolicy`, `AIOrchestrationEngine._on_approval_decided`, `TenantConcurrencyThrottler`) — see `browser_capability_model.md` for capability-level detail. Because website content is untrusted, the audit recommends **not** relying solely on each action's literal `is_read_only` flag to decide approval-worthiness: `browser.navigate`, `.click`, and `.type` should default to being treated as always-mutating regardless of apparent read intent, since a page can trigger side effects a static classification can't see in advance.

## 7. Sensitive-action confirmation

Two existing patterns, neither sufficient alone for Browser:

1. **Client-side confirm dialog** (e.g. the AI Studio "remove confirmation" pattern, `ProviderConfigCard.tsx:210-246`) — a UX courtesy only, no security guarantee by itself, since the real gate is the backend capability call it triggers. A page-triggered confirm dialog is exactly the clickjacking/phishing vector Browser Policy must prevent, so this pattern alone must never be the sole gate for a capability grant that originates from page content.
2. **Durable workflow approval** (`DurableApprovalManager`, `workflow/approval.py:235+`, Ed25519-signed decisions) — the pattern for governed/reversible AI actions requiring a human decision. Any capability grant to browser-originated content that is meant to be human-governed should go through this pattern, not (1) alone.

## 8. Audit logging

Reuse `AuditManager`/`UniversalAuditEntry` unchanged (`backend/src/kortex/engines/security/audit.py`) for every browser-originated capability attempt, **including denials** — this is exactly the kind of security-relevant event the existing fail-closed audit trail already exists to capture (auth success/failure and execution completion are both audited today via `CapabilityDispatcher._audit_authentication_success/_failure`, `._audit_execution`).

## 9. Tenant isolation & credential isolation

Follows the existing pattern exactly: tenant identity derived only from the verified `SecurityPrincipal`, never from a caller-supplied field; any browser-related secret (a future Web Account session blob, see `browser_auth_architecture.md`) stored through `SecretStore`, whose AES-256-GCM AAD already cryptographically binds `tenant_id` into the ciphertext (`security/secrets.py:159-179`) so it cannot be decrypted under the wrong tenant even if the wrong row were somehow read.

## 10. Browser-profile isolation & persistence

**A WebView2 browser profile does not fit KORTEX's existing persistence model as-is.** KORTEX's isolation primitives — SQL row-level `tenant_id` columns and AAD-bound encryption via `SecretStore` — assume the app owns and interprets the data. A WebView2 profile is an opaque, WebView2-owned user-data folder (cookies DB, IndexedDB/LevelDB, HTTP cache) that must be a real, exclusive OS directory per profile; it cannot be decomposed into DB rows or the existing shared-`base_directory` file sandbox (`storage/sandbox.py`'s `PathSandboxValidator` currently sandboxes all file access under **one shared** base directory, with no built-in per-tenant subdirectory convention).

This requires a **new, separate on-disk store construct** — e.g. a `BrowserProfileStore` abstraction owning one exclusive WebView2 user-data-folder path per tenant/profile — sitting alongside, not inside, KORTEX's existing four storage facades (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`). Design and land this in B3, not before.

**Refined by the Browser-B1 preflight**: Tauri's own `WebviewBuilder::data_directory(data_directory: PathBuf)` (`webview/mod.rs:963`, confirmed stable in the pinned `tauri = "=2.11.5"`) already gives a first-class hook for setting a webview's WebView2 user-data folder — `BrowserProfileStore` is therefore a **path-allocation/tenant-mapping layer over this existing Tauri hook**, not a from-scratch storage mechanism. The security-relevant constraint this adds: `data_directory()` must always be called with a Rust-computed `PathBuf`, resolved from a validated profile/tenant identifier — the frontend's `browser/api.ts` must never pass a raw filesystem path across the Tauri IPC boundary, mirroring `PathSandboxValidator`'s existing validate-before-use discipline. See `docs/architecture/browser_b1_preflight_report.md` §8.

## 11. Prompt-injection boundary

Page content that reaches the AI Browser Agent (via `browser.read`/`.extract`) is untrusted input, structurally no different from any other tool output the AI Engine already scrubs and truncates (`ToolResult.to_context_entry`, `backend/src/kortex/engines/ai/tools.py`). The Browser-B0 audit identifies this as the **one genuinely new component** the rest of the architecture doesn't already provide off the shelf: a prompt-injection-aware sanitization step for extracted page content, applied before it re-enters the LLM's context, on top of the existing secret-scrubbing/truncation backstop. Design in B6, adversarially test in B9.

## 12. Permission escalation rules & external trust boundaries

A website's own requested permission (e.g. clipboard, camera, geolocation, via WebView2's native permission-request APIs) is a request to Browser Policy, never an automatic grant, and never conflated with a KORTEX capability grant — a website being allowed to read the clipboard (a WebView2-native permission) must never be treated as equivalent to a KORTEX AI action being allowed to invoke `browser.read`. These are two different permission systems that must not be allowed to escalate into each other. Full rules deferred to B4.

## 13. Not yet answered (explicitly, per Browser-B0 scope)

This document establishes principles and enforcement-point placement only. It intentionally does not yet specify: the exact origin/profile allowlist format, the exact `BrowserProfileStore` schema, the exact prompt-injection sanitizer design, or CAPTCHA/Turnstile handling (which is explicitly never to be bypassed — see `browser_auth_architecture.md`). See `browser_known_limitations.md` and `browser_decision_log.md`.
