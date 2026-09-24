# KORTEX Browser — Known Limitations

**Status**: Living document — updated every phase. Established Browser-B0.

## As of Browser-B1 (WebView2 Browser Runtime Foundation — implemented)

- **No automated test exercises a live `Window`/`Webview`, real or mocked.** `tauri::test::MockRuntime` (needed for this) crashes every test binary at startup in this environment when combined with the `unstable` feature this crate requires (`STATUS_ENTRYPOINT_NOT_FOUND`); root cause not isolated. See `browser_decision_log.md` D12/OD-B5. `browser_runtime.rs`'s tests cover pure logic only (path sanitization, id/error serialization).
- **No interactive/visual confirmation that a WebView2 surface renders real content.** The real production binary was launched and observed to start cleanly, but no tool in this session can drive the resulting native OS window's UI — a human running the app and clicking through Open/Navigate/Reload/Close is the remaining verification step. See `docs/architecture/browser_b1_implementation_report.md` §Tests.
- **Fixed placement/size** for the single Browser-B1 proof-of-concept surface — no dynamic docking to a DOM element's layout yet (Browser-B2).
- **No back/forward navigation** — wry's stable `Webview` API has no session-history methods; deferred (D11).
- **No persistent profiles** — `resolve_profile_directory`/`default_profile_root` are a minimal stand-in for `BrowserProfileStore` (Browser-B3).
- **No Browser Policy enforcement point yet** — today's security posture rests entirely on Tauri's own static, build-time ACL (verified sound for the "arbitrary website gets zero KORTEX capabilities" baseline — see `browser_security_model.md` §4), not the dynamic, origin/profile-aware policy layer Browser-B4 will add.
- **`kortex.browser.view` is declared but not yet enforced**, matching every other `DEFAULT_APPLICATIONS` entry's current state (a pre-existing, repository-wide condition, not specific to Browser).

## As of Browser-B0 (documentation-only phase)

- **No code exists yet.** Every claim about "the cleanest integration seam" in `browser_architecture.md` is a recommendation based on read-only investigation, not a verified implementation — B1 may surface constraints this audit didn't anticipate.
- **Windows-only V1.** WebView2 is a Windows Evergreen/Chromium runtime; a future macOS/Linux desktop build (already a deferred item in `docs/release/RELEASE_CANDIDATE_READINESS.md`) will need a second `IKortexBrowserRuntime` adapter before Browser is available cross-platform. Not a defect — a scoped, known V1 gap (see ADR-0019 §5).
- ~~**Tauri multi-webview support is unverified.**~~ **Resolved by the Browser-B1 preflight (2026-09-25).** `Window::add_child()` exists in the pinned `tauri = "=2.11.5"` (`window/mod.rs:1129`) and supports exactly the embedded-child-webview architecture `browser_architecture.md` §2.3 describes. The remaining, more precise limitation: `add_child` is gated behind the `unstable` Cargo feature, which this repo's `Cargo.toml` does not currently enable — a required, low-risk, one-line prerequisite for B1 (see `browser_decision_log.md` D7, `docs/architecture/browser_b1_preflight_report.md`). Tauri v2's capability-file ACL schema scoping a grant to a specific *child-webview label* (vs. only a window label) remains unverified (`browser_decision_log.md` OD-B4).
- **No sandboxing model designed yet for the browser runtime process itself.** The existing Python execution sandbox (AppContainer + Job Object, `python_exec/windows_boundary.py`) is a separate system solving a different problem (arbitrary Python code execution) and does not transfer directly to a WebView2 host process; B1/B4 must determine what process-level isolation, if any, KORTEX layers on top of what WebView2 itself already provides.
- **No profile storage construct exists yet.** `browser_security_model.md` §10 identifies that KORTEX's current persistence model (SQL rows + AAD encryption) cannot represent an opaque WebView2 user-data folder; a new `BrowserProfileStore` must be designed in B3, not assumed to already fit an existing facade.
- **No prompt-injection sanitizer exists anywhere in the codebase.** Flagged in `browser_security_model.md` §11 as the one component this project cannot get "for free" from existing machinery; it must be designed and adversarially tested (B6, B9) before any AI-observable browsing ships.
- **`CapabilityPalette`'s capability allowlist is currently hardcoded.** `CURATED_CAPABILITY_NAMES` (`CapabilityPalette.tsx:13-17`) will need explicit `kortex.browser.*` entries added in B5/B8 — trivial, but not automatic.
- **The existing `devBrowserBridge` is not usable prior art for the untrusted-content isolation problem.** It solves a different, dev-tooling-only problem (see `browser_security_model.md` §4); B4 must design the native-bridge boundary from first principles.
- **Gate-numbering / phase-numbering ambiguity in the wider repository is a pre-existing condition, not something this project resolves.** `.kortex/roadmap.md`'s own reconciliation pass (uncommitted as of this audit) already flags unresolved "Gate 3/5/6" numbering and a "Phase A" section unrelated to this project's "Browser-B" numbering — see `browser_decision_log.md` D2.

## Anticipated (not yet confirmed) limitations for later phases

- B7's "controlled authentication window" concept has no existing UI precedent in this codebase to build from (today's OAuth flow opens the OS's external browser, not an embedded controlled surface) — genuinely new UX and security design required, not an adaptation of existing code.
- B9's adversarial testing scope (prompt injection, hostile websites, navigation escapes) has no existing red-team harness in this repository to reuse; tooling for it is not yet identified.
