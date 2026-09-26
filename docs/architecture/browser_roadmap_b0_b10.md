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
STATUS: **COMPLETE**
OBJECTIVE: Tabs, address/navigation bar, loading state, back/forward/reload, basic browser controls — a human-usable browser surface with no AI involvement and no persistent profile yet.
ARCHITECTURE: `BrowserRuntime` extended with `go_back`/`go_forward`/`set_bounds` (D15, D16); `apps/desktop/src/features/browser/{api.ts, hooks/useBrowserTabs.ts, components/{BrowserApp,BrowserToolbar,BrowserTabBar}.tsx}`. Tab switching parks inactive surfaces off-screen via `set_bounds` rather than destroying them (D16) — every visible tab is a real `BrowserSurfaceId`. Wired through `DEFAULT_APPLICATIONS` (unchanged from Browser-B1; the `id: "browser"` entry was already live).
IMPLEMENTATION: Complete — see `docs/architecture/browser_b2_implementation_report.md` for the full file-by-file account. Rust: `browser_runtime.rs` extended (`SurfaceBounds`, `SurfaceEntry`, `go_back`/`go_forward`/`set_bounds`, real event-driven `loading` via `NavigationStarting`/`NavigationCompleted`), 3 new commands, `Cargo.toml` (`webview2-com`/`windows` promoted to direct dependencies — no new crate/version). Frontend: toolbar, tab bar, tab-management hook, resize tracking via `ResizeObserver`.
SECURITY: Unchanged security boundary from Browser-B1 — `capabilities/browser.json` still scopes via `"webviews": ["main"]` (never `"windows"`), still declares no `remote` field. Three new commands added to the same capability, with matching new `permissions/browser-runtime.toml` entries; no existing permission was widened. No new native/filesystem/shell access introduced.
TESTS: Rust `cargo test --lib`: 68 passed (66 pre-existing + 2 new), 0 failed, 2 ignored. `cargo clippy --lib`: 0 new warnings. Frontend `pnpm typecheck`: clean. `pnpm test` (full suite): 830 passed across 101 files (0 failed) — 21 new tests across `BrowserToolbar.test.tsx` (incl. `normalizeAddress`), `BrowserTabBar.test.tsx`, and a full rewrite of `BrowserApp.test.tsx` for the tab-based UI. **Honest gap, larger than Browser-B1's**: the new raw WebView2 COM code (`GoBack`/`GoForward`/`CanGoBack`/`CanGoForward`/navigation events) has no automated test at all (same OD-B5 environment limitation), and the `#[cfg(not(windows))]` fallback path could not be confirmed by an actual Linux compiler run from this Windows machine (OD-B6) — only by manual review of deliberately trivial code.
EVIDENCE: `docs/architecture/browser_b2_implementation_report.md` §Evidence.
KNOWN LIMITATIONS: See `browser_known_limitations.md`'s "As of Browser-B2" section — no automated test for the new COM code, no Linux-target compile confirmation (OD-B6), no visual/interactive confirmation of real back/forward/loading/resize behavior, tabs share one non-persisted profile, no persistent profiles/sessions (B3), no capability layer (B5).
DECISIONS: D15 (raw COM for back/forward/loading, cfg-gated), D16 (tab switching via `set_bounds`, no new "active" concept) — see `browser_decision_log.md`. OD-B6 opened (Linux-target compile confirmation pending real CI).
NEXT PHASE: Browser-B3 — Profiles + Persistent Sessions. Recommended prerequisites: resolve OD-B5 and OD-B6 first; a human should manually verify real back/forward/loading/resize/tab-switching behavior in the actual running app before B3 builds further on this.

---

## Browser-B3 — Profiles + Persistent Sessions

PHASE: B3
STATUS: **COMPLETE**
OBJECTIVE: KORTEX browser profile, persistent user data, per-tenant isolation, profile lifecycle.
ARCHITECTURE: `BrowserProfileStore` (`apps/desktop/src-tauri/src/browser_profile_store.rs`) — one exclusive on-disk WebView2 user-data folder per tenant/profile, sitting alongside (not inside) KORTEX's existing four storage facades, exactly as `browser_security_model.md` §10 called for. `browser_runtime.rs`'s `BrowserRuntime`/`WebView2RuntimeAdapter` remain profile-agnostic; the Tauri command layer orchestrates between the two. See `browser_architecture.md` §2.7 and `browser_decision_log.md` D18–D26.
IMPLEMENTATION: Complete. Rust: new `browser_profile_store.rs` module (identity, storage, path containment, locking, ACL, legacy quarantine, audit logging, 4 new Tauri commands); `browser_runtime.rs` modified (`CreateSurfaceRequest` now takes an already-resolved `data_directory`, profile-agnostic; `browser_create_surface`/`browser_destroy` orchestrate profile open/close; WebView2 password/autofill hardening added); `ipc.rs` modified (OD-B7 tenant-identity bridge in `IpcClientState`); `lib.rs` wired (new app state, commands, shutdown-path lock release); `capabilities/browser.json` + `permissions/browser-runtime.toml` (4 new permission entries, same narrow scoping). Frontend: `features/browser/{api.ts, hooks/useBrowserProfiles.ts, hooks/useBrowserTabs.ts (profile-aware), components/ProfileSwitcher.tsx}`.
SECURITY: Profile directories are tenant-exclusive via hardened, containment-checked path resolution (ported from `PathSandboxValidator`'s algorithm) plus a baseline OS-user `icacls` ACL restriction (D26). Tenant identity for every profile operation is resolved server-side (`IpcClientState::current_tenant_id()`, OD-B7) — no command anywhere accepts a tenant id as a parameter. `capabilities/browser.json` still scopes via `"webviews": ["main"]` (never `"windows"`), still declares no `remote` field — 4 new commands added to the same capability, no existing grant widened.
TESTS: Rust `cargo test --lib`: **107 passed** (was 68 after B2; +39 net new across B3, after removing 6 tests for code Browser-B3 superseded), 0 failed, 2 ignored (pre-existing). `cargo clippy --lib --tests`: 0 new warnings (1 pre-existing, confirmed via A/B comparison against the pre-B3 baseline, unrelated to Browser). `cargo fmt --check`: clean for every B3-touched file. Frontend `pnpm typecheck`: clean. `pnpm test` (full suite): **839 passed** across 101 files (0 failed) — 5 new profile-switcher tests plus fixes to existing `BrowserApp.test.tsx` mocks. Live (non-unit) verification: two temporary, disposable preflight harnesses (both removed after verification, never shipped) confirmed (1) distinct WebView2 profile directories coexist simultaneously in one process and destroy-then-recreate against the same directory works (OD-B9), and (2) WebView2 password-autosave/general-autofill settings actually read back `false` after being set on a real instance (D22), plus a live `icacls` read-back confirming a created profile directory's ACL is actually restricted, not just "the call returned Ok."
EVIDENCE: See `docs/architecture/browser_b3_implementation_report.md`.
KNOWN LIMITATIONS: See `browser_known_limitations.md`'s "As of Browser-B3" section — local-only audit logging (interim, D23), baseline (not AppContainer) ACL, no disk quotas, no encryption-at-rest, OD-B5's live-WebView2-lifecycle test gap continues (most new B3 logic is unaffected, being pure Rust).
DECISIONS: D18–D27 — see `browser_decision_log.md` for the full account. D27 itself was a documentation-only error, later corrected during the Browser-B4 architecture gate: the `BrowserRuntimeError` field-casing fix it discusses was in fact already present in the B3 commit itself, not "flagged, not fixed" as this entry's own decision log originally (incorrectly) stated.
NEXT PHASE: Browser-B4 — Browser Security + Browser Policy.

---

## Browser-B4 — Browser Security + Browser Policy

PHASE: B4
STATUS: **COMPLETE** (V1 — navigation/popup/download/permission policy only; domain allowlisting, native-bridge policy, and sensitive-action confirmation UI remain future work)
OBJECTIVE: Domain/navigation policy, popup policy, download policy, native permission policy, audit — Browser Policy becomes real for human/page-triggered actions, as a local security boundary. (Full adversarial-review/AI-action scope, and the "fifth enforcement point through `CapabilityDispatcher`" framing, apply to B5/B6's governed capabilities, not to this phase — see below.)
ARCHITECTURE: **Corrected from this section's original framing** — the architecture gate found that `browser_security_model.md` §2's "fifth enforcement point routed through `CapabilityDispatcher.dispatch()`" describes B5/B6's FUTURE governed-capability state, not B4's actual scope. A human click or page-triggered navigation/popup/download/permission-request never produces a capability call, and `ICoreWebView2NavigationStartingEventArgs` has no `GetDeferral` (confirmed against the pinned bindings) — so the decision must be local, synchronous, in-process, never a backend round-trip. `BrowserPolicyEngine` (`browser_policy.rs`) is that local decision engine; `browser_runtime.rs`'s WebView2 COM event handlers (`NavigationStarting`/`NewWindowRequested`/`DownloadStarting`/`PermissionRequested`) are the enforcement points. See `browser_architecture.md` §2.8, `browser_security_model.md` §2/§3/§5/§12, and `browser_decision_log.md` D28.
IMPLEMENTATION: Complete. Rust: new `browser_policy.rs` module (`NavigationRequest`, `NetworkClassification`, `DenyReason`, `PolicyDecision`, `evaluate_navigation`, `PolicyDeniedEvent`, `PolicyAction`, `PolicyAuditEvent`, `record_policy_audit_event`); `browser_runtime.rs` modified (navigation policy wired into `NavigationStarting`; `NewWindowRequestedEventHandler`/`DownloadStartingEventHandler`/`PermissionRequestedEventHandler` added, all unconditional-deny in V1; shared `record_and_emit_policy_denial` helper); `lib.rs` wired (`mod browser_policy;`, policy audit log path threaded into the adapter). Frontend: `features/browser/api.ts` (`PolicyDeniedEvent`/`PolicyDenyReason`/`onBrowserPolicyDenied`/`policyDenyReasonMessage`), `hooks/useBrowserTabs.ts` (policy-denied error banner subscription).
SECURITY: Deny-by-default baseline for all four action kinds, owner-approved (D29). Local/private-network navigation denied (loopback/RFC1918/link-local/"this network"/CGN/IPv6 unique-local+link-local/`localhost`), including bypass classes closed by adversarial testing during this phase (trailing-dot `localhost.` FQDN notation, decimal/hex/octal/short-form numeric IPv4 encodings, userinfo host confusion — D33). Popups, downloads, and native permission requests are all unconditionally denied — no popup-to-tab conversion, no save-path confirmation, no permission-confirmation UI yet (OD-B13/B14/B15). Policy-denied events/audit entries never carry the actual URI, host, path, query, or fragment. Known, disclosed gap: no DNS resolution, so DNS-rebinding-style bypasses are not caught (OD-B16).
TESTS: Rust `cargo test --lib`: **142 passed** (was 107 after B3; +35 net new, all in `browser_policy.rs`), 0 failed, 2 ignored (pre-existing). `cargo clippy --lib --tests`: 0 new warnings (1 pre-existing, unrelated). `cargo fmt --check`: clean for every B4-touched file. Frontend `pnpm typecheck`: clean. `pnpm test` (full suite): **839 passed** across 101 files (0 failed) — required fixing a pre-existing gap this phase's own `onBrowserPolicyDenied` subscription exposed (`BrowserApp.test.tsx` had no mock for it, causing an unhandled-rejection failure once the effect actually ran in tests). Live (non-unit) verification: a temporary, disposable preflight (removed after use, never shipped) confirmed, against the real pinned WebView2 runtime, that all four denial mechanisms actually block the action, not merely audit it — including an external, on-disk check that a triggered download genuinely never reached the real Downloads folder (D32).
EVIDENCE: See `docs/architecture/browser_b4_implementation_report.md`.
KNOWN LIMITATIONS: See `browser_known_limitations.md`'s "As of Browser-B4" section — no DNS-rebinding protection (OD-B16), popups/downloads/permissions all unconditionally denied with no confirmation UI (OD-B13/B14/B15), the live preflight's permission-denial check did not independently re-observe the page-side promise rejection, `DownloadStarting` registration best-effort-degrades on a pre-`ICoreWebView2_4` WebView2 runtime, OD-B5's automated-live-lifecycle-test gap continues (worked around via a live preflight against the real binary, not resolved).
DECISIONS: D28–D33 — see `browser_decision_log.md`.
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
