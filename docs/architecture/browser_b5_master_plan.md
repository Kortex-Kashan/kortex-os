# Browser-B5 Master Plan — Browser Capability Layer

**Status**: consolidated, single reference for the entire B5 milestone. The internal stages previously tracked as B5.0–B5.11 are retired as separate milestone identifiers per explicit owner direction ("KORTEX OS — B5 MASTER IMPLEMENTATION") — this document and `browser_b5_implementation_report.md` are the only B5-scoped documents going forward. Do not create further `B5.x`-numbered documents.

**Baseline**:
- B4: `10f1b4ccf2488b64aadbc2efc632dcd82bf9698a`
- B5 foundation (Grant mint/verify, capability registration, no execution): `fbddfab5d8fa698eb986ba501604475eae38241f` (`origin/main` at the start of this milestone)
- `v1.0.0-rc.2` remains frozen at `041084539f4c68a05db4ffa49903bf8c726c4300` — untouched by this milestone.

---

## 1. What B5 delivers

The complete `kortex.browser.*` capability layer: typed, governed, authenticated, tenant/profile/surface-bound, policy-controlled, audited, fail-closed execution for every capability the architecture actually permits real execution for in this cycle, with every other capability still fully governed but explicitly, honestly deferred rather than silently stubbed.

| Capability | Status this milestone | Execution mechanism |
|---|---|---|
| `kortex.browser.navigate` | **Real execution** | `BrowserRuntime::navigate` + `arm_navigation_waiter`, reusing B4's unmodified `NavigationStarting`/`NavigationCompleted` pipeline |
| `kortex.browser.screenshot` | **Real execution** (viewport only) | `BrowserRuntime::capture_screenshot` via `ICoreWebView2::CapturePreview` |
| `kortex.browser.read` | Grant mint/verify/redeem fully governed; execution `NotYetEnabled` | **Blocked on an unresolved architecture question — see §5** |
| `kortex.browser.extract` | Same as `.read` | Same as `.read` |
| `kortex.browser.click` | Same as `.read` | Same as `.read` |
| `kortex.browser.type` | Same as `.read` (sensitive-input refusal at Grant-mint time already fully implemented and tested since B5.0-B5.4) | Same as `.read` |
| `kortex.browser.download` | Unchanged from B5.0-B5.4 — Grant mint/verify only, execution unconditionally refused | Deliberately untouched — see §6 |

No capability was silently skipped. `.read`/`.extract`/`.click`/`.type` are not implemented this cycle because their execution mechanism was never architecturally specified (not in the original B5 gate, not in this milestone's own authorization) and building it requires a substantial, novel, security-relevant design decision — see §5. This is reported, not hidden.

---

## 2. Canonical execution path (as actually implemented)

```
AI / authorized caller
      |
      v
typed Browser capability (backend, unchanged since B5.0-B5.4)
      |
      v
CapabilityDispatcher -> RBAC/ABAC -> ToolGovernanceEvaluator -> DurableAIApprovalPolicy
      |
      v
Capability Execution Grant minted (signed, short-lived, parameter-hash-bound)
      |
      v
Desktop redeem command (browser_execute_granted_action)
      |
      v
signature -> expiry -> single-use -> live surface -> live tenant/profile -> live generation
      |
      v
[NAVIGATE / SCREENSHOT ONLY] recompute canonicalize_and_hash, compare to Grant — fail closed (GrantInvalid) on mismatch
      |
      v
BrowserRuntime (navigate / capture_screenshot) — the SAME trait methods a human-driven command already uses
      |
      v
WebView2 NavigationStarting/NavigationCompleted (B4 BrowserPolicyEngine, unmodified, unbypassable)
      |
      v
typed result (Success | PolicyDenied | NavigationFailed | Timeout | ScreenshotFailed | ...)
      |
      v
desktop-local audit (BROWSER_GRANT_REDEEMED/_REJECTED, BROWSER_EXECUTION_STARTED/_SUCCEEDED/_FAILED)
```

No second dispatcher, no second authorization engine, no second approval engine, no new transport, no arbitrary JavaScript, no raw WebView2 handle exposed to AI. `BrowserPolicyEngine` (B4) was not modified — `evaluate_navigation` is called verbatim, from the identical `NavigationStarting` COM callback a human click already goes through.

---

## 3. OD-01 — Parameter-hash verification (RESOLVED, implemented)

**The gap** (identified during the B5.5 architecture-gate review before this consolidated authorization): a Capability Execution Grant carries only `canonicalized_parameters_hash` — a SHA-256 digest — never the raw parameters (by design, so the Grant model stays safe to log/audit verbatim). The redeem command therefore had no way to know a `browser.navigate` Grant's actual destination URL.

**Resolution, as implemented**: the desktop redeem command now accepts an explicit, narrowly-typed `params: Option<BrowserCapabilityParamsWire>` argument alongside the Grant (`Navigate { url, timeout_ms }` / `Screenshot { full_page }` — a tagged enum, never a generic capability-agnostic blob). Before touching `BrowserRuntime` at all, it:

1. Reconstructs the exact `{"target": {...from the Grant's own live-verified fields...}, ...capability-specific fields}` object the backend hashed at mint time.
2. Recomputes the hash via a Rust port of `canonicalize_and_hash` (`backend/src/kortex/engines/browser/grant.py`).
3. Compares it to `grant.canonicalized_parameters_hash` — fails closed (`GrantInvalid`) on any mismatch, before any runtime call.

**Cross-language correctness, proven not assumed**: `grant.py::canonicalize_and_hash` was changed to serialize with `separators=(",", ":")` (compact) instead of Python's default spaced separators, so it matches `serde_json`'s own compact, key-sorted (this crate does not enable `preserve_order`) output byte-for-byte — closing the exact class of cross-language bug that already shipped once in the Grant's signature payload (D37). Proven via `real_rust_hash_matches_real_python_hash` (Rust), embedding four digests captured from the actual, currently-shipping Python function — including one fixture constructed specifically to prove a substituted URL changes the hash. `execute_navigate_rejects_parameter_substitution_before_touching_runtime` additionally proves, structurally, that `BrowserRuntime::navigate` is never called when the hash doesn't match.

## 4. OD-02 — Navigation event correlation (RESOLVED, implemented)

**The gap**: `BrowserRuntime::navigate()` returns as soon as the WebView2 COM call is *issued* — not once B4 policy allows/denies the destination, or the navigation succeeds/fails at the network layer. Those are asynchronous, event-driven facts (`NavigationStarting`/`NavigationCompleted`) with no return channel to the original caller.

**Resolution, as implemented**: a per-surface, one-shot waiter (`SurfaceEntry::navigation_waiter: Arc<Mutex<Option<oneshot::Sender<NavigationOutcome>>>>`), armed by the new `BrowserRuntime::arm_navigation_waiter` **before** `navigate()` is issued (never after — arming-after would race a fast Deny/Complete against a waiter that isn't listening yet). Resolved, with `.take()` clearing the slot atomically, by whichever of:
- `NavigationStarting`'s `Deny` arm (a denied navigation never reaches `NavigationCompleted` at all), or
- `NavigationCompleted` (now reads `IsSuccess`/`WebErrorStatus` — previously discarded entirely)

fires next. The redeem command `.await`s the returned `oneshot::Receiver` under `tokio::time::timeout`, bounded by the capability's own `timeout_ms` (clamped to `[100, 300_000]` — the desktop does not trust the wire value alone, since the backend's own Pydantic bound is a separate trust boundary). The per-surface `SurfaceRedeemLocks` guard is held for the **entire** arm-issue-await sequence, not just the pre-execution checks, so two concurrent AI-originated redemptions of the same surface can never interleave or cross-talk over the same waiter slot — proven by `concurrent_redemptions_on_same_surface_are_serialized`, which drives two real concurrent `execute_granted_action` calls against one surface and asserts strict ordering.

**Disclosed residual limitation**: a **human**-driven navigation (the separate `browser_navigate` command, unmodified) fires the identical `NavigationStarting`/`NavigationCompleted` events. If a human actively navigates the *same* tab an AI redemption is concurrently waiting on, that event could resolve the AI's waiter instead of a genuine AI-issued navigation's own outcome. This is a real, narrow, disclosed limitation — not a security-boundary break (no tenant/authorization confusion results; B4 policy still applies to whichever URL actually loaded; the AI caller receives some typed outcome, at worst attributed to the wrong navigation, never an untyped hang) — left open because closing it would require adding synchronization to the frozen, already-shipped B1/B2 human-facing command path, which was judged disproportionate to this milestone's scope. Tracked as a known limitation (§7).

## 5. OPEN — `.read`/`.extract`/`.click`/`.type` execution mechanism (formally investigated, NOT implemented)

A dedicated architecture-validation pass (six independent parallel investigations, followed by synthesis and three independent adversarial reviews specifically tasked with trying to refute the synthesis) was run before writing any code. This section records that investigation's actual, evidence-backed findings — not a plan, a verdict on real evidence.

### 5.1 What is CONFIRMED (the underlying primitives are real)

- **A per-surface, unique HWND is retrievable today**, via the exact idiom already used for `GoBack`/`Settings`: inside the existing `with_core_webview2` STA closure, `platform_webview.controller()` → `ICoreWebView2Controller::ParentWindow` (`webview2-com-sys-0.38.2/src/bindings.rs:9137`). This resolves to wry's own `WRY_WEBVIEW` native child window (`wry-0.55.1/src/webview2/mod.rs:181-280`, a real, distinct `CreateWindowExW` call per surface — confirmed structurally, not guessed), never shared between browser tabs and never KORTEX's own top-level window. **Caveat, not yet closed**: this is one level above the actual Chromium-painting window — WebView2 creates a further internal child window under it that wry itself has no typed accessor for and must reach today only via `GetWindow(hwnd, GW_CHILD)`/`EnumChildWindows` (confirmed: wry's own `drag_drop.rs:44-61` does exactly this, commented "Enumerate child windows to find the WebView2 'window'"). Whether `ElementFromHandle` on the outer container alone is sufficient, or a further scoped child-window step is required, needs a live spike, not assumed.
- **The pinned `windows` 0.61.3 crate has everything needed**: `IUIAutomation`, `IUIAutomationElement`, `IUIAutomationCondition`, `IUIAutomationTreeWalker`, `IUIAutomationInvokePattern`, `IUIAutomationValuePattern`, `IUIAutomationLegacyIAccessiblePattern` — all confirmed present with exact signatures, via `CoCreateInstance(CUIAutomation)` + `IUIAutomation::ElementFromHandle` (`UiaAccessibleObjectFromWindow` does **not** exist in this crate — confirmed absent by exhaustive grep). The gating feature, `Win32_UI_Accessibility` (plus `Win32_System_Ole`/`Win32_System_Variant` for VARIANT-based methods), is purely additive to what's already enabled — no conflict, no new crate.
- **UIA's own object model structurally prevents subtree escape**: Microsoft Learn, quoted directly — `FindAll`/`FindFirst` "cannot search for ancestor elements... `TreeScope_Ancestors` is not a valid value for the scope parameter," and "the Find methods do not support searching up the... tree." A different top-level HWND's element is unreachable from another surface's root by construction, not merely by convention.
- **Stale-element and ambiguous-match detection are both real and precise**: `UIA_E_ELEMENTNOTAVAILABLE` (`0x80040201`) is a genuine, catchable HRESULT (Microsoft's own error-codes page, quoted); `IUIAutomationElementArray::Length() > 1` after `FindAll` is a clean, deterministic ambiguity signal.
- **No existing typed contract needs to change.** `BrowserElementSelector` (role/accessible_name/node_ref) and the Grant/parameter-hash/wire-params machinery (`BrowserCapabilityParamsWire`) are already sufficient — a UIA executor would be purely additive new Rust code (new enum variants, a new executor module), never a change to any Python model or to `navigate`/`screenshot`'s own code.
- **The STA/MTA deadlock risk is real and avoidable in principle.** Microsoft's own threading documentation, quoted directly: UIA calls should run on a dedicated MTA thread that "should not own any windows" — the existing `with_webview` closures run **on** WebView2's own STA UI thread, so issuing a UIA call from inside one risks a genuine, concretely-reachable deadlock (the target thread can't service the cross-thread message a UIA call needs while it's blocked running the closure). A dedicated worker thread, initialized once with `CoInitializeEx(COINIT_MULTITHREADED)`, handling only the plain `HWND` (confirmed apartment-agnostic) and never a raw COM/UIA pointer, was identified as the correct shape — matching this codebase's own `arm_navigation_waiter`/`capture_screenshot` oneshot-completion precedent in spirit.
- **WebView2 does expose its content via UIA** — Microsoft's own WebView2 team confirmed this directly on GitHub ("WebView2 uses UIA for its accessibility tree... Narrator should work correctly," MicrosoftEdge/WebView2Feedback#2330). **But activation is not free**: Chromium (and, by inheritance, WebView2) builds its accessibility tree lazily, on-demand — the specific trigger mechanism found (a `WM_GETOBJECT` message to the render window activating on-demand mode) is sourced from an unofficial third-party wiki, not Microsoft documentation for this exact WebView2 combination, and is **not yet confirmed**. Known, Microsoft-acknowledged gaps regardless of activation: cross-origin (out-of-process) iframes render as a single opaque node with no content; NVDA/JAWS have documented WebView2-specific reading bugs.

### 5.2 What the adversarial review found wrong with the *specific* proposed design (all three lenses independently refuted the synthesis's GO_WITH_CONDITIONS verdict)

These are not restatements of the confirmed-primitive gaps above — each is a genuine defect in how the synthesis proposed to *use* those primitives, found by three separate reviewers each specifically tasked with trying to refute the verdict, none of whom knew what the others would find:

1. **The proposed process-identity cross-check does not work, and would reject every legitimate element.** The design proposed comparing a resolved element's `CurrentProcessId`/`CurrentNativeWindowHandle` against KORTEX's own process/HWND as a defense-in-depth double-check. But WebView2 always runs its renderer in a **separate OS process** (`msedgewebview2.exe` — Microsoft's own "Process model for WebView2 apps" documentation, quoted directly: "A WebView2 process group includes... one or more renderer processes"). A real DOM element's `CurrentProcessId` will therefore report the *WebView2 Runtime's* process ID, never KORTEX's own — so this check, implemented as literally proposed, fails for every legitimate resolution, not just malicious ones. `CurrentNativeWindowHandle` is separately confirmed (Microsoft's own property definition) to read `0` for virtual, non-HWND-backed elements — i.e. for exactly the DOM elements click/type/extract need to act on — making that half of the check vacuous too. **Neither of the two proposed independent checks is usable as specified.** A correct scoping proof must rest on tree containment alone (§5.1's confirmed `FindAll`/`FindFirst` ancestor-search prohibition), not on a process/window comparison that was never actually validated against WebView2's real, multi-process architecture.
2. **The design's own selector-resolution sketch contradicts its own fail-closed requirement.** It proposes `FindFirst` as a valid way to resolve `accessible_name` — but `FindFirst` returns exactly one element with no count exposed at all, so it structurally cannot detect an ambiguous match. Real pages routinely have multiple same-named elements (repeated "Delete"/"Submit" buttons in a list). If `accessible_name` resolution ever goes through `FindFirst` (as the design itself names as an acceptable option), an ambiguous selector silently resolves to whichever element UIA's tree-walk visits first — the exact "silently choose an arbitrary element" outcome this project explicitly forbids. **`FindFirst` must never be used for any selector resolution that requires ambiguity detection; every such resolution must go through `FindAll` with an explicit `Length() == 1` check.**
3. **The proposed threading/completion model would cause a real, triggerable denial of service.** The design claimed its worker-thread pattern "mirrors" `arm_navigation_waiter`/`capture_screenshot` — it does not. Both of those are genuinely non-blocking (`CapturePreview` is an async WebView2 API with its own completion handler; no OS thread is ever blocked waiting on it). Every UIA method needed here (`ElementFromHandle`, `FindFirst`/`FindAll`, `Invoke`, `SetValue`) is a **synchronous, blocking COM call with no async/completion-handler variant anywhere in the crate** — and because the renderer lives in a separate process, each call is a real cross-process RPC that can hang (a busy renderer, a native modal, a slow page, an accessibility tree that hasn't finished building). A `tokio::time::timeout` can abandon the *await* on the async side, but cannot cancel the underlying blocked native call — the `windows` crate exposes no cancellation token for these methods. The design's "one dedicated, long-lived worker thread... handling every surface" means a single hung call permanently wedges browser automation across **every** tab, for the remainder of the process's life, with no stated recovery (`TerminateThread` mid-COM-call is itself documented as unsafe/corrupting). This is triggerable by an ordinary slow or busy page — not an adversarial one. **A shared, long-lived worker thread is not an acceptable design for this; a bounded-lifetime, per-call (or per-call-with-hard-timeout-and-abandon) execution model is required instead, accepting that a hung call leaks one throwaway thread rather than poisoning a shared one.**

### 5.3 Verdict and disposition

**Not implemented this cycle.** The underlying primitives are real (HWND retrieval, UIA crate bindings, structural containment, no contract changes needed) and a corrected design is plausibly buildable — but the *specific* design that came out of the six investigations has three concrete, adversarially-confirmed defects, at least one of them (the threading/DoS gap) severe enough that shipping it as originally proposed would have been a real regression, not a completed feature. Per this milestone's own instruction ("if UIA cannot be safely scoped... STOP and report the blocker," "never label something verified if it was only code-inspected," "a secure NotYetEnabled state is preferable to an insecure 'working' [feature]"), `.read`/`.extract`/`.click`/`.type` remain fully governed (Grant mint, RBAC/ABAC, approval, signature/expiry/single-use/live-binding all enforced) but honestly report `NotYetEnabled`.

**What a corrected design would need, before implementation is attempted again**:
- Drop the process-ID/native-window-handle cross-check as specified; rely on UIA's own confirmed tree-containment guarantee instead, or design a genuinely different verification (e.g., confirm the resolved element's ancestor chain, walked via `GetParentElement`, terminates at the known-good root element — never a raw process/HWND comparison against KORTEX's own identity).
- Ban `FindFirst` outright for any selector resolution; always `FindAll` + explicit `Length() == 1`, fail closed otherwise.
- Replace the shared-worker-thread model with a bounded-lifetime execution strategy (e.g., a fresh, throwaway OS thread per UIA call with a hard wall-clock timeout; abandon and leak rather than reuse a thread that might be wedged in a blocking native call) — this needs real design work, not just "add tests."
- Resolve, via an actual live spike (not reasoning alone): whether the confirmed container HWND is sufficient as the UIA root or whether a further scoped child-window step is needed; whether Chromium's on-demand accessibility activation actually works reliably against real WebView2 content from a host application (not just a screen reader); whether `role`/`accessible_name` actually resolve to correct, stable UIA `ControlType`/`Name` values against representative real DOM content.
- Explicitly disclose cross-origin iframe blind spots and known WebView2 UIA reading bugs as a typed limitation surfaced to any future caller of `.read`/`.extract`, not a silent gap.

This is materially more evidence than existed before this investigation (D44 in the decision log) — the next attempt starts from confirmed primitives and three named, specific defects to fix, not from an open question.

## 6. `kortex.browser.download` — deliberately untouched, full reasoning

B4's own `DownloadStarting` handler unconditionally cancels every download attempt (`browser_runtime.rs`, `DenyReason::NotYetSupported`), with no confirmation/destination UI. A real, secure `browser.download` requires a genuinely new subsystem — evaluated item by item against what this codebase actually has today, rather than dismissed in one line:

| Requirement | What exists today | Gap |
|---|---|---|
| Approved destination directory (tenant + profile scoped) | `BrowserProfileStore` manages only the WebView2-internal `webview2-data/` directory — never meant to receive arbitrary downloaded files (mixing them risks confusing WebView2's own internals) | A new, dedicated downloads-root concept, with its own provisioning/lifecycle/ACL model, does not exist |
| Canonical path validation + reparse-point/junction defense | Nothing — confirmed zero existing "safe path join"/canonicalization helper anywhere in `backend/src` (checked directly this session) | Must be built from scratch, including a post-creation re-check (a junction could be introduced between the containment check and the write) |
| Filename normalization | Nothing Browser-specific | Must reject path separators, `..`, reserved Windows device names (`CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9` — a real, classic Windows vulnerability class), leading/trailing dots/spaces (Windows silently reinterprets these), and bound length/Unicode-homograph risk |
| Allowed/blocked file types | Nothing | A genuine policy decision (allowlist vs. denylist of dangerous extensions) requiring product/security sign-off, not something to invent unilaterally mid-implementation |
| Size limits / disk exhaustion | Nothing — disk-quota protection for Browser profiles was already an unresolved open item from B3 (OD-B10) | `Content-Length` cannot be trusted alone; needs a running byte-count cap during streaming plus a per-tenant/profile quota check, neither of which exists |
| Cancellation | Nothing | WebView2's `DownloadOperation` likely exposes `Cancel()`, but there is no existing Grant-shape for a long-running, cancellable operation — the Grant model today is a one-shot mint-verify-redeem artifact, not a job abstraction |
| Atomic finalization / partial-download cleanup / crash recovery | Nothing | Standard safe pattern (temp file in the same volume, atomic rename on success, startup sweep or transactional ledger for orphaned partial files) is unbuilt |
| Concurrent downloads | Nothing | Needs per-download (not just per-surface) identity and state tracking — `SurfaceRedeemLocks` is surface-scoped, not download-scoped |
| Audit | Partial precedent (this cycle's `BrowserExecutionAuditEvent` pattern is reusable) | Needs additional fields (destination — redacted to a safe token, never a full path; size; MIME/extension classification outcome) |

Building a partial version of this (e.g. skipping crash-recovery or quota enforcement "for now") would be exactly the "unsafe partial implementation" this project's own instructions forbid — "a secure NotYetEnabled state is preferable to an insecure 'working' download." Left disabled, exactly as B4/B5.0-B5.4 shipped it. Status: REASONED ONLY — this analysis was not implemented or tested, since nothing was built.

---

## 7. Known limitations (this milestone)

1. **DNS rebinding remains unresolved** — B4's `classify_host` classifies the literal host string, never resolves DNS; `NavigationStarting` has no `GetDeferral` support in the pinned WebView2 SDK, making an async DNS pre-check architecturally impossible at this enforcement point. Unchanged by this milestone; tracked for the dedicated Red Team phase.
2. **Human-vs-AI navigation waiter cross-talk** (§4) — narrow, disclosed, not a security-boundary break.
3. **Redirect-through-the-AI-path is verified by design/code inspection, not by a live redirect-specific test** — `NavigationStarting` fires again for a redirect target regardless of who issued the original navigation, and this milestone's waiter design only ever resolves on `Deny`/`Completed` (never on `Allow`), so a multi-hop redirect chain falls through correctly to whichever event actually resolves it — reasoned through carefully, but no live test drove an actual redirecting endpoint. Labeled UNVERIFIED-by-live-test, not silently assumed.
4. **Destroying a surface mid-navigate-and-wait was not live-tested.** Reasoned to degrade safely to a bounded `Timeout` (the waiter's `Arc` clone inside the WebView2 event closures likely outlives the `HashMap` entry removal, but if closed COM handlers stop firing entirely, the caller times out rather than hanging forever or erroring insecurely) — not proven live.
5. **Screenshot is viewport-only.** WebView2's `CapturePreview` has no native full-scrollable-page capture in the pinned SDK; `full_page: true` is refused explicitly (`FullPageNotYetSupported`), never silently served as a cropped image.
6. `.read`/`.extract`/`.click`/`.type`/`.download` execution — see §5/§6.

---

## 8. Documentation map

- This document: architecture, decisions, capability status, known limitations.
- `browser_b5_implementation_report.md`: evidence — tests, live verification, adversarial findings, git/CI status, final verdict.
- `browser_architecture.md` / `browser_capability_model.md` / `browser_decision_log.md` / `browser_known_limitations.md` / `browser_roadmap_b0_b10.md`: updated in place to reflect the above; no new `B5.x`-numbered files.
