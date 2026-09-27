# Browser-B5 Implementation Report — Browser Capability Layer

**Status**: COMPLETE for this cycle's scope (navigate + screenshot real execution, full capability-layer governance). Not committed as of this report — commit/push authorization is separate and explicit, per this repository's milestone workflow.

The internal stage identifiers B5.0–B5.11 are retired per explicit owner direction ("KORTEX OS — B5 MASTER IMPLEMENTATION"); this report and `browser_b5_master_plan.md` are the only two B5-scoped documents, covering the entire B5 milestone to date (the B5.0-B5.4 foundation, previously reported separately, plus this cycle's navigate/screenshot execution).

## 1. Scope

**Delivered this cycle**: real execution of `kortex.browser.navigate` and `kortex.browser.screenshot`, end to end, through the unmodified B4 `BrowserPolicyEngine` and the unmodified B5.0-B5.4 Grant/redeem foundation — plus the parameter-hash verification (OD-01) and navigation-event correlation (OD-02) mechanisms both capabilities' execution depends on.

**Explicitly not delivered, and why** (see `browser_b5_master_plan.md` §5/§6 for full reasoning): `.read`/`.extract`/`.click`/`.type` execution (no approved, or even architecturally specified, access mechanism exists — building one under time pressure was judged higher-risk than reporting the gap honestly); `.download` execution (B4's own download policy is a full subsystem not yet built, unrelated to this cycle); B6-B11.

## 2. Baseline

- B4: `10f1b4ccf2488b64aadbc2efc632dcd82bf9698a`
- B5 foundation (no execution): `fbddfab5d8fa698eb986ba501604475eae38241f` (`origin/main` at the start of this cycle)
- `v1.0.0-rc.2`: `041084539f4c68a05db4ffa49903bf8c726c4300` — confirmed unchanged, untouched by this cycle.

## 3. Architecture

See `browser_b5_master_plan.md` §2 for the full canonical execution path and §3/§4 for the OD-01/OD-02 resolutions. Summary: no second dispatcher, authorization engine, approval engine, or transport was created; `BrowserPolicyEngine` (B4) was not modified in any way — `execute_navigate` reaches `BrowserRuntime::navigate` (the identical trait method the human-facing `browser_navigate` command already calls), which triggers the identical `NavigationStarting`/`NavigationCompleted` WebView2 COM callback a human click already goes through.

## 4. Implementation

**Backend** (`backend/src/kortex/engines/browser/grant.py`): one targeted change — `canonicalize_and_hash`'s JSON encoding switched from Python's default spaced separators to `separators=(",", ":")` (compact), so it matches Rust's `serde_json` (compact, key-sorted — this crate does not enable `preserve_order`) byte-for-byte. No capability contract, handler, or registration changed; `engine.py`'s `navigate`/`screenshot` handlers already minted correctly-shaped Grants in B5.0-B5.4.

**Desktop (Rust)**:
- `browser_runtime.rs`: `NavigationOutcome` enum (`Success`/`PolicyDenied`/`Failed`); `SurfaceEntry.navigation_waiter: Arc<Mutex<Option<oneshot::Sender<NavigationOutcome>>>>`; `NavigationStarting`'s `Deny` arm and a rewritten `NavigationCompleted` handler (now reads `IsSuccess`/`WebErrorStatus` — previously discarded) both resolve it via a new `resolve_navigation_waiter` helper; two new `BrowserRuntime` trait methods, `arm_navigation_waiter` and `capture_screenshot` (the latter via `ICoreWebView2::CapturePreview` + `CreateStreamOnHGlobal`/`IStream::Read`, confirmed against the pinned `webview2-com-sys-0.38.2` bindings — `CapturePreview` lives on the base `ICoreWebView2` interface, no versioned-interface cast needed); a new `PolicyAuditLogPath` Tauri-managed state (a second handle to the same audit-log path `WebView2RuntimeAdapter` already holds privately).
- `browser_grant.rs`: a Rust port of `canonicalize_and_hash` (SHA-256 over a `serde_json::json!`-built value); `navigate_parameters_value`/`screenshot_parameters_value` (reconstruct the exact hashed shape from the Grant's own live-verified fields plus caller-supplied wire params — never from a second, caller-supplied target); `BrowserCapabilityParamsWire` (tagged enum, `Navigate { url, timeout_ms }` / `Screenshot { full_page }`); five new `BrowserGrantExecutionError` variants (`ParametersRequired`, `FullPageNotYetSupported`, `PolicyDenied`, `NavigationFailed`, `ScreenshotFailed`, `Timeout`); `BrowserGrantExecutionResult` changed from a flat placeholder struct to a tagged enum (`Success`/`Screenshot`/`NotYetEnabled`); a new `BrowserExecutionAuditEvent` enum + writer, appending to the same local JSONL audit log B4's own policy enforcement already uses; the command itself refactored into a thin `#[tauri::command]` wrapper plus a plain, dependency-injected `execute_granted_action` (and its `execute_navigate`/`execute_screenshot` helpers) — the split that makes everything but the raw COM calls testable without a live Tauri/WebView2 process.
- `browser_profile_store.rs`: `BrowserProfileId::from_raw_for_test` promoted to `pub(crate)` (test-only fixture reuse across sibling modules).
- `Cargo.toml`: `sha2 = "0.10"`, `base64 = "0.22"` promoted from transitive to direct dependencies (both already resolved in `Cargo.lock` via existing dependents — no new crate or version enters the graph); `windows`'s feature list extended with `Win32_System_Com`/`Win32_System_Com_StructuredStorage` (for `IStream`/`CreateStreamOnHGlobal` — `webview2-com`/`webview2-com-sys` already request the former for the same resolved `windows` version).

## 5. Security Model

Unchanged invariants from B5.0-B5.4 (authentication, tenant binding, live surface/profile/generation re-verification, fail-closed) all still apply, unmodified, before either navigate or screenshot code is ever reached. New for this cycle:

- **Parameter integrity**: neither capability's execution touches `BrowserRuntime` until the recomputed parameter hash matches the Grant's own `canonicalized_parameters_hash` — a validly-signed, validly-bound Grant redeemed with substituted parameters is rejected as `GrantInvalid`, proven structurally (the fake runtime's `navigate` is asserted never-called on a mismatch).
- **BrowserPolicyEngine remains the sole, unbypassable navigation authority** — `execute_navigate` never evaluates policy itself; it only observes B4's own decision via the waiter.
- **Screenshot data minimization**: captured bytes are base64-encoded directly into the one IPC response and never written to disk; audit entries for screenshot execution carry only capability/surface/outcome metadata, never image bytes.
- **Desktop-side timeout bound is independent of the wire value's trustworthiness**: `timeout_ms.clamp(100, 300_000)` — found and fixed during this cycle's own adversarial review (the original code clamped only the upper bound).

## 6. Capability Matrix

| Capability | Auth | Live surface/profile/generation binding | Parameter-hash verified | Policy-enforced | Real execution | Audited |
|---|---|---|---|---|---|---|
| `navigate` | ✓ (unchanged) | ✓ (unchanged) | ✓ (new) | ✓ (B4, unmodified) | ✓ (new) | ✓ (new: STARTED/SUCCEEDED/FAILED) |
| `screenshot` | ✓ | ✓ | ✓ (new) | N/A (no navigation) | ✓ (new, viewport-only) | ✓ (new) |
| `read`/`extract`/`click`/`type` | ✓ | ✓ | N/A (no params wired) | N/A | `NotYetEnabled` | Grant lifecycle only |
| `download` | ✓ | ✓ | N/A | N/A (B4 denies unconditionally) | `NotYetEnabled` | Grant lifecycle only |

## 7. Threat Model / Adversarial Review

Formal review against the required attack categories, scoped to what this cycle actually built (navigate/screenshot execution) — categories specific to unbuilt capabilities (prompt injection into read content, click/type secret handling beyond the already-existing mint-time gate, download path traversal) are out of scope for this review, not silently passed.

| Attack | Classification if unmitigated | Status |
|---|---|---|
| Forged / replayed / expired / tampered Grant | CRITICAL | Mitigated (B5.0-B5.4, unchanged), re-proven via `expired_grant_never_reaches_capability_dispatch` |
| **Parameter substitution** (valid Grant, different URL/params redeemed) | CRITICAL | **Mitigated (new).** Proven by `execute_navigate_rejects_parameter_substitution_before_touching_runtime` — `BrowserRuntime::navigate` structurally never called |
| Wrong surface / profile / tenant / generation | CRITICAL/HIGH | Mitigated (unchanged), re-proven via `unregistered_surface_never_reaches_capability_dispatch` |
| Malformed/encoded-bypass URI, redirect to denied target (human path) | HIGH | Mitigated (B4, unchanged, extensively tested) |
| **Redirect to denied target via the NEW AI-redeemed path specifically** | HIGH | Mitigated **by design/code inspection** (the waiter only ever resolves on `Deny`/`Completed`, never on `Allow`, so a multi-hop redirect chain falls through correctly regardless of who issued the original navigation) — **not proven by a live redirect-driving test.** Labeled UNVERIFIED-by-live-test, not silently assumed solved. |
| DNS rebinding | HIGH | **Not mitigated.** Unchanged, disclosed B4 gap. Explicitly not claimed solved. |
| **Waiter cross-talk / stale-event resolution (same-surface, AI-vs-AI)** | HIGH | **Mitigated (new).** `.take()` clears the slot atomically; full-duration per-surface lock. Proven by `concurrent_redemptions_on_same_surface_are_serialized` — two real concurrent redemptions on one surface, asserted strictly ordered, zero interleaving. |
| **Waiter cross-talk (human-vs-AI, same surface, same moment)** | MEDIUM | **Not mitigated — disclosed.** See `browser_b5_master_plan.md` §4. Correctness/attribution issue, not a security-boundary break (no cross-tenant/authorization confusion results). |
| Timeout used to hide a real outcome | MEDIUM | Mitigated: `Timeout` is a distinct, honest, typed outcome (never silently reported as failure or success); documented as "outcome unknown," not "did not happen" |
| Screenshot data leakage (disk persistence, audit leakage) | MEDIUM | Mitigated (new): never written to disk; audit never carries image bytes — verified by inspection of every audit call site in `execute_screenshot` |
| `full_page` silently served as a cropped image | LOW-MEDIUM (correctness/trust, not confidentiality) | Mitigated (new): explicit `FullPageNotYetSupported` refusal, checked before the hash comparison so it is never confused with a tamper rejection |
| Tiny/zero `timeout_ms` causing a spurious near-instant failure | LOW | Mitigated (new, found during this review): `.clamp(100, 300_000)`, not just an upper bound |
| Surface destroyed mid-navigate-and-wait | LOW (availability, not security) | Reasoned to degrade safely to `Timeout` — **not live-tested.** Disclosed as untested. |
| Audit log manipulation/leakage of secrets or full URLs | MEDIUM | Mitigated (new writer follows the identical, already-audited redaction convention B4's own policy audit uses — capability/surface/outcome only, never a URL or parameter value) |

No BLOCKER finding. No unresolved CRITICAL/HIGH finding against what was actually built — the two HIGH items marked "not mitigated"/"UNVERIFIED-by-live-test" are pre-existing (DNS rebinding) or explicitly scoped-out-and-disclosed (redirect-via-AI-path live proof), never silently passed over.

## 8. Targeted Test Results

**Rust** (`cargo test --lib`, full crate): **169 passed, 0 failed, 2 ignored** (the 2 ignored are pre-existing, environment-gated real-Windows-Credential-Manager tests, unrelated to Browser). 27 of the 169 are `browser_grant`'s own tests — 12 pre-existing (unchanged) plus 15 new this cycle: the cross-language parameter-hash fixture (`real_rust_hash_matches_real_python_hash`, four digests captured from the real, currently-shipping Python function, including a substituted-URL fixture proving the hash changes), parameter-substitution rejection, missing/wrong-shaped params rejection, policy-denied/navigation-failed/success/timeout propagation (navigate), success/full-page-refusal (screenshot), expired-grant and unregistered-surface short-circuit proofs, unrecognized-capability-still-NotYetEnabled, and the concurrency-serialization test. `cargo clippy --lib --tests`: 0 new warnings (1 pre-existing, `sidecar.rs`, unrelated, unchanged from B5.0-B5.4). `cargo fmt --check`: clean for every touched file. `git diff --check`: clean.

**Backend** (targeted, per this milestone's own "do not run the full suite merely to obtain green" testing policy): `pytest tests/unit/test_browser_grant.py tests/integration/test_browser_capability_dispatch.py tests/integration/test_capability_metadata_completeness.py` — **97 passed** (unchanged from B5.0-B5.4; the `canonicalize_and_hash` separator change altered no test's assertions, since none of them assert an exact digest value, only relative properties — key-order independence, capability-name sensitivity, distinct parameters producing distinct hashes). `ruff check`/`ruff format --check` on the one modified file (`grant.py`): clean. The full backend regression suite was NOT re-run this cycle, per this milestone's own explicit testing policy (§17/§27) — deferred to the final Browser validation stage, exactly as directed.

## 9. Live Verification

A disposable, env-var-gated preflight (`KORTEX_BROWSER_B5_NAVIGATE_LIVE_PREFLIGHT`), spawned on its own thread, exercising `BrowserRuntime` directly (never through the Grant/backend round-trip, which would require a live backend process and is not what this preflight needs to prove), confirmed against the real, pinned WebView2 runtime:

```
[B5-PREFLIGHT] creating surface...
[B5-PREFLIGHT] surface created: BrowserSurfaceId("browser-surface-18d900b624a093f4-0")
[B5-PREFLIGHT] allowed navigate outcome: Success
[B5-PREFLIGHT] denied navigate outcome: PolicyDenied(PrivateNetworkAccess { classification: Loopback })
[B5-PREFLIGHT] screenshot: 11829 bytes, valid PNG magic header: true
[B5-PREFLIGHT] done, surface destroyed.
```

All three observed outcomes are correct: a real, policy-allowed navigation to a second real destination resolved `Success`; a real, policy-denied (loopback) navigation resolved `PolicyDenied` with the correct classification — proving the single highest-risk new wiring (a bug here would silently hang until timeout rather than failing fast) actually works against real WebView2 COM events, not just a fake runtime; a real `CapturePreview` call produced 11,829 bytes with a valid PNG magic header, confirming the `IStream`/`CreateStreamOnHGlobal` read-back path is correct. All temporary preflight code was removed after this run; confirmed absent via `git diff --check`/inspection of the current `lib.rs`.

**Not live-tested** (see §7): a redirect chain through the AI-redeemed path specifically; surface destruction mid-navigate-and-wait; a real network-level navigation failure (`NavigationFailed`, as opposed to policy-denial) — this last one is covered deterministically by the fake-runtime unit test (`execute_navigate_failure_propagates_web_error_status`) but not against a real unreachable host, which would have required a slow, flaky, or infrastructure-dependent live scenario disproportionate to this cycle's scope.

## 10. Graph/Architecture Evidence

`graphify update .` was run against the full repository after implementation (23,239 nodes, 54,775 edges, 738 communities — up from the pre-B5-execution graph). Verified, not merely run: `graphify explain "arm_navigation_waiter"` correctly resolves to both the production implementation (`WebView2RuntimeAdapter`) and the test double (`FakeBrowserRuntime`), confirming the graph reflects the actual, current implementation rather than a stale or aspirational snapshot.

## 11. Known Limitations

See `browser_b5_master_plan.md` §7 for the full, consolidated list (DNS rebinding; human-vs-AI waiter cross-talk; redirect-via-AI-path and surface-destruction-mid-wait unverified by live test; screenshot viewport-only; `.read`/`.extract`/`.click`/`.type`/`.download` execution not yet built).

## 12. Deferred Risks

- The `.read`/`.extract`/`.click`/`.type` access-mechanism decision (native UIA scoped to the WebView2 HWND, recommended but not approved — `browser_b5_master_plan.md` §5) — the single largest remaining architectural decision in the Browser capability layer.
- A real download-policy subsystem, needed before `.download` can execute at all.
- Human-vs-AI navigation waiter cross-talk (§7) — closing it would mean adding synchronization to the frozen B1/B2 human-facing command path, judged disproportionate to this cycle.
- Live-testing redirect-through-the-AI-path and surface-destruction-mid-wait, ideally alongside whichever future phase next touches this code.

## 13. Git Status

Working tree, at the time of this report: the 25 files already committed in `fbddfab5d8fa698eb986ba501604475eae38241f` (verified via `git show --stat`) remain unchanged in that commit; this cycle's changes are unstaged, on top of it. Touched files: `apps/desktop/src-tauri/Cargo.toml`, `apps/desktop/src-tauri/Cargo.lock`, `apps/desktop/src-tauri/src/browser_runtime.rs`, `apps/desktop/src-tauri/src/browser_grant.rs`, `apps/desktop/src-tauri/src/browser_profile_store.rs`, `backend/src/kortex/engines/browser/grant.py`, `docs/architecture/browser_b5_master_plan.md` (new), `docs/architecture/browser_b5_implementation_report.md` (this file, rewritten), plus the remaining documentation files listed in §14 of the master plan, not yet updated as of this report draft — see the final commit-authorization message for the exact, final file list. `.kortex/roadmap.md`, `CHANGELOG.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md` confirmed untouched; `scratch/` confirmed untracked/unstaged. No force-push, amend, or history rewrite used at any point. `v1.0.0-rc.2` confirmed unchanged.

## 14. CI Status

Not pushed as of this report — no CI run exists yet for this cycle's changes. Per this milestone's own CI policy (§18/§28): CI verification for B5-scoped work is not a blocking requirement; the authoritative, complete Backend + Desktop CI verification is deferred to the Browser Technical RC phase.

## 15. Final Verdict

**READY FOR COMMIT — for the scope actually delivered (navigate + screenshot execution).**

No BLOCKER or unresolved CRITICAL/HIGH finding against the delivered scope. Two genuine adversarial-review findings were made and fixed during this cycle itself (the timeout lower-bound clamp; the parameter-hash separator mismatch, caught before it could ever cause a real failure since no Grant had been redeemed-for-execution before this cycle). One substantial architectural question (`.read`/`.extract`/`.click`/`.type`'s access mechanism) was investigated thoroughly, found genuinely unresolved, and is reported — with a concrete, ready-to-approve recommendation — rather than silently built under time pressure or silently omitted.

Awaiting explicit user review of this report and `browser_b5_master_plan.md`, and explicit commit/push authorization, per this repository's milestone workflow.
