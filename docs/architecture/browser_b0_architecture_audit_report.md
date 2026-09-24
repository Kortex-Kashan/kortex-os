# KORTEX OS — Browser-B0 Architecture Audit Report

## Architecture Audit + Documentation Foundation

---

## 1. Executive Summary

This report records a read-only architecture audit of the KORTEX OS repository, conducted to find the cleanest integration seam for a future built-in browser (KORTEX Browser) before any implementation begins. No source code, dependencies, CI configuration, or unrelated documentation was modified. Six parallel investigation tracks (Tauri/frontend/IPC, .NET Desktop Agent, AI Engine/Provider Registry, capability/action architecture, security/audit/persistence, documentation/roadmap/CI conventions) each produced file:line-cited findings, synthesized here and distributed across a new living documentation set (§14).

**Headline finding**: KORTEX OS already has every architectural pattern this feature needs, except two genuinely new components — a `BrowserProfileStore` for WebView2's opaque profile data (§9), and a prompt-injection-aware sanitizer for extracted page content (§10). Everything else (capability registration, approval/governance, audit logging, tenant isolation, the feature-module frontend convention) is directly reusable as-is.

## 2. Starting Repository State

- Branch `main`, up to date with `origin/main`, HEAD `0410845` at audit start.
- Pre-existing, unrelated uncommitted changes to `.kortex/roadmap.md`, `CHANGELOG.md`, and `docs/release/RELEASE_CANDIDATE_READINESS.md` (a separate "Release Candidate Reconciliation Audit" pass), plus an untracked `scratch/` directory of debugging scripts. Confirmed with the project owner before starting; none of these were touched by this audit (see `browser_decision_log.md` D2's note on the reconciliation pass).
- `graphify-out/graph.json` present; all six investigation agents were instructed to orient via graphify before reading raw source.

## 3. Method

Six general-purpose research agents ran in parallel, each scoped to one investigation track from the task brief's 18-point list, each required to cite concrete `file:line` evidence for every claim and forbidden from editing any file. Findings below are synthesized from their reports; nothing here goes beyond what was verified by opening the cited files.

## 4. Repository Architecture Findings, by Track

### 4.1 Tauri desktop + frontend + native↔web communication

- `apps/desktop/src-tauri` is a single Rust crate (`kortex_desktop_lib`) with a flat module layout (`lib.rs`, `ipc.rs`, `events.rs`, `sidecar.rs`/`backend_process.rs`, `secure_keys.rs`). Plugins: `tauri-plugin-single-instance`, `tauri-plugin-deep-link` (`kortex-auth://`), `tauri-plugin-shell` (scoped `shell:allow-open` only, documented as existing solely to launch the OS browser for OAuth — never arbitrary shell execution).
- `tauri.conf.json` declares one window (`"windows": ["main"]`); permissions are deny-by-default (`capabilities/default.json:6-15`) — no `http`/`websocket` grant to the webview.
- IPC: `apps/desktop/src/ipc/client.ts:59-63` `invokeCapability()` → Rust `invoke_capability` command → loopback HTTP `POST /capabilities/invoke` to the local Python backend. Streaming via `emit_all("kortex://event", ...)` / `listen(...)`. Every feature owns its own `api.ts` wrapper; feature code never calls `invoke()` directly.
- Frontend is feature-based (`apps/desktop/src/features/<name>/{components,hooks,api.ts}`); `DesktopShell.tsx` composes `TopBar + AppSidebar + Workspace + StatusBar`, with `MiniChatHost` mounted as a persistent sibling of the route outlet.
- **`devBrowserBridge` is not embedded-browser prior art** — it's a dev-only IPC mock for running `pnpm dev` in a plain browser tab, environment-gated to `DEV` builds, with no capability allowlisting of its own.
- **Existing external-navigation precedent (OAuth)** opens the OS default browser via `@tauri-apps/plugin-shell`'s `open()`, with the callback caught via the `kortex-auth://` deep link. No iframe or embedded webview exists anywhere in the current codebase.
- **A disabled, unwired navigation slot already exists**: `apps/desktop/src/shell/navigation/navConfig.ts:40` — `{ id: "browser", label: "Browser" }`, added during an earlier visual-redesign pass.

### 4.2 .NET Desktop Agent + native communication

- `apps/desktop-agent/` runs as a LocalSystem Windows Service (`Program.cs:3`), self-supervising its own gRPC session loop with exponential backoff.
- gRPC contract (`agent_gateway/protos/agent.proto`): one RPC (`Connect`, bidirectional stream) carrying a closed `oneof` of narrow typed commands — deliberately no generic "execute" message.
- New capabilities are added by: (1) a new proto message, (2) a new engine method on `DesktopAutomationEngine` registering via `kernel.register_capability(..., requires_execution_context=True, security_classification="CONFIDENTIAL")`, (3) a new `Handle(...)` overload in `DesktopAutomationHandler.cs`.
- The Python execution sandbox (AppContainer + Job Object + restricted token) is a wholly separate system from the Desktop Agent, which itself runs unsandboxed as LocalSystem and relies on a closed wire protocol plus an application allow-list.
- Full call chain confirmed: Tauri IPC → `CapabilityDispatcher.dispatch()` → `SecurityEngine.authorize()` → `DesktopAutomationEngine` → `AgentGatewayEngine.send_desktop_command()` → mTLS gRPC → `.NET` command handler → FlaUI/UIA3.
- **Recommendation confirmed**: Browser should reuse the governance *pattern* (closed command set, `register_capability`, fail-closed target resolution), not the mTLS/gRPC *transport* — that layer authenticates a remote, separately-installed machine identity, a problem the in-process Browser runtime doesn't have.

### 4.3 AI Engine, Provider Registry, credential architecture

- `ProviderRegistry` is an in-memory, process-global catalogue, explicitly never a tenant credential store. Credentials live in `SecretStore` (AES-256-GCM, tenant-bound AAD), resolved per-request with no caching via `TenantCredentialResolver`.
- **Auth method is currently a fixed, provider-level constant, not a per-tenant runtime choice**: `credential_requirement` is hard-coded once per provider (`"api_key"` for OpenAI/Anthropic/Gemini/OpenRouter, `"none"` for Ollama); `"oauth"` is a defined-but-unused enum value.
- The existing OAuth deep-link mechanism (Phase A account sign-in, reused for GitHub connector OAuth) opens the OS default browser and catches a signed-state callback via a custom URI scheme — the callback/state-verification half is reusable for a future controlled auth window; the window-hosting half is not, since it deliberately opens an *external, uncontrolled* browser today.
- **Recommendation confirmed**: a future `auth_method` field on `AIProviderConfig` (default `"api_key"`), not a repurposed `credential_requirement`, is the correct home for "Web Account" auth.

### 4.4 Action/capability architecture

- `CapabilityDescriptor` is the single source of truth for every capability, fail-closed by design (`is_read_only`/`is_idempotent` both default `False`; unparseable `security_classification` fails closed to `RESTRICTED`).
- Every mutating AI action is gated by one shared chain: `ToolGovernanceEvaluator` → `DurableAIApprovalPolicy` → durable ticket → async `workflow.approval.decided` event → `AIOrchestrationEngine._on_approval_decided` (re-verifies an action fingerprint, then resumes through `TenantConcurrencyThrottler`). No subsystem writes its own approval/throttling logic.
- `CapabilityProjection` filters visibility strictly by verified `SecurityPrincipal`, never caller-supplied tenant data, failing closed via `CapabilityNotFoundError` to avoid metadata leakage.
- The Connector/Document/Knowledge pattern (Kernel capability → `generate_tool_definition_from_capability()` → idempotent `ToolRegistry` registration) is the exact template `kortex.browser.*` should follow.
- **Recommendation confirmed**: treat `browser.navigate/click/type/download` as mutating by default regardless of apparent read intent, since untrusted page content can trigger side effects a static classification can't rule out; a new prompt-injection-aware sanitizer is the one genuinely new component needed beyond the existing secret-scrubbing/truncation backstop.

### 4.5 Security, audit, persistence, isolation

- Tenant isolation is enforced once, centrally, in `CapabilityDispatcher.dispatch()` — tenant identity derived only from the verified principal, frozen into an immutable execution context; row-level DB scoping plus AAD-bound encryption reinforce it independently.
- Audit logging (`AuditManager`/`UniversalAuditEntry`) is structured, durable-first (publish failures never roll back the write), and already invoked at both authentication and execution boundaries.
- Two confirmation patterns exist: a client-side confirm dialog (UX courtesy only, no security guarantee) and a durable, Ed25519-signed workflow-approval flow (the pattern for anything meant to be human-governed).
- **Persistence does not fit a WebView2 profile as-is**: KORTEX's isolation primitives (SQL rows + AAD encryption) assume the app owns and interprets its data; a WebView2 profile is an opaque, exclusive OS directory that needs a new, separate on-disk store construct.
- **Recommendation confirmed**: Browser Policy should be a fifth, independent enforcement point analogous to `SecurityEngine`/`CapabilityProjection`, routed through the same dispatcher, default-deny for page-initiated capability requests.

### 4.6 Documentation, roadmap, and CI conventions

- `docs/architecture/` is flat, ~55 files, with a consistent implementation-report house style (numbered sections, tables, a closing bolded readiness verdict) — no per-feature subfolder convention exists.
- `.kortex/roadmap.md`'s numbering is explicitly, self-declared non-sequential (`Phase 1`–`7`, a separate `M7.x` track, and a newly added "Phase A" section) — directly informing the decision to always fully-qualify "Browser-B0" etc. (`browser_decision_log.md` D2).
- **The "Built-in Browser" line item this project activates is already recorded as deferred and not an RC blocker** (`.kortex/roadmap.md:383`); a separate, unrelated "browser automation (Playwright, Puppeteer)" concept is an explicit Phase 6 non-goal for desktop UI automation, superseded by FlaUI/UIA3 — the two must not be conflated (`browser_decision_log.md` D3, D4).
- CI: `backend-ci.yml` and `desktop-ci.yml` are the two existing workflows; a future Browser CI check needs the same shape — a named job block producing a citable run ID, referenced by name in roadmap/changelog evidence sections.
- Recommended, and used, documentation location: flat files in `docs/architecture/` with a `browser_` prefix, matching the existing `ai_engine_*`/`m7.*_*` convention. A formal architectural decision (WebView2 + Chromium) was additionally recorded as `docs/adr/ADR-0019`, following this repository's existing ADR process (`docs/adr/README.md`).

## 5. Existing Integration Points

- `apps/desktop/src-tauri/lib.rs` — where a new `browser_runtime` module would register alongside `ipc`/`events`.
- `apps/desktop/src-tauri/capabilities/default.json` — the narrow-grant convention a new Browser capability file should mirror, not extend.
- `apps/desktop/src/shell/navigation/navConfig.ts:40` — the pre-existing, unwired `{ id: "browser" }` nav slot.
- `apps/desktop/src/shell/DesktopShell.tsx` — the persistent-sibling pattern (`MiniChatHost`) a browser tab strip should follow.
- `backend/src/kortex/engines/registry/engine.py`, `api/capability_tool_bridge.py`, `api/kernel_bootstrap.py` — the capability/tool registration pattern `kortex.browser.*` reuses verbatim.
- `backend/src/kortex/core/dispatch.py` (`CapabilityDispatcher.dispatch()`) — the single enforcement chokepoint Browser Policy plugs into as a new, independent check.
- `backend/src/kortex/engines/ai/models.py` (`AIProviderConfig`) — where a future `auth_method` field extends, without breaking, existing API-key rows.

## 6. Recommended WebView2 Integration Seam

A new Rust module (`apps/desktop/src-tauri/src/browser_runtime.rs`) implementing `IKortexBrowserRuntime` as Tauri commands, registered in `lib.rs`, with its own narrowly-scoped capability file — rendered as an embedded child WebView (not a second top-level window), matching the single-primary-window model and the existing persistent-sibling UI pattern. **Not yet verified**: whether the pinned Tauri version supports true multi-webview embedding (`browser_decision_log.md` OD-B3) — confirm at the start of B1.

## 7. Proposed Browser Module/Package Structure

```
apps/desktop/src-tauri/src/browser_runtime.rs        (Rust: IKortexBrowserRuntime + WebView2RuntimeAdapter)
apps/desktop/src-tauri/capabilities/browser.json     (new, narrow Tauri capability grant)
apps/desktop/src/features/browser/
  ├── components/
  ├── hooks/
  └── api.ts
backend/src/kortex/engines/browser/                  (future, B4+: Browser Policy + kortex.browser.* capabilities)
```

## 8. Proposed `IKortexBrowserRuntime` Abstraction

See `browser_architecture.md` §2.2 for the full interface responsibility breakdown (lifecycle, navigation, content access, input, downloads, profile binding) — deliberately mechanical, with all policy decisions layered above it.

## 9. Proposed `WebView2RuntimeAdapter` Responsibility

Implements the interface against Microsoft Edge WebView2, owns the embedded child-webview lifecycle, and reports (never decides) download requests and permission prompts up to Browser Policy. See `browser_architecture.md` §2.3.

## 10. Browser Security Boundary

Browser Policy as a fifth, independent enforcement point alongside `SecurityEngine`/`CapabilityProjection`, routed through the existing `CapabilityDispatcher.dispatch()`, default-deny for page-initiated capability requests. Full detail in `browser_security_model.md`.

## 11. Browser ↔ AI Boundary

AI Browser Agent is a consumer of governed tool calls through the existing approval/governance chain — it is explicitly **not** the security boundary. See `browser_architecture.md` §3.

## 12. Browser ↔ Action/Capability Boundary

New `kortex.browser.*` owner domain, registered via the existing Connector/Document/Knowledge pattern, with conservative `is_read_only` defaults. See `browser_capability_model.md`.

## 13. Browser ↔ Provider Boundary

Browser is a runtime/capability, never a provider. Provider/auth-method/model separation is preserved and extended (not collapsed) via a future `auth_method` field. See `browser_auth_architecture.md`.

## 14. Browser ↔ Tauri/.NET/Frontend Boundary

In-process with the Tauri app; does not require the .NET Desktop Agent's mTLS/gRPC transport. See `browser_architecture.md` §4.

## 15. Browser Documentation Structure (files created by this phase)

| File | Covers |
|---|---|
| `docs/adr/ADR-0019-kortex-browser-runtime-webview2-chromium.md` | Formal architecture decision (WebView2 + Chromium, `IKortexBrowserRuntime`) |
| `docs/architecture/browser_vision.md` | Vision, scope, explicit non-goals |
| `docs/architecture/browser_architecture.md` | System placement, runtime abstraction, AI boundary, automation architecture |
| `docs/architecture/browser_security_model.md` | Security boundary, all policy categories from the task brief |
| `docs/architecture/browser_capability_model.md` | Governed action set, registration pattern, classification table |
| `docs/architecture/browser_auth_architecture.md` | Current provider/credential architecture, future Web Account direction |
| `docs/architecture/browser_roadmap_b0_b10.md` | Full B0–B10 phase breakdown, repeatable per-phase structure |
| `docs/architecture/browser_decision_log.md` | Append-only decision record + open owner-decision items |
| `docs/architecture/browser_known_limitations.md` | Explicit known gaps, per phase |
| `docs/architecture/browser_b0_architecture_audit_report.md` | This report |

Also updated: `docs/adr/README.md` (index row for ADR-0019 — a normal part of this repository's existing ADR-authoring convention, not a change to unrelated content).

## 16. B0–B10 Roadmap

See `docs/architecture/browser_roadmap_b0_b10.md` for the complete, per-phase PHASE/STATUS/OBJECTIVE/.../NEXT PHASE breakdown.

## 17. Verification Strategy

Per `CLAUDE.md`'s milestone workflow, no future Browser phase is complete merely because its own tests pass. Each phase (B1 onward) must pass through Specification → Dependency Investigation → Implementation → Targeted Tests → Adversarial Review → Dependency-Chain Review → Remediation → Full Regression → Final Architectural Gate → Owner GO/NO-GO, exactly like every other KORTEX milestone.

## 18. Known Risks

- WebView2 is Windows-only; cross-platform Browser support requires a second adapter not yet designed (ADR-0019 §5).
- The prompt-injection sanitizer (§4.4, §10) is genuinely new and untested territory for this codebase — it is the highest-risk single component in the entire roadmap and should not be rushed in B6.
- `BrowserProfileStore` (§4.5) introduces a new class of on-disk, non-DB tenant isolation that the existing security test suite has no precedent for — B3's tests must be written from first principles, not adapted from `test_capability_projection_security.py`.
- Tauri multi-webview support is unverified (`browser_decision_log.md` OD-B3) — a B1 technical spike could reveal the recommended embedding approach isn't available in the pinned Tauri version, requiring a design change before implementation.

## 19. Unresolved Architectural Decisions

See `browser_decision_log.md` "Open items pending owner decision" (OD-B1, OD-B2, OD-B3) for the complete list: constrained script execution in the runtime's read primitive, whether Browser-B10 folds into the main RC process, and Tauri multi-webview support.

## 20. Exact B1 Implementation Plan

1. **Spike first**: confirm the pinned Tauri version's multi-webview/child-webview embedding support (resolves OD-B3) before committing to the exact embedding mechanism in `browser_architecture.md` §2.3.
2. Add `apps/desktop/src-tauri/src/browser_runtime.rs` implementing an `IKortexBrowserRuntime` Rust trait: `create_surface(profile_id)`, `dispose_surface(handle)`, `navigate/back/forward/reload(handle)`, `get_navigation_state(handle)`, `read_content(handle)`, `screenshot(handle)`, `dispatch_click/type(handle, target)`, `on_download_requested(handle)`.
3. Register new Tauri commands in `lib.rs` (e.g. `browser_create_surface`, `browser_navigate`, `browser_dispatch_input`), each backed by a new capability file (`capabilities/browser.json`) granting only these new commands — no widening of `shell:*` or `default.json`.
4. Embed as a Tauri child webview within the future Browser feature's UI region — not a second top-level OS window.
5. Add `apps/desktop/src/features/browser/api.ts` wrapping the new commands, following the existing feature-owned-`api.ts` convention.
6. **Explicit B1 non-goals**: no `kortex.browser.*` capability registration (B5), no policy enforcement (B4), no persistent profiles (B3) — an ephemeral/in-memory WebView2 profile is acceptable for B1's own validation only.
7. Verification: a manual smoke test (open a real URL, navigate, close), a Rust test confirming the new capability file grants exactly the new commands and nothing else, and a CI job addition to `desktop-ci.yml`'s existing `rust`/`frontend` jobs (or a new job) producing a citable run ID.
8. Any new Cargo/NuGet dependency this requires (e.g. a WebView2 bindings crate) is a B1-time decision requiring its own authorization — explicitly not pre-approved by this B0 report.

## 21. Files Changed (this phase)

New files only — nothing pre-existing was modified except the ADR index (§15):
- `docs/adr/ADR-0019-kortex-browser-runtime-webview2-chromium.md` (new)
- `docs/adr/README.md` (index row added for ADR-0019)
- `docs/architecture/browser_vision.md` (new)
- `docs/architecture/browser_architecture.md` (new)
- `docs/architecture/browser_security_model.md` (new)
- `docs/architecture/browser_capability_model.md` (new)
- `docs/architecture/browser_auth_architecture.md` (new)
- `docs/architecture/browser_roadmap_b0_b10.md` (new)
- `docs/architecture/browser_decision_log.md` (new)
- `docs/architecture/browser_known_limitations.md` (new)
- `docs/architecture/browser_b0_architecture_audit_report.md` (this file, new)

## 22. Working-Tree Status at Phase Close

`.kortex/roadmap.md`, `CHANGELOG.md`, and `docs/release/RELEASE_CANDIDATE_READINESS.md` retain their pre-existing, unrelated uncommitted changes from a separate reconciliation pass — untouched by this phase. The untracked `scratch/` directory is likewise untouched. No file was staged, committed, or pushed by this phase.

## 23. Out-of-Scope Items (explicitly not done in B0)

No WebView2 implementation, no NuGet/Cargo dependency additions, no Tauri modifications, no frontend/backend code changes, no provider web adapters, no authentication implementation, no browser action implementation, no CI changes, no release tags, no commits, no pushes — per the task brief's strict scope control.

## 24. Browser-B0 Readiness Verdict

**COMPLETE.** All 17 B0 deliverable items from the task brief are addressed above and in the companion living documents. No source code was implemented. No commit or push was performed. Browser-B1 (WebView2 Browser Runtime) may begin from this specification once the project owner authorizes it, starting with the Tauri multi-webview spike (§20 step 1).
