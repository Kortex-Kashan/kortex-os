# Browser-B5 Implementation Report — Browser Capability Layer

**Status**: COMPLETE for the scope the B5 architecture currently permits real execution for — `navigate`/`screenshot` (prior cycle) plus `read`/`extract`/`click`/`type` (this cycle). Not committed as of this report — commit/push authorization is separate and explicit, per this repository's milestone workflow.

The internal stage identifiers B5.0–B5.11 are retired per explicit owner direction ("KORTEX OS — B5 MASTER IMPLEMENTATION"); this report and `browser_b5_master_plan.md` are the only two B5-scoped documents, covering the entire B5 milestone to date (the B5.0-B5.4 foundation and navigate/screenshot execution, previously reported, plus this cycle's `read`/`extract`/`click`/`type` UIA execution).

## 1. Scope

**Delivered this cycle**: real execution of `kortex.browser.read`/`.extract`/`.click`/`.type`, end to end, through a new bounded UIA (Windows UI Automation) worker pool (`browser_uia.rs`) — the design three dedicated architecture gates (D44/D45/D46) produced, implemented here without deviation. Shares the identical Grant-verification foundation (`browser_grant.rs`) navigate/screenshot already established: signature, expiry, single-use, live tenant/profile/surface/generation binding, and an independently recomputed parameter hash, all before any UIA call is ever made.

**Previously delivered** (prior cycle, unchanged, not regressed): real execution of `kortex.browser.navigate` and `kortex.browser.screenshot`, through the unmodified B4 `BrowserPolicyEngine`.

**Explicitly not delivered, and why** (see `browser_b5_master_plan.md` §5/§6 for full reasoning): `.download` execution (B4's own download policy is a full subsystem not yet built, unrelated to this cycle); `node_ref`-only resolution (refused as a specific, typed, honest outcome — never a cached UIA object, which the D45 live spike proved unsafe); B6-B11.

## 2. Baseline

- B4: `10f1b4ccf2488b64aadbc2efc632dcd82bf9698a`
- B5 foundation (no execution): `fbddfab5d8fa698eb986ba501604475eae38241f` (`origin/main` at the start of this cycle)
- `v1.0.0-rc.2`: `041084539f4c68a05db4ffa49903bf8c726c4300` — confirmed unchanged, untouched by this cycle.

## 3. Architecture

See `browser_b5_master_plan.md` §2 for the full canonical execution path, §3/§4 for the OD-01/OD-02 resolutions (navigate/screenshot), and §5.6 for the UIA execution architecture this cycle delivers. Summary: no second dispatcher, authorization engine, approval engine, or transport was created; `BrowserPolicyEngine` (B4) was not modified in any way. Navigate/screenshot reach `BrowserRuntime` directly; read/extract/click/type reach a new, bounded `UiaWorkerPool` instead (WebView2's UIA methods are synchronous, blocking COM calls with no async completion-handler variant, unlike navigate/screenshot's own async WebView2 APIs — a dedicated worker-thread pool, never the WebView2 STA thread itself, is required).

## 4. Implementation

**Backend** (`backend/src/kortex/engines/browser/grant.py`): one targeted change (prior cycle, unchanged this cycle) — `canonicalize_and_hash`'s JSON encoding switched from Python's default spaced separators to `separators=(",", ":")` (compact), so it matches Rust's `serde_json` (compact, key-sorted — this crate does not enable `preserve_order`) byte-for-byte. No capability contract, handler, or registration changed this cycle either.

**Desktop (Rust) — prior cycle (navigate/screenshot), unchanged, not regressed**:
- `browser_runtime.rs`: `NavigationOutcome` enum (`Success`/`PolicyDenied`/`Failed`); `SurfaceEntry.navigation_waiter: Arc<Mutex<Option<oneshot::Sender<NavigationOutcome>>>>`; `NavigationStarting`'s `Deny` arm and a rewritten `NavigationCompleted` handler (now reads `IsSuccess`/`WebErrorStatus` — previously discarded) both resolve it via a new `resolve_navigation_waiter` helper; two new `BrowserRuntime` trait methods, `arm_navigation_waiter` and `capture_screenshot` (the latter via `ICoreWebView2::CapturePreview` + `CreateStreamOnHGlobal`/`IStream::Read`); a new `PolicyAuditLogPath` Tauri-managed state.
- `browser_grant.rs`: a Rust port of `canonicalize_and_hash`; `navigate_parameters_value`/`screenshot_parameters_value`; `BrowserCapabilityParamsWire`'s `Navigate`/`Screenshot` variants; the `execute_granted_action`/`execute_navigate`/`execute_screenshot` split (dependency-injected, testable without a live Tauri/WebView2 process).
- `browser_profile_store.rs`: `BrowserProfileId::from_raw_for_test` promoted to `pub(crate)`.
- `Cargo.toml`: `sha2`/`base64` promoted to direct dependencies; `windows`'s feature list extended with `Win32_System_Com`/`Win32_System_Com_StructuredStorage`.

**Desktop (Rust) — this cycle (`read`/`extract`/`click`/`type` UIA execution)**:
- `browser_uia.rs` (NEW module, ~950 lines): `UiaSelectorSpec` (`role`/`accessible_name` only — no `node_ref` field at all); `UiaBounds` (`read_max_chars: 20_000`, `max_extract_fields: 50`, `max_ancestor_depth: 25`, `max_elements_scanned: 5_000`); `UiaOperationKind`/`UiaOutcome`/`UiaExecutionError` (the full typed contract — `RootUnavailable`/`AccessibilityNotReady`/`SelectorNotFound`/`SelectorAmbiguous`/`SelectorUnsupported`/`ContainmentFailed`/`UnsupportedControlPattern`/`ComFailure`/`PoolExhausted`); `UiaWorkerPool` (bounded `max_workers`/`max_lifetime_creations`, no pending-work queue, mutex-serialized worker creation, `submit`/`retire`, a `Drop` impl that joins every still-healthy worker); `uia_worker_thread_main` (per-worker `CoInitializeEx(MTA)` → `CoCreateInstance(CUIAutomation)` → job loop → explicit `drop(automation)` before `CoUninitialize()`); `resolve_root_with_readiness` (bounded poll, `READINESS_MIN_DESCENDANTS = 16`); `collect_bounded_text`, `role_to_control_type` (a small, explicit, disclosed vocabulary), `resolve_unique_element` (`FindAll`+`Length()`, never `FindFirst`), `verify_containment` (`RawViewWalker`/`GetParentElement` ancestor walk to the Grant-verified surface HWND).
- `browser_runtime.rs`: one new, permanent trait method, `uia_root_hwnd` (`ICoreWebView2Controller::ParentWindow`, the identical idiom `capture_screenshot`/`GoBack` already use).
- `browser_grant.rs`: `READ_CAPABILITY`/`EXTRACT_CAPABILITY`/`CLICK_CAPABILITY`/`TYPE_CAPABILITY` constants; `SelectorWire` (mirrors `BrowserElementSelector`, with `to_uia_selector()` returning `None` — never a substituted selector — for a `node_ref`-only selector); `BrowserCapabilityParamsWire`'s `Read`/`Extract`/`Click`/`Type` variants; `read_parameters_value`/`extract_parameters_value`/`click_parameters_value`/`type_parameters_value` (mirroring `navigate_parameters_value`'s own "target always from the Grant" pattern); `NodeRefOnlyNotSupported`/`UiaFailed` new `BrowserGrantExecutionError` variants; `Read`/`Extract`/`Click`/`Type` new `BrowserGrantExecutionResult` variants; `submit_and_await_uia_operation` (wraps the pool's blocking `submit` in `spawn_blocking`, then a `tokio::time::timeout`; retires the worker on timeout); `execute_read`/`execute_extract`/`execute_click`/`execute_type`; `unexpected_outcome`/`finish_uia_operation` helpers.
- `lib.rs`: `mod browser_uia;`; one new managed state, `Arc<UiaWorkerPool>` (sized `max_workers = 4`, `max_lifetime_creations = 64` — see the module's own doc comment for the justification).
- `Cargo.toml`: `windows`'s feature list extended with `Win32_UI_Accessibility` (confirmed present and sufficient during the architecture-gate live spikes — no VARIANT/PropertyCondition COM complexity needed, so `Win32_System_Ole`/`Win32_System_Variant` were NOT added).

## 5. Security Model

Unchanged invariants from B5.0-B5.4 (authentication, tenant binding, live surface/profile/generation re-verification, fail-closed) all still apply, unmodified, before any capability's execution code is ever reached — including this cycle's four new ones. From the prior cycle (unchanged):

- **Parameter integrity**: no capability's execution touches `BrowserRuntime`/the UIA pool until the recomputed parameter hash matches the Grant's own `canonicalized_parameters_hash` — proven structurally for read/extract/click/type this cycle too (`FakeBrowserRuntime::uia_root_hwnd` panics via `unimplemented!()` if a test with a mismatched hash ever reached it — none do).
- **BrowserPolicyEngine remains the sole, unbypassable navigation authority.**
- **Screenshot data minimization.**
- **Desktop-side timeout bound is independent of the wire value's trustworthiness.**

New this cycle (`read`/`extract`/`click`/`type`):

- **`node_ref`-only resolution is refused, not silently substituted or ignored.** `SelectorWire::to_uia_selector()` returns `None` for a selector supplying only `node_ref`; every one of `execute_extract`/`execute_click`/`execute_type` maps `None` to `NodeRefOnlyNotSupported` BEFORE the parameter hash check even matters for the selector's own shape.
- **No UIA element, COM pointer, or HWND-derived reference is ever retained as durable identity.** Every element this module resolves is used immediately, within the same worker-thread call, and discarded — never returned, never cached, never crosses a thread or an `.await` point. `UiaJob`/`UiaOutcome` carry only plain data (`String`/`bool`/small typed structs).
- **Containment is proven, never assumed.** A resolved element's ancestor chain (via `RawViewWalker`/`GetParentElement`, bounded to 25 hops) must terminate at the Grant-verified surface's own HWND — never a `CurrentProcessId`/direct-HWND-equality check (proven not to work against WebView2's real multi-process architecture — see `browser_b5_master_plan.md` §5.2).
- **Selector ambiguity fails closed, never picks first.** `FindFirst` is never used anywhere in this module; every resolution goes through `FindAll` + an exact `Length() == 1` check.
- **`.click` uses `InvokePattern` only; `.type` uses `ValuePattern` only.** Neither uses `SendInput`, global mouse/keyboard injection, or raw screen coordinates; either pattern's absence on the resolved element fails closed as `UnsupportedControlPattern`.
- **`browser.type`'s sensitive-input refusal is not re-implemented or re-checked here** — the existing backend, Grant-mint-time check (`grant.py::is_sensitive_type_target`/`looks_like_secret_value`) is the only gate; `execute_type` is reachable only via a Grant that already passed it, and the recomputed hash proves the typed text was not substituted after minting.
- **A bounded worker pool is the sole admission-control mechanism** — no pending-work queue (fails closed immediately, `PoolExhausted`, rather than queuing, which would itself be an attacker-fillable resource); worker creation is serialized under the pool's own mutex (closing the LIVE VERIFIED first-instantiation `CoCreateInstance` race — see §7); a per-call timeout never claims to cancel the underlying blocked native call, and a timed-out worker is abandoned (never `TerminateThread`'d) up to a hard, process-lifetime cumulative-creation cap.

## 6. Capability Matrix

| Capability | Auth | Live surface/profile/generation binding | Parameter-hash verified | Policy-enforced | Real execution | Audited |
|---|---|---|---|---|---|---|
| `navigate` | ✓ | ✓ | ✓ | ✓ (B4, unmodified) | ✓ | ✓ (STARTED/SUCCEEDED/FAILED) |
| `screenshot` | ✓ | ✓ | ✓ | N/A (no navigation) | ✓ (viewport-only) | ✓ |
| `read` | ✓ | ✓ | ✓ (new) | N/A | ✓ (new — bounded UIA text read) | ✓ (new) |
| `extract` | ✓ | ✓ | ✓ (new) | N/A | ✓ (new — bounded UIA field extraction) | ✓ (new) |
| `click` | ✓ | ✓ | ✓ (new) | N/A | ✓ (new — `InvokePattern` only) | ✓ (new) |
| `type` | ✓ | ✓ | ✓ (new) | N/A (sensitive-input refusal is backend, Grant-mint-time) | ✓ (new — `ValuePattern` only) | ✓ (new) |
| `download` | ✓ | ✓ | N/A | N/A (B4 denies unconditionally) | `NotYetEnabled` | Grant lifecycle only |

## 7. Threat Model / Adversarial Review

Formal review against the required attack categories. Navigate/screenshot rows are unchanged from the prior cycle (re-listed for completeness); UIA-specific rows are new this cycle. Categories specific to `.download` (path traversal, disk exhaustion) remain out of scope, not silently passed — that capability is unimplemented.

| Attack | Classification if unmitigated | Status |
|---|---|---|
| Forged / replayed / expired / tampered Grant | CRITICAL | Mitigated (B5.0-B5.4, unchanged) |
| **Parameter substitution** (valid Grant, different URL/params/selector/text redeemed) | CRITICAL | **Mitigated.** Proven for navigate (prior cycle) and, this cycle, for each of read/extract/click/type via `execute_{read,extract,click,type}_rejects_parameter_substitution_before_touching_runtime`-style tests — the recomputed hash mismatch is caught before `uia_root_hwnd` is ever called. |
| Wrong surface / profile / tenant / generation | CRITICAL/HIGH | Mitigated (unchanged); this cycle additionally proven for a UIA capability specifically via `execute_click_rejects_stale_navigation_generation_before_touching_uia_pool` — LIVE VERIFIED too (a real UIA-triggered click observably advanced `navigation_generation` from 1 to 2, the exact signal this check depends on). |
| **`node_ref`-only resolution used to smuggle a cached/opaque reference past the typed selector contract** | HIGH | **Mitigated (new).** `to_uia_selector()` returns `None` for a `node_ref`-only selector; every one of extract/click/type maps that to `NodeRefOnlyNotSupported` before touching the pool — proven by 3 dedicated tests plus 4 pure `to_uia_selector` unit tests. |
| **Cross-surface element resolution** (a selector that would match content on a DIFFERENT surface) | CRITICAL | **Mitigated.** UIA's own object model structurally prevents `FindAll` from crossing into another surface's subtree (Microsoft Learn, quoted in `browser_b5_master_plan.md` §5.1) — LIVE VERIFIED this cycle against the actual shipped functions (not spike code): a selector unique to surface B's own page, resolved against surface A's root, returned `SelectorNotFound`, reproduced twice. |
| **Ambiguous selector silently resolving to an arbitrary element** | HIGH | **Mitigated.** `FindFirst` is never used anywhere in this module; `FindAll`+`Length()==1` is the only resolution path. LIVE VERIFIED: 4 real checkboxes with no distinguishing name correctly returned `SelectorAmbiguous { match_count: 4 }`, reproduced twice. |
| **First-instantiation `CoCreateInstance(CUIAutomation)` race** (concurrent worker creation) | HIGH (availability/correctness — a spurious `E_FAIL`, or worse) | **Mitigated.** Worker creation is serialized under the pool's own mutex. LIVE VERIFIED (real COM, not mocked): 8 real OS threads racing `submit()` on a cold pool produced no panic and no spurious `ComFailure`, every time. A SEPARATE, related hazard (a retired worker's own unsynchronized teardown racing a fresh worker's creation) was found and is tracked as OD-B22 — see below. |
| **Hung native UIA call wedging the pool / a shared worker** | HIGH (availability — a triggerable DoS by an ordinary slow page) | **Mitigated by design** (a bounded pool of per-call-dispatched, individually-abandonable workers, never one shared long-lived worker — the specific defect the D44 adversarial review found in the original proposed design). `TerminateThread` is never used (documented unsafe). **Not live-tested** — deliberately inducing a genuine hang to prove recovery is judged too risky an experiment, per this and every prior gate's own explicit prohibition. |
| **Sensitive-input bypass via `.type`** | CRITICAL | Mitigated (unchanged, backend Grant-mint-time check, B5.0-B5.4) — not re-implemented or re-checked at the UIA execution layer, since there is no second, lower-level path into `execute_type` that could bypass it. |
| **Click/type escaping to global mouse/keyboard/screen coordinates** | CRITICAL | **Mitigated.** `.click` uses `InvokePattern` only; `.type` uses `ValuePattern` only; neither ever calls `SendInput` or uses raw coordinates. Either pattern's absence fails closed as `UnsupportedControlPattern`. |
| **Worker-pool exhaustion / unbounded thread growth** | HIGH (availability) | **Mitigated.** No pending-work queue (fails closed immediately); a hard `max_lifetime_creations` cap bounds total leaked-thread growth even under sustained, repeated timeouts. LIVE VERIFIED (real COM): both the `max_workers` cap and the `max_lifetime_creations` cap fail closed with `PoolExhausted` exactly as designed. |
| **Iframe boundary escape** (reading/acting on cross-origin iframe content the surface doesn't own) | MEDIUM | Not applicable as a distinct escape — UIA's own tree scoping means such content, if unreachable, simply cannot match any selector (`SelectorNotFound`), not a distinct bypass. Not live-tested against a real iframe this cycle (see Known Limitations). |
| Malformed/encoded-bypass URI, redirect to denied target (human path) | HIGH | Mitigated (B4, unchanged, extensively tested) |
| DNS rebinding | HIGH | **Not mitigated.** Unchanged, disclosed B4 gap. |
| Timeout used to hide a real outcome | MEDIUM | Mitigated: `Timeout` is a distinct, honest, typed outcome. |
| Screenshot data leakage (disk persistence, audit leakage) | MEDIUM | Mitigated (unchanged). |
| Audit log manipulation/leakage of secrets or full URLs | MEDIUM | Mitigated (unchanged redaction convention — capability/surface/outcome only, never a URL, selector, or typed text). |

No BLOCKER finding. No unresolved CRITICAL/HIGH finding against what was actually built this cycle. One genuine bug (COM release-before-uninitialize ordering) was found and fixed during this cycle's own testing — see §8/§17. One narrower finding (OD-B22, retired-worker teardown/fresh-worker-creation race) is disclosed, accepted, not remediated — the only fix would defeat the abandon-never-kill design's own purpose.

## 8. Targeted Test Results

**Rust** (`cargo test --lib`, full crate): **193 passed, 0 failed, 2 ignored** (the 2 ignored are pre-existing, environment-gated real-Windows-Credential-Manager tests, unrelated to Browser). Prior cycle: 169 passed (27 in `browser_grant`, including the cross-language parameter-hash fixture and the concurrency-serialization test). This cycle adds **24 new tests**:
- **16 grant-level** (`browser_grant.rs`): params-required, parameter-hash-substitution-rejected, and `node_ref`-only-rejected, for each of `read`/`extract`/`click`/`type` (12 tests), plus 4 pure `SelectorWire::to_uia_selector` unit tests. Prove the security-critical ordering — hash verification and `node_ref` rejection both happen strictly before `BrowserRuntime::uia_root_hwnd` is ever called (`FakeBrowserRuntime::uia_root_hwnd` panics via `unimplemented!()` if a test ever reached it, so a regression here fails loudly).
- **1 generation test**: `execute_click_rejects_stale_navigation_generation_before_touching_uia_pool`.
- **7 real-COM tests** (`browser_uia.rs`) — genuinely exercise Windows COM/UIA (`CoInitializeEx`/`CoCreateInstance(CUIAutomation)`/`CoUninitialize`), not a mock: worker creation and teardown; fail-closed behavior against an unresolvable HWND; the `max_lifetime_creations` cap; the `max_workers` cap; 8 real concurrent OS threads racing `submit()` on a cold pool. Serialized against each other via a `static Mutex` (matching production's own single-pool topology — see §17 for why this was necessary).

`cargo build`: 0 warnings. `cargo clippy --lib --tests`: 0 new warnings (added a `NewWorkerHandles` type alias to close a `type_complexity` lint the new pool code introduced; 1 pre-existing, `sidecar.rs`, unrelated, unchanged). `cargo fmt --check`: clean for every file this cycle touched (a whole-crate `cargo fmt` run briefly reformatted the untouched, unrelated `secure_keys.rs` as a side effect; caught and reverted — see §13 — so `cargo fmt --check` on the full crate now reports that one file's own pre-existing, unrelated drift, exactly as it did before this cycle began). `git diff --check`: clean.

**Backend**: unchanged this cycle — no backend file was touched (this cycle is desktop-only). Prior cycle's targeted suite (**97 passed**) still applies unchanged. The full backend regression suite was NOT re-run this cycle, per this milestone's own explicit testing policy — deferred to the final Browser validation stage.

## 9. Live Verification

**Prior cycle (navigate/screenshot)**, unchanged: a disposable preflight confirmed, against the real, pinned WebView2 runtime, a policy-allowed navigation resolving `Success`, a policy-denied (loopback) navigation resolving `PolicyDenied`, and a real `CapturePreview` producing a valid PNG. See the decision log for the full transcript; not repeated here.

**This cycle (UIA execution)** — a disposable, env-var-gated preflight (`KORTEX_BROWSER_B5_UIA_LIVE_PREFLIGHT`), spawned on its own thread from `lib.rs`'s `.setup()`, calling the REAL `WebView2RuntimeAdapter` and REAL `UiaWorkerPool` directly (never through the Grant/hash/signature layer, which is pure Rust logic already covered by unit tests against a fake runtime — this preflight exists specifically to prove behavior against real WebView2 accessibility content, which no unit test can construct). Two real, concurrently-open surfaces (`https://example.com`, `https://httpbin.org/forms/post`) — reproduced twice, with identical qualitative results both times. Second run's transcript:

```
[B5-UIA-PREFLIGHT] surface A (example.com) finished loading: https://example.com/
[B5-UIA-PREFLIGHT] surface B (httpbin forms) finished loading: https://httpbin.org/forms/post
[B5-UIA-PREFLIGHT] hwnd A = Ok(3409952), hwnd B = Ok(1444102)
[B5-UIA-PREFLIGHT] distinct HWNDs: true
[B5-UIA-PREFLIGHT] READ A: truncated=false text="Example Domain Example Domain - Web content Example Domain Example Domain This domain is for use in documentation examples without needing permission. Avoid use in operations. Learn more"
[B5-UIA-PREFLIGHT] READ B: truncated=false text="httpbin.org/forms/post httpbin.org/forms/post - Web content Customer name: Customer name: Telephone: Telephone: E-mail address: E-mail address: Pizza Size Pizza Size Small Medium Large Pizza Toppings Pizza Toppings Bacon Extra Cheese Onion Mushroom Preferred delivery time: Preferred delivery time: H"
[B5-UIA-PREFLIGHT] EXTRACT B (customer name field): Ok(Ok(Extract { fields: {"customer_name_field": ExtractedField { accessible_name: "Customer name:", control_type: "50004", value: Some("") }} }))
[B5-UIA-PREFLIGHT] TYPE B (customer name field): Ok(Ok(Type))
[B5-UIA-PREFLIGHT] EXTRACT B after TYPE (confirm typed value stuck): Ok(Ok(Extract { fields: {"customer_name_field": ExtractedField { accessible_name: "Customer name:", control_type: "50004", value: Some("Kortex Live Test") }} }))
[B5-UIA-PREFLIGHT] CLICK B (ambiguous checkbox selector, expect SelectorAmbiguous): Ok(Err(SelectorAmbiguous { match_count: 4 }))
[B5-UIA-PREFLIGHT] CLICK A-with-B's-selector (expect SelectorNotFound): Ok(Err(SelectorNotFound))
[B5-UIA-PREFLIGHT] CLICK B (real submit button): Ok(Ok(Click))
[B5-UIA-PREFLIGHT] navigation_generation B before=Ok(1) after=Ok(2)
[B5-UIA-PREFLIGHT] surface B url after submit: https://httpbin.org/post
[B5-UIA-PREFLIGHT] done
```

Every observed outcome is correct: two distinct HWNDs; `Read` returned real, correct page content on both surfaces (not a stub); `Extract` uniquely resolved a real `edit` control and correctly reported its starting value (`""`) and its live `control_type` (`50004`, confirmed live as `UIA_EditControlTypeId`); `Type` (`SetValue`) followed by a second `Extract` round-tripped correctly — the field read back exactly `"Kortex Live Test"`, the single highest-risk previously-unverified claim in this whole design (does `.type` actually write real, persisted content into a real WebView2 form field?), now proven; an intentionally ambiguous selector (4 real checkboxes, no distinguishing name) correctly resolved `SelectorAmbiguous { match_count: 4 }`; a cross-surface attempt (resolving surface B's own button using surface A's root) correctly resolved `SelectorNotFound`, proving structural isolation for the actual shipped code, not merely the prior architecture-gate's spike; a real `Click` (`InvokePattern::Invoke`) on the real submit button succeeded and caused a real page navigation, confirmed both by the resulting URL and by `navigation_generation` observably advancing from 1 to 2 — proving the generation-tracking invariant correctly registers a UIA-triggered navigation, not only a Grant-issued `navigate()` call. All temporary preflight code was removed after this run; confirmed absent via `git diff --check`/`git status` (both clean, zero trace).

**Not re-verified live this round**: held-element staleness after navigation (this module never holds an element across calls at all, by design — there is no equivalent scenario to construct without deliberately violating the architecture; D45's prior finding, against spike code, is inherited as the governing rationale); iframe behavior (neither test page used one); hung-call/timeout recovery (deliberately not attempted, per this and every prior gate's own explicit prohibition on inducing a hang); `browser.type`'s sensitive-input refusal (backend-only, not reachable from the desktop layer); worker-pool cap/exhaustion under real (as opposed to invalid-HWND) load (covered instead by the automated real-COM tests in §8, deliberately against HWND `0` for determinism).

## 10. Graph/Architecture Evidence

`graphify update .` was run against the full repository after implementation (23,349 nodes, 55,176 edges, 727 communities). Verified, not merely run: `graphify explain "UiaWorkerPool"` correctly resolves to `browser_uia.rs:253`, with edges to all four call sites (`execute_read`/`execute_extract`/`execute_click`/`execute_type` in `browser_grant.rs`), its own `submit`/`retire`/`retire_and_join_for_test`/`drop` methods, and the test harness (`Harness`) — confirming the graph reflects the actual, current, fully-wired implementation rather than a stale or aspirational snapshot.

## 11. Known Limitations

See `browser_known_limitations.md`'s "As of Browser-B5" section and `browser_b5_master_plan.md` §7 for the full, consolidated list — this cycle's own additions: `node_ref`-only resolution refused, not built; a retired UIA worker's own COM teardown is unsynchronized with later worker creation (OD-B22, a real, LIVE VERIFIED hazard in this project's own test suite, disclosed not remediated); the `READINESS_MIN_DESCENDANTS` threshold is REASONED from two data points, not a larger sample; iframe content surfaces as `SelectorNotFound`, not a dedicated error, and was not live-tested against a real iframe; held-element staleness and hung-call recovery carry forward from D45/D46 as REASONED, not re-demonstrated against this cycle's own shipped code (see §9 for why). Everything from the prior cycle (DNS rebinding, human-vs-AI waiter cross-talk, screenshot viewport-only, etc.) remains unchanged.

## 12. Deferred Risks

- A real download-policy subsystem, needed before `.download` can execute at all (`browser_b5_master_plan.md` §6).
- OD-B22 (retired-worker COM-teardown race) — no fix is proposed; joining on retire would defeat retire's own hang-avoidance purpose. Flagged for a future hardening pass if this risk class is ever judged unacceptable (the child-process-based alternative D46 already documents has no equivalent race, at the cost of a new local IPC transport).
- Human-vs-AI navigation waiter cross-talk (prior cycle) — closing it would mean adding synchronization to the frozen B1/B2 human-facing command path, judged disproportionate.
- Live-testing iframe boundary behavior and hung-call recovery, ideally alongside whichever future phase next touches this code (hung-call recovery specifically needs its own careful, dedicated design pass before even attempting a safe live test — not a quick addition to an existing preflight).
- Live-testing redirect-through-the-AI-path and surface-destruction-mid-wait (prior cycle, still open).

## 13. Git Status

Working tree, at the time of this report: `git diff --check` clean; `git status` shows only files this milestone (and, separately, a prior, unrelated documentation-reconciliation pass) actually touched. This cycle's own changes: `apps/desktop/src-tauri/src/browser_uia.rs` (new, untracked), `apps/desktop/src-tauri/src/browser_grant.rs`, `apps/desktop/src-tauri/src/browser_runtime.rs`, `apps/desktop/src-tauri/src/lib.rs`, `apps/desktop/src-tauri/Cargo.toml` (all modified — see §4), plus all five B5-scoped documentation files (`browser_b5_master_plan.md`, `browser_b5_implementation_report.md` — this file, `browser_decision_log.md`, `browser_known_limitations.md`, `browser_roadmap_b0_b10.md`). `.kortex/roadmap.md`, `CHANGELOG.md`, `docs/release/RELEASE_CANDIDATE_READINESS.md` show as modified too, but confirmed by inspection to be an entirely separate, pre-existing, unrelated documentation-reconciliation effort (a Release Candidate backfill covering post-`v1.0.0-rc.1` work) that predates this session and this milestone — not part of this cycle's own diff, and not touched by this cycle. `scratch/` confirmed untracked, pre-existing, unrelated. A formatting-only, unintended side effect of running `cargo fmt` on the whole crate (rather than scoped to touched files) briefly reformatted `apps/desktop/src-tauri/src/secure_keys.rs`; caught during this report's own git-status review and reverted (`git checkout --`) before this report was finalized — confirmed absent from the final diff. No force-push, amend, or history rewrite used at any point. `v1.0.0-rc.2` confirmed unchanged.

## 14. CI Status

Not pushed as of this report — no CI run exists yet for this cycle's changes. Per this milestone's own CI policy: CI verification for B5-scoped work is not a blocking requirement; the authoritative, complete Backend + Desktop CI verification is deferred to the Browser Technical RC phase.

## 15. Final Verdict

**READY FOR COMMIT — for the scope actually delivered this cycle (`read`/`extract`/`click`/`type` UIA execution), on top of the prior cycle's navigate/screenshot execution, which remains unregressed.**

No BLOCKER or unresolved CRITICAL/HIGH finding against the delivered scope. One genuine, previously-unknown bug (the UIA worker COM release-before-uninitialize ordering defect) was found by this cycle's OWN testing — not by inspection — and fixed, with the fix itself LIVE VERIFIED via 5 consecutive clean full-suite runs where the unfixed code reliably crashed. One narrower, disclosed, unremediated finding (OD-B22) is a genuine tradeoff, not an oversight: the only fix would reintroduce the exact hang-risk this architecture exists to avoid. The single largest architectural question this milestone opened (`.read`/`.extract`/`.click`/`.type`'s access mechanism) is now not just answered but implemented, adversarially tested, and live-verified against real WebView2 content, including the highest-risk previously-unproven claim (`.type` actually persisting real content, round-tripped and confirmed).

Awaiting explicit user review of this report and the other four updated B5 documents, and explicit commit/push authorization, per this repository's milestone workflow.
