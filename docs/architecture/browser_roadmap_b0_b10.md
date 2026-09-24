# KORTEX Browser — Roadmap (B0–B10)

**Status**: Living document — updated as each phase completes. Established Browser-B0.

**Numbering note**: this roadmap uses a "B" prefix (Browser-B0 through Browser-B10) to keep Browser phases unambiguous from this repository's existing, already-non-sequential phase/milestone numbering (`Phase 1`–`Phase 7`, the separately-tracked `M7.x` Application Completion track, and an unrelated post-RC "Phase A Authentication Completion" section — see `.kortex/roadmap.md`). **Always write "Browser-B0" (or "KORTEX Browser Phase B0") in full outside this document** — a bare "B0" or "Phase A" risks colliding with unrelated existing labels elsewhere in this repository's documentation. See `browser_decision_log.md` D2 for the full rationale.

Each phase entry below follows a fixed structure:

```
PHASE:
STATUS:
OBJECTIVE:
ARCHITECTURE:
IMPLEMENTATION:
SECURITY:
TESTS:
EVIDENCE:
KNOWN LIMITATIONS:
DECISIONS:
NEXT PHASE:
```

---

## Browser-B0 — Architecture Audit + Documentation Foundation

PHASE: B0
STATUS: **COMPLETE** (this pass)
OBJECTIVE: Read-only audit of the existing KORTEX OS repository to find the cleanest integration seam for a future built-in browser, and establish a living documentation structure before any implementation begins.
ARCHITECTURE: See `browser_architecture.md`, `browser_security_model.md`, `browser_capability_model.md`, `browser_auth_architecture.md`, and ADR-0019 for the full output of this phase.
IMPLEMENTATION: None. No code, no dependencies, no CI changes. Explicitly out of scope for B0 per the task brief.
SECURITY: No new attack surface introduced (documentation only). Security *model* established (see `browser_security_model.md`), not yet enforced.
TESTS: None applicable — no code exists yet.
EVIDENCE: See `docs/architecture/browser_b0_architecture_audit_report.md` for the full audit report, findings, and file:line citations across six investigation tracks (Tauri/frontend/IPC, .NET Desktop Agent, AI Engine/Provider Registry, capability/action architecture, security/audit/persistence, documentation/roadmap/CI conventions).
KNOWN LIMITATIONS: See `browser_known_limitations.md`.
DECISIONS: See `browser_decision_log.md` and ADR-0019 (WebView2 + Chromium via `IKortexBrowserRuntime`).
NEXT PHASE: Browser-B1 — WebView2 Browser Runtime (implementation of `IKortexBrowserRuntime` + `WebView2RuntimeAdapter`, per the integration seam this phase identified).

---

## Browser-B1 — WebView2 Browser Runtime

PHASE: B1
STATUS: **COMPLETE**
OBJECTIVE: Implement `IKortexBrowserRuntime` and the V1 `WebView2RuntimeAdapter`, embedded in the existing Tauri desktop shell as a new, narrowly-scoped module — no UI, no policy enforcement, no capabilities yet.
ARCHITECTURE: Per `browser_architecture.md` §2 — `BrowserRuntime` trait + `WebView2RuntimeAdapter<R>` in `apps/desktop/src-tauri/src/browser_runtime.rs`, registered in `lib.rs`, embedded child webview via `Window::add_child()` (not a second top-level window). `create_surface` combines "create"+"attach" into one call, since Tauri's own primitive doesn't separate them either; `go_back`/`go_forward` deliberately omitted (D11).
IMPLEMENTATION: Complete — see `docs/architecture/browser_b1_implementation_report.md` for the full file-by-file account. Rust: `browser_runtime.rs` (new), `lib.rs` (wired), `Cargo.toml` (`unstable` feature enabled, no new dependency), `capabilities/browser.json` + `permissions/browser-runtime.toml` (new). Frontend: `features/browser/{api.ts,components/BrowserApp.tsx}` (new), `workspace/{icons.tsx,defaultApps.ts}` (added Browser entry), `shell/navigation/navConfig.ts` (removed the now-superseded placeholder).
SECURITY: `capabilities/browser.json` scopes via `"webviews": ["main"]`, not `"windows": ["main"]` — a load-bearing finding (D10): a `"windows"` match would have granted these permissions to every child webview embedded in "main," including untrusted browser surfaces. Independently, Tauri's own Local/Remote origin ACL check denies any capability with no `remote` field to `WebviewUrl::External` content regardless of label matching. `profile_id` is sanitized before ever becoming a filesystem path (`resolve_profile_directory`, unit-tested). No existing capability (`default.json`) was touched or widened.
TESTS: Post adversarial-review fix — Rust `cargo test --lib`: **66 passed** (56 pre-existing unchanged + 10 new pure-logic, including 2 added post-review to verify the id-collision fix), 0 failed, 2 ignored (pre-existing). `cargo clippy --lib`: 0 new warnings. Frontend `pnpm typecheck`: clean. `pnpm test` (full suite): 809 passed across 99 files (0 failed), confirmed independently three times across implementation/review/post-fix, including new `BrowserApp.test.tsx` and updated `defaultApps.test.ts`/`AppSidebar.test.tsx` coverage. **Honest gap**: no automated test exercises a live `Window`/`Webview` (real or mocked) — `tauri::test`'s `MockRuntime` crashes the test binary in this environment when combined with `unstable` (D12/OD-B5); `cargo build` + a real binary launch substitute as partial evidence, but full interactive lifecycle verification requires a human running the app.
EVIDENCE: `docs/architecture/browser_b1_implementation_report.md` §Evidence (full command output summary).
KNOWN LIMITATIONS: See `browser_known_limitations.md`'s "As of Browser-B1" section — no automated live-webview test coverage, no visual/interactive confirmation, fixed surface placement, no back/forward, no persistent profiles, no Browser Policy enforcement point yet.
DECISIONS: D10 (capability scoping + Local/Remote ACL, load-bearing), D11 (no back/forward), D12 (MockRuntime environment limitation), OD-B5 (recommend root-causing the test crash before B2/B3) — see `browser_decision_log.md`.
NEXT PHASE: Browser-B2 — Browser UI. Recommended prerequisite: resolve OD-B5 first.

---

## Browser-B2 — Browser UI

PHASE: B2
STATUS: NOT STARTED
OBJECTIVE: Tabs, address/navigation bar, loading state, back/forward/reload, basic browser controls — a human-usable browser surface with no AI involvement and no persistent profile yet.
ARCHITECTURE: New frontend feature module `apps/desktop/src/features/browser/{components,hooks,api.ts}`, wired to the existing but unwired `{ id: "browser" }` nav entry at `apps/desktop/src/shell/navigation/navConfig.ts:40`.
IMPLEMENTATION: To be filled when B2 starts.
SECURITY: No new capability surface — UI only, driving B1's runtime directly through its own `api.ts`, following the same feature-module isolation rule every other feature already follows.
TESTS: To be filled when B2 starts.
EVIDENCE: To be filled when B2 completes.
KNOWN LIMITATIONS: No persistent sessions yet (B3).
DECISIONS: To be filled as B2 makes them.
NEXT PHASE: Browser-B3 — Profiles + Persistent Sessions.

---

## Browser-B3 — Profiles + Persistent Sessions

PHASE: B3
STATUS: NOT STARTED
OBJECTIVE: KORTEX browser profile, persistent user data, per-tenant isolation, profile lifecycle.
ARCHITECTURE: New `BrowserProfileStore` construct — one exclusive on-disk WebView2 user-data folder per tenant/profile, sitting alongside (not inside) KORTEX's existing four storage facades. See `browser_security_model.md` §10.
IMPLEMENTATION: To be filled when B3 starts.
SECURITY: Profile directories must be tenant-exclusive at the OS filesystem level, since WebView2 profile data cannot be decomposed into KORTEX's existing row-level tenant isolation.
TESTS: To be filled when B3 starts — at minimum: cross-tenant profile-directory isolation, matching the spirit of `test_capability_projection_security.py`'s existing cross-tenant tests.
EVIDENCE: To be filled when B3 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B3 makes them.
NEXT PHASE: Browser-B4 — Browser Security + Browser Policy.

---

## Browser-B4 — Browser Security + Browser Policy

PHASE: B4
STATUS: NOT STARTED
OBJECTIVE: Domain policy, navigation policy, permission policy, native bridge policy, downloads/uploads, audit, sensitive actions — Browser Policy becomes real, as the independent security boundary described in `browser_security_model.md`.
ARCHITECTURE: Browser Policy as a fifth enforcement point analogous to `SecurityEngine`/`CapabilityProjection`, routed through `CapabilityDispatcher.dispatch()`. See `browser_security_model.md` §2.
IMPLEMENTATION: To be filled when B4 starts.
SECURITY: This phase *is* the security boundary — full adversarial review required before sign-off (not just unit tests).
TESTS: To be filled when B4 starts.
EVIDENCE: To be filled when B4 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B4 makes them.
NEXT PHASE: Browser-B5 — Browser Capability Layer.

---

## Browser-B5 — Browser Capability Layer

PHASE: B5
STATUS: NOT STARTED
OBJECTIVE: `browser.navigate/.read/.click/.type/.extract/.screenshot/.download` registered as governed `kortex.browser.*` capabilities, per `browser_capability_model.md`.
ARCHITECTURE: Reuse `CapabilityDescriptor`/`generate_tool_definition_from_capability()`/`ToolRegistry` registration pattern exactly as Connector/Document/Knowledge do. See `browser_capability_model.md` §2.
IMPLEMENTATION: To be filled when B5 starts.
SECURITY: `is_read_only` classification per `browser_capability_model.md` §3 (conservative — most actions default to mutating). `CapabilityPalette.tsx`'s `CURATED_CAPABILITY_NAMES` allowlist gains `kortex.browser.*` entries.
TESTS: To be filled when B5 starts.
EVIDENCE: To be filled when B5 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B5 makes them.
NEXT PHASE: Browser-B6 — AI Browser Assistant.

---

## Browser-B6 — AI Browser Assistant

PHASE: B6
STATUS: NOT STARTED
OBJECTIVE: KORTEX AI Engine observation of and governed action on the browser; confirmation model; prompt-injection defenses.
ARCHITECTURE: AI Browser Agent issues tool calls through the existing `DurableAIApprovalPolicy`/`AIOrchestrationEngine` chain — no new orchestration engine. See `browser_architecture.md` §3.
IMPLEMENTATION: To be filled when B6 starts.
SECURITY: The prompt-injection-aware sanitizer for extracted page content (`browser_security_model.md` §11) — the one genuinely new component this whole project requires beyond reusable existing machinery — is built here.
TESTS: To be filled when B6 starts.
EVIDENCE: To be filled when B6 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B6 makes them.
NEXT PHASE: Browser-B7 — AI Studio Provider Web Sign-In.

---

## Browser-B7 — AI Studio Provider Web Sign-In

PHASE: B7
STATUS: NOT STARTED
OBJECTIVE: Controlled authentication window; provider account connection; account/session state; provider connection status; preserve API-key authentication unmodified.
ARCHITECTURE: Per `browser_auth_architecture.md` §3 — new `auth_method` field on `AIProviderConfig`, Web Account session stored via existing `SecretStore` under a new handle namespace, controlled window hosted as a Browser capability, reusing only the callback/state-verification half of the existing OAuth deep-link precedent.
IMPLEMENTATION: To be filled when B7 starts.
SECURITY: Must satisfy every constraint in `browser_auth_architecture.md` §4 (no password collection, no cookie replay, no CAPTCHA/Turnstile bypass). Provider-specific web adapters require separate review before implementation.
TESTS: To be filled when B7 starts.
EVIDENCE: To be filled when B7 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B7 makes them.
NEXT PHASE: Browser-B8 — Copilot + Workflow Integration.

---

## Browser-B8 — Copilot + Workflow Integration

PHASE: B8
STATUS: NOT STARTED
OBJECTIVE: Browser actions available in Copilot and in workflows/automations, via the existing Action/capability integration.
ARCHITECTURE: `CapabilityPalette` and workflow builder integration, per `browser_capability_model.md` §6.
IMPLEMENTATION: To be filled when B8 starts.
SECURITY: No new security mechanism expected — reuses B4/B5's policy and governance.
TESTS: To be filled when B8 starts.
EVIDENCE: To be filled when B8 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B8 makes them.
NEXT PHASE: Browser-B9 — Browser Security / Adversarial Testing.

---

## Browser-B9 — Browser Security / Adversarial Testing

PHASE: B9
STATUS: NOT STARTED
OBJECTIVE: Prompt injection, hostile websites, navigation escapes, native bridge attacks, profile isolation, credential isolation, download/upload abuse, AI action abuse — adversarial validation of every boundary established in B4/B6/B7.
ARCHITECTURE: N/A (testing phase).
IMPLEMENTATION: N/A (testing phase, though remediation of findings belongs to this phase's boundary per the dependency-chain rule in `CLAUDE.md`).
SECURITY: This phase's entire purpose is security validation.
TESTS: To be filled when B9 starts.
EVIDENCE: To be filled when B9 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: To be filled as B9 makes them.
NEXT PHASE: Browser-B10 — Browser Technical RC.

---

## Browser-B10 — Browser Technical RC

PHASE: B10
STATUS: NOT STARTED
OBJECTIVE: Full validation, documentation, evidence, release readiness for the Browser feature — analogous to how `docs/release/RELEASE_CANDIDATE_READINESS.md` gates the rest of KORTEX.
ARCHITECTURE: N/A (readiness/gate phase).
IMPLEMENTATION: N/A.
SECURITY: Final sign-off against every boundary in `browser_security_model.md`.
TESTS: Full regression across B1–B9.
EVIDENCE: To be filled when B10 completes.
KNOWN LIMITATIONS: To be filled.
DECISIONS: Whether Browser ships as part of a future KORTEX release, or remains its own independently-versioned track, is an owner decision deferred to this phase (`browser_decision_log.md` OD-B2).
NEXT PHASE: None — B10 is the terminal phase of this roadmap as currently scoped.

---

## Phase verification requirements (cross-phase)

Per this repository's milestone workflow (`CLAUDE.md`): no Browser phase is complete merely because its own tests pass. Each phase must go through Specification → Dependency Investigation → Implementation → Targeted Tests → Adversarial Review → Dependency-Chain Review → Remediation → Full Regression → Final Architectural Gate → Owner GO/NO-GO before being marked STATUS: COMPLETE above.
