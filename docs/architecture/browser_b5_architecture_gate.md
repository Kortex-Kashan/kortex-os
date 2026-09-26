# KORTEX OS — Browser B5 Architecture Gate
## Browser Capability Layer

**Status**: ARCHITECTURE GATE ONLY. No source code changed. No commit. No push. B3/B4 untouched.
**Baseline**: `main` @ `10f1b4ccf2488b64aadbc2efc632dcd82bf9698a` (B4 CLOSED).
**Author stance**: findings are evidence-first. Every non-obvious claim is cited to a file:line. Where the repository has no answer, this document says so and marks it an open decision — it does not invent one.

---

## 1. Executive Summary

B5's job is to expose a small, typed, auditable set of browser capabilities (`browser.navigate/.read/.click/.type/.extract/.download/.screenshot`) that AI orchestration can invoke, without ever handing an AI agent — or a webpage — direct authority over WebView2, arbitrary JavaScript, arbitrary Tauri commands, or the filesystem.

The repository already has almost everything B5 needs on the **authorization/governance side**: a capability registry (`RegistryEngine`), a single dispatch chokepoint (`CapabilityDispatcher`), a binary mutation-gated approval chain (`ToolGovernanceEvaluator` → `DurableAIApprovalPolicy` → `AIOrchestrationEngine._on_approval_decided`), tamper-evident approval (Ed25519-signed decisions + `action_fingerprint` re-verification), per-tenant concurrency throttling, and an audit trail. B5 should **reuse this framework, not fork it** — `docs/architecture/browser_capability_model.md` (established B0) already sketched this correctly and this gate confirms it against source.

What the repository does **not** have, and what B0's own sketch glossed over with "call into it directly," is a way for a capability handler running in the **backend Python process** to reach a WebView2 surface living in the **desktop Tauri process** and get a result back. Two existing patterns look superficially similar and are both wrong fits: the event relay (`events.rs`) is one-way, notification-only; the Phase 5/6 Desktop Agent Gateway is a bidirectional command/response channel but built for a *different, remote, mTLS-authenticated managed-endpoint agent* — not the interactive app the user is sitting at. **This is the one genuinely new abstraction B5 must design.** §6 proposes a specific shape for it that reuses existing primitives (Ed25519 signing, `action_fingerprint`, the existing loopback `invoke_capability` transport) rather than standing up a second gRPC/mTLS layer.

**Verdict: READY WITH CONDITIONS.** See §30.

---

## 2. Current Repository Baseline

- Browser milestones B0–B4 complete and pushed. B4 commit `10f1b4c`, closed per its own gate.
- B4 established: `BrowserPolicyEngine` (`apps/desktop/src-tauri/src/browser_policy.rs`) — local, synchronous, zero-I/O navigation/popup/download/permission policy, enforced directly inside WebView2 COM event handlers in `browser_runtime.rs`. No backend round-trip exists in this path today, and **cannot** for `NavigationStarting` specifically (no `GetDeferral` — D28).
- `BrowserProfileStore` (B3) owns tenant-scoped, persistent WebView2 profile directories; `browser_runtime.rs`'s `WebView2RuntimeAdapter` remains profile-agnostic by design.
- Desktop↔backend transport today: `apps/desktop/src-tauri/src/ipc.rs`'s `invoke_capability` Tauri command → `POST {base_url}/capabilities/invoke` (loopback HTTP, Bearer session token) → `CapabilityDispatcher.dispatch()`. One-way notification channel exists separately: `events.rs`'s `WS /events/stream` → Tauri event `kortex://event` (backend push, no response path).
- Every existing Browser Tauri command (`browser_create_surface`, `browser_navigate`, `browser_reload`, `browser_go_back/forward`, `browser_set_bounds`, `browser_query_state`, `browser_destroy`) is **direct frontend↔Rust IPC with no backend hop at all** — confirmed in B1 and never changed since (`browser_b1_preflight_report.md` §7, reconfirmed in `browser_runtime.rs`'s own module doc, first paragraph).

---

## 3. Existing Capability/Action Architecture (verified this session)

### 3.1 The one real dispatch chokepoint

`CapabilityDispatcher.dispatch(request: CapabilityRequest) -> Any` — `backend/src/kortex/core/dispatch.py:301,352`.

- `CapabilityRequest` (dispatch.py:127-141): `request_id`, `correlation_id`, `idempotency_key: str | None`, `capability_name`, `session_token: TokenPayload | None`, `parameters: dict`, `context: dict`.
- `CapabilityExecutionContext` (dispatch.py:246-279) — **frozen**, the only channel a handler may read identity from. `tenant_id` is contractually `principal.tenant_id` — **except** in the narrow bootstrap-exempt (`requires_authentication=False`) branch, where it falls back to caller-supplied `context["resource_tenant_id"]` (default `"default"`, dispatch.py:405-408). Every Browser capability must be registered `requires_authentication=True`, closing this exception for B5's own surface.
- Sequence (dispatch.py:364-540): resolve descriptor → authenticate (`verify_token`, signature + fresh DB re-check + expiry, `auth.py:1193`) → authorize (`SecurityEngine.authorize()`, built from the **descriptor's own metadata only, never caller data**) → bind tenant → build frozen context → optional idempotency claim (only if `idempotency_key` is supplied — **AI tool calls do not supply one today**, see §16) → invoke handler → audit success/failure (best-effort, never blocks the security decision).

### 3.2 Capability registry / metadata

`RegistryEngine.register_capability(...)` (`backend/src/kortex/engines/registry/engine.py:634`) is the single registration chokepoint. `CapabilityDescriptor` (engine.py:327-424) fields relevant to B5: `required_permissions`, `requires_authentication`, `security_classification` (`"INTERNAL"` default), `requires_execution_context`, `is_read_only` (fail-closed default `False`), `is_idempotent`, `owner_id`.

### 3.3 Authorization: RBAC then ABAC, hard short-circuit, AND not OR

`SecurityEngine.authorize()` (`engine.py:1017`) delegates to `AuthorizationEngine.authorize()` (`authorization.py:104-123`): RBAC evaluated first; a deny short-circuits and **ABAC is never reached**. `ABACEvaluator` (`abac.py:88`) evaluates exactly two attributes — tenant match and clearance-vs-`security_classification` rank — and nothing else (no `time_of_day`, no `resource_ownership`; both explicitly out of scope per its own module docstring). **There is no "action sensitivity" (mutating/irreversible/read-only) concept anywhere inside RBAC or ABAC** — that lives one layer up, in the tool/registry metadata (§3.4), never consulted by the authorize() call itself.

### 3.4 The sensitivity flag that actually gates approval

`ToolDefinition.is_mutation: bool = False` (`ai/tools.py:229-249`) — a **single boolean**, not a graded taxonomy. `ToolGovernanceEvaluator.evaluate_tool_calls()` (`ai/governance.py:275-321`) reads it: mutation + `require_human_approval_for_mutations` (policy default `True`) ⇒ `requires_approval=True`. **Unknown tool ⇒ fail-closed, treated as mutating** (governance.py:315-318). `CapabilityDescriptor.is_read_only` and `ToolDefinition.is_mutation` are two *different* fields on two *different* models, linked only through the opt-in bridge `generate_tool_definition_from_capability()` (`api/capability_tool_bridge.py:63-86`, `is_mutation = not is_read_only`) — `browser_capability_model.md` §2 already correctly identifies this as the path Browser should use.

### 3.5 Approval: tamper-evident, TOCTOU-checked, but with one real gap

`DurableAIApprovalPolicy.requires_approval()` (`governance.py:354-438`) computes `action_fingerprint = sha256(json({tool, scrub_secrets_from_text(args)} for each call))` (governance.py:393-405) and persists it on the `ApprovalRequest` (`workflow/models.py:583-589`). `DurableApprovalManager.submit_decision()` (`workflow/approval.py:503-683`) verifies the human's Ed25519 signature against the **principal's own registered public key** (never a client-supplied one), rejects self-approval, and rejects role mismatch. `AIOrchestrationEngine._on_approval_decided()` (`ai/engine.py:2410-2520`) **re-computes the fingerprint and refuses to resume on mismatch** — the exact "approve-one/execute-another" defense. **Gap, stated plainly**: the mismatch check is skipped entirely if `stored_fingerprint` is falsy (`engine.py:2469-2480`, `if stored_fingerprint and ... !=`) — a ticket created without a fingerprint resumes unchecked. Browser capability approval requests must always populate `action_fingerprint`; this gate flags it, it does not fix a pre-existing cross-cutting gap.

Second, independent replay defense: `ResumeToken` (`agent.py:130-151,594-700`), HMAC-SHA256 over `task_id:step_count:pending_call_hash:issued_at:expires_at`, verified unconditionally before resume. **Caveat carried into this document rather than silently dropped**: the default signing secret is `secrets.token_bytes(32)` generated once at module import (`agent.py:591`) unless a persisted secret is injected by bootstrap — not independently confirmed in this pass. Not a B5 blocker (pre-existing, cross-cutting), but relevant if Browser approval tickets are expected to survive a backend restart.

### 3.6 Concurrency: per-tenant only, fail-fast

`TenantConcurrencyThrottler` (`ai/throttling.py:22-99`) — two counters keyed by `tenant_id` only (generation slots, cap 10; agent slots, cap 5), **fails fast, never queues**. No per-surface, per-profile, or per-session concept exists here — Browser must add its own serialization for those (§17, §23).

### 3.7 Idempotency: real, but unused by the AI path

`core/idempotency.py`'s `IdempotencyStore` is wired into `CapabilityDispatcher` but **gated strictly on `CapabilityRequest.idempotency_key` being present** (dispatch.py:424-428). Confirmed: `KernelBridgeAdapter.invoke_capability` (`ai/bridge.py:178-184`), the sole production translator from an AI tool call to a `CapabilityRequest`, **never populates it**, and `ToolCall.call_id` is never threaded into it anywhere. **A duplicated/retried `browser.click` ToolCall has no dispatcher-level idempotency protection today.** This is a real, pre-existing, cross-cutting gap — not something B5 can fix by itself without touching `KernelBridgeAdapter`, but Browser's own capability contracts should be designed to be **safe under at-least-once delivery** regardless (idempotent where the action allows it, e.g. `browser.read`; explicitly non-idempotent and flagged as such where it doesn't, e.g. `browser.click`/`browser.type`).

### 3.8 Prompt-injection / secret-scrubbing machinery that already exists

- `scrub_secrets_from_text()` (`ai/tools.py:71-102`) — regex-based, redacts `api_key|token|password|secret|bearer|credential|authorization`-shaped dict keys and bearer/`sk-`/`pk-`-shaped values.
- `sanitize_context_content()` (`ai/memory.py:45-61`) — neutralizes `[[role]]`-marker forgery in any text re-entering LLM context.
- Output size bounds: 50,000-char / 65,536-byte ceilings on tool results (`ai/tools.py:46-50`).
- `ContentSafetyGuardrail` (`governance.py:222-267`) — prompt-injection **and PII** regex scanner — **confirmed applied to prompts/completions only, never to tool call results.** This is exactly the gap `browser_security_model.md` §11 already named ("the one genuinely new component") — now independently confirmed by direct code search, not merely asserted. `browser.read`/`.extract` output is precisely the "adversarial external content re-entering context" case this scanner was built for but isn't wired to.

### 3.9 The wrong-fit precedent, and why it's the wrong fit

`backend/src/kortex/engines/agent_gateway/` (Phase 5/6): `grpc.aio` mTLS server, `agent.proto`'s `DesktopAgentGateway.Connect` bidirectional stream, narrow typed commands (`desktop_launch/click/type/read_text`), deterministic `UiElementSelector` (fails closed on zero or >1 match), explicit "never log or audit the typed text" convention (`agent.proto`'s `DesktopTypeCommand.text` field comment). **Design philosophy directly reusable for Browser's `click`/`type`/`read` contracts** (§8). **Transport not reusable**: this gateway authenticates a *separate, remotely-installed, certificate-identified machine* (`machine_installation_id`, revocation-on-disable) — the opposite trust model from "the same interactive desktop app the user is already logged into, already connected via loopback HTTP." Standing up a second mTLS session layer to talk to a process already sitting on the other end of an authenticated loopback connection would be pure duplication. `browser_capability_model.md` §7 already reached this same conclusion; this gate independently confirms it from the proto/engine source rather than repeating the claim.

---

## 4. Existing Browser Architecture (recap, grounded in this session's own B1–B4 work)

- `browser_runtime.rs`: `BrowserRuntime` trait (`create_surface/navigate/reload/go_back/go_forward/set_bounds/query_state/destroy/destroy_all`), all keyed by opaque `BrowserSurfaceId` (process-lifetime, monotonic-sequence-suffixed, generated fresh per surface — **never persisted, never known to the backend today**). `WebView2RuntimeAdapter<R>` is the sole implementation; no WebView2/wry type crosses the trait boundary.
- `browser_policy.rs`: pure, synchronous `evaluate_navigation()`; `DenyReason`/`PolicyAction`/`PolicyDeniedEvent`/audit plumbing shared by all four B4 enforcement points.
- `browser_profile_store.rs`: `BrowserProfileId` (128-bit opaque, tenant-scoped), `<app_data_dir>/browser-profiles/<tenant_id>/<profile_id>/...`, PID-liveness lock, `icacls` baseline ACL.
- Frontend: `features/browser/{api.ts, hooks/useBrowserTabs.ts, hooks/useBrowserProfiles.ts, components/*}` — a thin wrapper directly over the Tauri commands above, **deliberately not routed through `invokeCapability()`** (`api.ts`'s own header comment: "there is no backend capability/governance layer for Browser yet — that is Browser-B5's job").
- Audit today: local JSON-Lines file only (`<profiles_root>/audit.log`), shared by both `browser_profile_store.rs` and `browser_policy.rs` — never `UniversalAuditEntry`/`AuditManager` (D23, reaffirmed D-series through B4). **B5 is the first phase where a Browser action's audit trail could plausibly live in the real backend `AuditManager`, since B5 is the first phase where a Browser action is even reachable from the backend at all.**
- **Nothing about BrowserSurfaceId, BrowserProfileId, or a live tab is known to the backend today.** This is not a gap to "fix" so much as a design fact B5 must resolve explicitly (§8).

---

## 5. B5 Architectural Objective

Expose `browser.navigate/.read/.click/.type/.extract/.download/.screenshot` as capability *contracts* — never raw WebView2, never arbitrary script execution, never an unrestricted Tauri command — such that an AI agent (or anything acting on its behalf) can only ever reach a browser surface through: capability authorization (RBAC/ABAC + mutation-gated approval) → **B4's existing, unbypassable local policy** → the existing, narrow `BrowserRuntime` primitives. Not to build a second execution engine; not to weaken B4.

---

## 6. Proposed Browser Capability Architecture

**Decision on the critical question (§5 of the task brief): Option C — a thin Browser Capability Layer registering typed capabilities into the existing central `RegistryEngine`/`CapabilityDispatcher`/`ToolRegistry`.** Rejected: (A) is what this already is once registered — there's no separate "extend" step; (B) a competing dispatcher would duplicate RBAC/ABAC/approval/audit for zero benefit and is explicitly what `browser_capability_model.md` §7 and this gate's own §3.9 finding argue against.

**The one new abstraction required — a Capability Execution Grant, not a new transport:**

The backend-side capability *handler* for a mutating Browser action (`navigate`/`click`/`type`/`download`) does **not** execute the action. It cannot — the WebView2 surface is a live object in a different OS process it has no channel to command synchronously. Instead:

1. The handler runs exactly what every other governed capability already runs: RBAC/ABAC (`SecurityEngine.authorize`) + mutation-gated approval (`ToolGovernanceEvaluator`/`DurableAIApprovalPolicy`) + audit. It never touches WebView2.
2. On approval, the handler mints a **short-lived, Ed25519-signed Capability Execution Grant**: `{grant_id, tenant_id, principal_id, capability_name, surface_id, canonicalized_parameters_hash, issued_at, expires_at (short — seconds, not the 24h default approval timeout), signature}`. This is structurally the same primitive as `ResumeToken`/`action_fingerprint` (§3.5) applied to a new purpose — reusing proven code shape, not inventing new cryptography.
3. The **existing** `invoke_capability` HTTP response (already the transport carrying every capability's result back to the desktop, `ipc.rs:327-404`) carries the grant back to the desktop process that originated the request.
4. The desktop frontend redeems the grant via a **new, narrowly-typed Tauri command** (e.g. `browser_execute_granted_action`) that: (a) verifies the grant's signature and expiry locally (defense in depth — never trust "it came over HTTPS" alone), (b) resolves `surface_id` to a real, still-open `BrowserSurfaceId` (fails closed if gone — §17), (c) invokes the **existing** `BrowserRuntime` primitive (`navigate`/etc.) — which still runs through **B4's unchanged, unbypassable local `BrowserPolicyEngine`**, because that check lives inside the WebView2 COM callback itself, not in anything this new command could skip.
5. The desktop reports completion back via a **second, ordinary capability call** (e.g. `kortex.browser.report_execution`, itself authenticated/authorized/audited identically to any other capability) carrying the grant_id and outcome, which the original AI tool call is `await`-ing (via the existing `AIToolInvoker`/`IToolExecutionPort` polling or long-poll pattern — exact mechanics are an implementation detail for B5.4, not an architecture decision).

Why this shape and not a bidirectional gRPC channel: it reuses the transport that already exists in exactly the direction it already flows (desktop → backend, request/response), reuses signing/fingerprinting patterns already proven in production (`DurableApprovalManager`, `_action_fingerprint`), and — critically — **never asks anything except B4's own existing, already-audited WebView2 event handlers to actually touch WebView2.** The backend never "reaches into" the desktop process; the desktop process, which was going to make an HTTP call anyway, simply carries a signed instruction back with it.

**Read-only capabilities** (`browser.read/.extract/.screenshot`) don't need the two-step grant/report dance if no approval is required (`is_read_only=true` per `browser_capability_model.md` §3) — but they still can't execute backend-side, so the SAME plumbing (grant → redeem → report) is used uniformly for all seven capabilities; only the approval step in front of it differs. One mechanism, not two.

---

## 7. Capability Contracts

Legend: **[EXISTING]** = repository fact. **[DECISION]** = this gate's recommendation. **[OPEN]** = requires user decision (§29).

### `browser.navigate`
- Purpose: load a URL in a specific surface. Caller: AI orchestration only (human navigation stays on the existing direct `browser_navigate` Tauri command — §25).
- Input: `{surface_id, url, timeout_ms?}`. Output: `{final_url, http_status?, loading: bool}` or a typed error.
- Identity/tenant/profile: from `CapabilityExecutionContext` **[EXISTING mechanism]**; `surface_id` must resolve to a surface bound to that tenant/profile (§17 — new binding table required, **[DECISION]**).
- Authorization: RBAC + ABAC (`security_classification="CONFIDENTIAL"`, matching Desktop Automation's own precedent, `desktop_automation/engine.py:126`) **[DECISION, precedented]**.
- Browser policy: **B4's `BrowserPolicyEngine` always executes, unconditionally, with zero exception for AI-originated calls** **[DECISION — hard requirement, not negotiable]**. A private-network/denied-scheme target is denied identically whether a human or the AI asked for it.
- Sensitivity: `is_read_only=false` (`browser_capability_model.md` §3) → `is_mutation=true` → approval-gated by default policy.
- Failure modes: `SurfaceNotFound`, `PolicyDenied{reason}` (mirrors B4's `DenyReason`, never carries the URI past what B4 already discloses), `Timeout`, `Malformed`. Never leaks a raw COM error.
- Timeout/cancellation: bounded wait for `NavigationCompleted` or timeout; a timed-out navigate does not retry automatically (§22).
- Autonomous execution: **NO by default** (mutating). **[OPEN]**: should some navigations (e.g. to a small, operator-curated allowlist the tenant has separately approved) be exempt from per-call approval? Existing framework has no per-domain autonomy tier — would require new policy, not just a flag (§19).

### `browser.read`
- Purpose: return the currently-loaded page's rendered text/title/URL — **never** arbitrary script execution to obtain it (§11).
- Input: `{surface_id}`. Output: `{url, title, text: string (bounded), truncated: bool}`.
- `is_read_only=true` → autonomous-eligible by default policy, but still authorized/audited.
- Content **must** pass `scrub_secrets_from_text` + `sanitize_context_content` (existing) **and** a new pass through `ContentSafetyGuardrail`-equivalent injection scanning **[DECISION — closes the §3.8 gap, this is the "one genuinely new component," per `browser_security_model.md` §11 and this gate's independent confirmation]**.
- Failure modes: `StaleSurface` (navigated away / destroyed mid-read — §17), `SurfaceNotFound`, `ContentUnavailable`.

### `browser.click`
- Purpose: click one, unambiguous, deterministically-targeted element.
- Targeting mechanism **[DECISION, precedented by `agent.proto`'s `UiElementSelector`]**: an accessibility-tree-derived selector (role + accessible name + a stable node reference from the immediately-preceding `browser.read`/`.extract` call), **never raw pixel coordinates** — coordinate clicking cannot distinguish a real button from an overlay/clickjacking surface, exactly the risk the brief calls out. Fails closed on zero or >1 match, mirroring `UiElementSelector`'s own documented contract (`agent.proto` header comment) — never guesses.
- `is_read_only=false` → approval-gated by default.
- Sensitive-action escalation **[OPEN]**: should a click matched against text like "Delete account"/"Confirm payment"/"Sign out everywhere" require a *distinct, stronger* confirmation than an ordinary mutating click? The existing framework has no semantic-content-aware approval tier (§19) — flagged, not decided here.
- Failure modes: `TargetNotFound`, `TargetAmbiguous`, `StaleReference` (the read that produced the selector is no longer valid — §17), `PolicyDenied` (if the click would itself trigger a denied navigation/popup, B4 still catches it downstream, unconditionally).

### `browser.type`
- Purpose: enter text into a focused/targeted input.
- **Sensitive-input policy [DECISION, hard requirement]**: input value is scanned with the **same detection philosophy already used for output redaction** (`_SECRET_KEYS_PATTERN`/`_SECRET_VALUE_PATTERN`-equivalent, applied at the *input* boundary instead of output) — a value shaped like a password, API key, OTP, or payment/recovery-code is **refused outright for AI-originated calls**, not merely logged carefully. Ordinary text (search queries, names, addresses, non-secret account identifiers) is permitted. This is the concrete answer to §13: KORTEX must never require or accept a provider password *from the AI* — a human typing their own password directly into a real provider page (B7's domain) is a completely different, human-only flow this capability does not cover and must not be confused with.
- Never logged/audited verbatim (matching `agent.proto`'s own `DesktopTypeCommand.text` convention) — audit records that a type occurred and its *category* (ordinary/refused-as-secret), never the value.
- `is_read_only=false` → approval-gated.
- Failure modes: `TargetNotFound/Ambiguous`, `RefusedSensitiveInput` (new, distinct from `PolicyDenied` — this is a content-shape refusal, not a destination-policy refusal), `StaleReference`.

### `browser.extract`
- Purpose: structured extraction (typed fields from a page), not a script-execution primitive.
- Input: `{surface_id, schema: {field: selector}}` (accessibility-tree/DOM-derived selectors, same targeting model as `.click`, never raw script). Output: typed key/value object, `provenance: {url, extracted_at}`, `truncated: bool`.
- `is_read_only=true`. Same output-sanitization requirement as `.read` (§3.8 gap, closed identically).
- Failure modes: `StaleReference`, `SchemaMismatch` (selector matched nothing / matched an unexpected shape), `ContentUnavailable`.

### `browser.download`
- **B4 currently denies every download unconditionally (D29). B5 does not implement execution of this capability — it defines the contract only [DECISION, explicit].** The capability is registered (for discovery/documentation — `is_read_only=false`) but its handler returns a typed `NotYetSupported` result identical in spirit to `DenyReason::NotYetSupported` (B4, D31) — **never silently bypasses B4's own deny-all**. Real implementation (destination control, filename sanitization, size limits, human confirmation UX) is explicitly OD-B14's job, a later milestone, per the existing decision log. This gate only specifies the shape it will eventually need: explicit destination (never AI-chosen path — a KORTEX-managed download directory, tenant/profile-scoped), content-type/size limits, mandatory human confirmation (never autonomous, ever — irreversible + can deliver malicious content), audit of hash+size, never audit of raw filename if it could carry sensitive data from the page.

### `browser.screenshot`
- Purpose: capture pixels — viewport by default; full-page only if explicitly requested and separately size-capped.
- Treat output as **potentially as sensitive as any other page content** — same secret/PII exposure risk as `.read`, arguably higher (visual credentials, financial dashboards). `is_read_only=true` for authorization purposes, but **[DECISION]** route the resulting image through a *storage* path (a KORTEX-managed, tenant-scoped location with the same audit/retention discipline as any other sensitive artifact), never inline into LLM context as raw bytes, and never to the OS clipboard on the AI's behalf (clipboard is a cross-application escape hatch B4/B5 have no visibility into once written).
- Failure modes: `StaleSurface`, `CaptureFailed`, `SizeLimitExceeded`.

---

## 8. Identity / Tenant / Profile / Surface Binding

Required identity tuple for every capability call, **all six** components, none implicit:

`tenant_id` (from verified principal, §3.1) → `principal_id` (from verified principal) → `browser_profile_id` (must belong to `tenant_id` — existing `BrowserProfileStore` check) → `surface_id` (must be currently open, bound to that profile) → **[NEW] a monotonic "navigation generation" counter per surface** (so a stale `browser.click` referencing a pre-navigation page state fails closed rather than clicking whatever loaded after — no existing concept of this in `browser_runtime.rs` today; `BrowserSurfaceState` has no generation counter, **[DECISION — new, small addition to `BrowserSurfaceState`]**).

TOCTOU scenarios and required behavior (all fail-closed, per the brief's own instruction and B4's existing posture):
| Race | Required outcome |
|---|---|
| Tab/surface closes during request | `SurfaceNotFound` — the new command must re-resolve `surface_id` against the live registry at execution time, never trust a cached handle from grant-mint time |
| Profile closes during request | Same as above — `BrowserProfileStore`'s existing lock/lifecycle already makes this observable |
| Navigation changes during request | Generation-counter mismatch → `StaleReference`, never silently operate on the new page |
| User switches profile in the UI | Irrelevant to a request already bound to a specific `surface_id`/profile at grant-mint time — the grant is scoped to that pair, not "whatever is active" |
| Grant arrives late (past `expires_at`) | Reject before touching the surface at all |
| Browser/app process restarts | All surfaces gone by construction (`BrowserSurfaceId` is process-lifetime only) — any outstanding grant is unredeemable, fails closed automatically |

---

## 9. Authorization Model

RBAC + ABAC, unchanged, exactly as it exists (§3.3) — **no isolated Browser-specific authorization mechanism**, per the brief's own instruction against that. `security_classification="CONFIDENTIAL"` for every `kortex.browser.*` capability (precedented, §3.9/Desktop Automation). `requires_execution_context=True` for all seven (page-originated calls must never bypass the dispatcher-constructed context). `required_permissions`: new, Browser-specific RBAC permission strings (e.g. `browser.navigate`, `browser.type`) — **[OPEN]** exact naming/role-assignment is a policy decision for the user/owner, not something this gate should invent.

---

## 10. Browser Policy Integration

**Invariant, stated once and binding everywhere in this document: `BrowserPolicyEngine` is not a step B5 calls — it is a boundary B5 cannot get past.** Every capability that results in a real `navigate`/`click`/`type`/`download`/permission-adjacent action ends up calling the *same* `BrowserRuntime` trait methods a human click already calls, which fire the *same* WebView2 COM events `browser_runtime.rs` already wires to `browser_policy.rs`. There is no code path in this design where a capability grant, however validly signed and approved, can cause a WebView2 event handler to skip its policy check — the check isn't parameterized by caller identity at all (B4 built it that way deliberately). This is the strongest structural guarantee this document can make, and it costs nothing new to keep, because B5 was designed (§6) specifically to route back through the *existing* runtime rather than a parallel one.

---

## 11. Trust Boundary

```
AI / AI Orchestration (backend, Python)
        |  ToolCall
        v
ToolGovernanceEvaluator  (mutation check, allow/blocklist)   [EXISTING]
        |
        v
DurableAIApprovalPolicy  (action_fingerprint, human approval)[EXISTING]
        |
        v
CapabilityDispatcher.dispatch()  (RBAC -> ABAC, audit)        [EXISTING]
        |
        v
Browser capability handler (backend) -- mints signed grant, NEVER touches WebView2   [NEW]
        |  (carried back over the existing invoke_capability response)
        v
Desktop: new narrow Tauri command -- verifies grant, resolves live surface_id  [NEW]
        |
        v
BrowserRuntime (existing trait: navigate/click(as-navigate-trigger)/etc.)     [EXISTING]
        |
        v
WebView2 COM event (NavigationStarting/NewWindowRequested/DownloadStarting/PermissionRequested)
        |
        v
BrowserPolicyEngine  (LOCAL, SYNCHRONOUS, UNBYPASSABLE)       [EXISTING, B4]
        |
        v
WebView2 / Web (untrusted)
```

Explicitly prevented, and how:
| AI cannot reach... | Because |
|---|---|
| raw WebView2 COM | `browser_runtime.rs`'s own module doc: no type crosses the `BrowserRuntime` boundary; the new Tauri command in §6 calls the same trait, nothing lower |
| arbitrary JavaScript | No capability contract in §7 has a "run this script" shape; `.click`/`.type`/`.extract` all use structured, deterministic targeting, never script injection |
| arbitrary native IPC | New Tauri command is one narrow, typed command, capability-scoped like every existing one (`capabilities/browser.json` convention) |
| browser profile filesystem / cookie DB | `BrowserProfileStore` is never exposed to a capability contract; surfaces are addressed only by opaque `surface_id` |
| authentication tokens | `CapabilityExecutionContext.session_token` never crosses into a capability's `parameters`/return value; existing dispatcher discipline |
| arbitrary download path | §7 `browser.download` explicitly has no execution path yet; when it does, destination is KORTEX-managed, never AI-chosen |
| arbitrary native permissions | B4 already denies all of these unconditionally; nothing in B5 changes that |
| policy bypass | §10 |
| tenant/profile crossing | §8's binding table, checked at redeem-time against the live registry, not the grant's own claims alone |

---

## 12. Prompt-Injection Boundary (foundational for B6)

Page content is data. It is never elevated to instruction status merely by entering an AI's context, and B5 must make this true structurally, not just by convention:

- `browser.read`/`.extract` output passes through `scrub_secrets_from_text` + `sanitize_context_content` (existing) **plus** a `ContentSafetyGuardrail`-equivalent scan (new, §3.8/§7) **before** it re-enters any LLM context — closing the one confirmed gap.
- A page saying "ignore previous instructions" / "click this to verify your identity" / "send your API key here" is, mechanically, just text returned by `browser.read`. It cannot invoke `browser.click`/`.type`/`.download` itself — only a real `ToolCall` from the orchestration engine can, and that still goes through governance/approval (§3.4-3.5) regardless of what text preceded it. **The AI model choosing to act on injected text is a model-behavior risk B6 owns (`browser_security_model.md` §11 already scopes this to B6); B5's job is only to ensure the page's text can never manufacture an actual capability invocation on its own** — confirmed true by construction, since nothing in §7's contracts accepts "instructions found in page content" as an input.
- A page attempting to trigger a download or navigate to a private destination hits B4's existing, page-agnostic deny — same outcome whether the trigger was human, AI, or the page's own script.

---

## 13. AI Autonomy / Confirmation Model

**Do not invent a graded taxonomy the codebase doesn't have.** The only real mechanism today is `ToolDefinition.is_mutation: bool` (§3.4) — binary. Recommendation: use it exactly as `browser_capability_model.md` §3 already tabulated (`read/extract/screenshot = false`; `navigate/click/type/download = true`). This gives every mutating Browser capability the existing, proven approval path for free, with zero new governance code.

**[OPEN — explicitly not decided here]**: the brief asks whether finer distinctions (`LOW_RISK_MUTATION` vs `IRREVERSIBLE_ACTION`, e.g. an ordinary `browser.click` vs. one matched against "Delete account") are warranted. The evidence says: the *existing* approval model has no such gradient anywhere in the codebase, for any capability, not just Browser's. Adding one would be a cross-cutting change to `AIGovernancePolicy`/`ToolGovernanceEvaluator` that every other governed tool would also need to migrate through — out of proportion for a Browser-specific gate to decide unilaterally. Flagged for the owner as a real question, not silently resolved either way.

`browser.download`: **never permitted, full stop, for the reasons in §7** — this is not an autonomy-tier question, B4 already denies it categorically.

---

## 14. Audit Model

Every capability invocation reuses the **existing** `CapabilityDispatcher` audit path (`_audit_authentication_success/failure`, `_audit_execution` — dispatch.py:544-702) — this already audits request-received-equivalent (authentication), authorization decision (inside `SecurityEngine.authorize`, `engine.py:1031-1057`), execution start/success/failure, all best-effort/non-blocking. **New audit events specific to Browser**: grant minted, grant redeemed (or rejected — expired/signature-invalid/surface-gone), execution result reported. All reuse `AuditManager.record_event`/`UniversalAuditEntry` (§3, real backend audit — a first for Browser, replacing nothing about B4's own local audit.log, which continues to record the WebView2-level enforcement facts as it does today; the two logs describe different layers and both stay).

**Never logged, anywhere in this chain**: the value typed via `browser.type` (§7), full page content from `.read`/`.extract` beyond what's already scrubbed for the AI's own context (a *separate* redaction pass for audit-context, following `sanitize_for_persistence`'s existing convention, dispatch.py:686/698), raw screenshot bytes (store a reference/hash, not the image, in the audit context), session tokens (existing, unconditional dispatcher discipline).

---

## 15. Error Model

Typed, never a raw COM/WebView2 leak: `Unauthorized`, `Forbidden` (RBAC/ABAC), `PolicyDenied{reason}` (B4's own `DenyReason`, re-exported not re-invented), `RefusedSensitiveInput` (new, §7 `.type`), `SurfaceNotFound`, `ProfileNotFound`, `StaleReference` (new, §8/§17), `GrantExpired`/`GrantInvalid` (new, §6), `NavigationFailed`, `Timeout`, `Cancelled`, `TargetAmbiguous`/`TargetNotFound` (new, §7 `.click`/`.type`), `NotYetSupported` (B4's own convention, reused for `.download`), `InternalError` (catch-all, never a raw exception string).

---

## 16. Timeout / Cancellation Model

Every capability has a bounded timeout (existing precedent: `ToolDefinition.timeout_seconds`, default 30s, range 0.1–300s, `ai/tools.py:55-57` — reuse this exact field, don't invent a Browser-specific one). Grant `expires_at` (§6) is deliberately shorter than the tool timeout (seconds, not the 24h approval window) — a slow-to-redeem grant should expire well before the AI's own call times out, so the failure the AI sees is `GrantExpired`, not a silent hang. Cancellation: the existing `asyncio.wait_for`-based timeout in `AIToolInvoker.invoke_tool` (tools.py, §"execution routing" in agent 2's findings) already cancels the backend-side await; the desktop-side action (if already redeemed and running) is **not** magically interrupted by that alone — a genuinely in-flight WebView2 navigation should still be cancellable via the **existing** `browser_navigate`-to-a-new-URL semantics (already how a human interrupts a slow load today), not a new primitive.

---

## 17. Concurrency Model

Per-surface serialization, **new, required** — nothing in `browser_runtime.rs` today prevents two concurrent `navigate` calls against the same `BrowserSurfaceId` from racing (B2's UI never needed this, since a human can only click one thing at a time). Recommendation: a per-surface `tokio::sync::Mutex` (or equivalent) at the point where the new redeem-command dispatches into `BrowserRuntime`, so:

| Concurrent pair | Rule |
|---|---|
| navigate + click (same surface) | Serialized — click waits for navigate's `NavigationCompleted` or timeout |
| navigate + read | Serialized — a read racing a navigation would read a page mid-transition; must wait |
| type + navigate | Serialized |
| click + close (destroy) | `destroy` wins if it arrives — in-flight click resolves `SurfaceNotFound` |
| download + profile close | Moot until `.download` is actually implemented (§7) |
| screenshot + navigation | Serialized, same reasoning as read |
| different tabs/profiles/tenants | Fully concurrent — no cross-surface lock needed |

This is additive to B4's own concurrency posture (B4 never needed this — its handlers are per-event, not per-multi-step-capability).

---

## 18. WebView2 / Native Boundary

Unchanged from B1–B4, restated as an explicit B5 invariant: only `browser_runtime.rs` may reference `webview2_com`/raw COM types. The new redeem-command (§6) calls `BrowserRuntime` trait methods exactly like every existing Tauri command does — it gains no new access. No HWND, COM pointer, or unrestricted script-execution primitive is ever handed to anything above the `BrowserRuntime` trait boundary, including the new command itself.

---

## 19. Frontend Boundary

**Human UI actions and AI capability actions must not share a code path**, per the brief's own explicit requirement. Today: human actions go through `features/browser/api.ts`'s direct Tauri calls (`navigateBrowserSurface`, etc.) — unauthenticated-by-design at the Tauri layer (Tauri's own capability ACL is the only gate, per `browser_architecture.md` §2.3's D10 finding), because the human is already sitting at the machine. **AI capability actions must go through the new grant/redeem command (§6), never through `api.ts`'s existing exports** — two distinct Tauri commands, two distinct capability-file scopes, even though they both ultimately call the same `BrowserRuntime` methods underneath. This is a **new, second Tauri command**, not a repurposing of the existing human-facing ones — required to satisfy "Human browser controls must not accidentally become an unrestricted AI capability API," and to keep the new command's own capability-file grant narrow and separately auditable.

Transport chosen: **AI Backend → (existing `invoke_capability`/HTTP, in the redeem-command direction) → Desktop Rust**, not Frontend→Backend→Rust (there is no "frontend" participant in an AI-originated action at all — no human is clicking anything) and not a new AI Backend→Desktop Agent gRPC hop (§3.9/§6). No redundant IPC hop is introduced; the existing loopback HTTP connection is reused in the direction it already exists.

---

## 20. Capability Discovery

Reuse `CapabilityProjection`/`project_tools_for_tenant()` (`core/projection.py`) exactly as every other capability domain does — no bespoke Browser visibility mechanism (`browser_capability_model.md` §6, independently plausible from `RegistryEngine`'s own metadata-driven design, §3.2). Metadata surfaced: name, description, `parameters_schema`/`returns_schema` (already first-class fields on `CapabilityDescriptor`), `is_read_only`/`is_mutation` (derived), `security_classification`. The desktop's `CapabilityPalette.tsx` hardcoded allowlist (`browser_capability_model.md` §6 cites `CapabilityPalette.tsx:13-17`) needs the seven new names added — a one-line addition, explicitly B8's concern per that doc, not B5's.

---

## 21. Threat Model

| # | Threat | Attack path | Boundary | Existing mitigation | B5 mitigation | Residual risk | Test requirement |
|---|---|---|---|---|---|---|---|
| 1 | AI bypasses capability layer | Model attempts a raw WebView2/script call | §18 | N/A (no such primitive exists) | Contracts in §7 expose no such primitive | None identified | Negative test: no tool schema accepts a script/selector-free free-form command |
| 2 | Webpage injects instructions | Hidden text in `.read` output | §12 | `scrub_secrets_from_text`, `sanitize_context_content` | New injection-pattern scan on tool *results* (closes §3.8 gap) | Model may still choose to "obey" visible injected text — a B6 concern, not preventable at B5 | Prompt-injection corpus test against `.read`/`.extract` output |
| 3 | Wrong-tenant execution | Grant/redeem swapped across tenants | §8/§9 | Frozen `CapabilityExecutionContext.tenant_id` | Redeem-time re-check against live surface/profile tenant, not grant claim alone | Low | Cross-tenant redeem attempt must fail closed |
| 4 | Wrong-profile execution | Grant references a surface from a different profile | §8 | `BrowserProfileStore` isolation | Explicit profile_id in binding tuple, re-verified at redeem | Low | Cross-profile redeem attempt must fail |
| 5 | Wrong-surface execution | Ambiguous/guessed `surface_id` | §8 | `BrowserSurfaceId` is a 96+-bit-entropy opaque string, not guessable | Explicit resolution against live registry, not cache | Low | Unknown surface_id → `SurfaceNotFound` |
| 6 | Stale tab reference | Tab closed between grant and redeem | §8/§17 | None (new scenario) | Re-resolve at redeem-time | None if implemented correctly | Close-then-redeem race test |
| 7 | Stale profile reference | Profile closed mid-flight | §8 | `BrowserProfileStore` lock/lifecycle | Redeem-time re-check | None | Same |
| 8 | Navigation race | `.click` targets pre-navigation DOM | §8 | None (new scenario) | Generation counter (new) | Low, if counter is correctly bumped on every `NavigationStarting` allow | Navigate-then-click-stale-selector test |
| 9 | Malicious URL | AI/page navigates to `file:`/private IP | §10 | B4 `BrowserPolicyEngine` (unconditional) | None needed — already covers AI-originated calls identically | OD-B16 (DNS rebinding) — pre-existing, disclosed | Already covered, B4's own 35 tests |
| 10 | Private-network access | Same as #9 | §10 | Same | Same | Same | Same |
| 11 | localhost access | Same, incl. trailing-dot bypass | §10 | B4 D33 fix | Same | None new | Already covered |
| 12 | DNS rebinding | Public hostname resolving to private IP at connect time | §10 | None (OD-B16, disclosed) | Not solved by B5 — architecturally out of B4's synchronous contract | Real, disclosed, unchanged by B5 | No new test — inherited limitation |
| 13 | Malicious redirect | Page redirects itself post-load | §10 | B4 evaluates every `NavigationStarting`, including redirects, identically | None needed | None new | Already covered |
| 14 | Clickjacking | Overlay disguises real target | §7 `.click` | None today (no click capability exists yet) | Accessibility-tree targeting, not coordinates; fail-closed on ambiguity | An overlay with a *matching* accessible name to the real target is not caught by name-matching alone | Adversarial overlay test |
| 15 | Hidden element interaction | `.click`/`.type` targets a display:none element | §7 | None today | Selector resolution should require the target to be visible/interactable, not merely present in the tree — **[DECISION, add to §7 `.click`/`.type` contract]** | Low if enforced | Hidden-element-target test must fail |
| 16 | Credential exfiltration via `.type` | AI asked to type a password into a phishing page | §7 `.type` | None today | `RefusedSensitiveInput` (§7) | Detection is pattern-based, not perfect — a password that doesn't match known shapes could slip through | Adversarial secret-shaped-value test suite |
| 17 | Screenshot exfiltration | Sensitive on-screen data captured and returned to AI context | §7 `.screenshot` | None today | Store-not-inline (§7); never to clipboard | Screenshot storage itself becomes a new sensitive-data-at-rest surface | Confirm no raw image bytes ever appear in a `ToolResult`/audit context |
| 18 | Malicious download | `.download` delivers malware | §7 | B4 denies all downloads unconditionally | B5 does not implement execution (§7) | None — deferred, not present | N/A until OD-B14 |
| 19 | Path traversal | AI-chosen download destination | §7 | Same | Same — no AI-chosen path exists in this design | None | N/A until OD-B14 |
| 20 | Filesystem escape | Same family | §7 | Same | Same | None | N/A until OD-B14 |
| 21 | Unauthorized external side effect | Any mutating capability without approval | §13 | `ToolGovernanceEvaluator` fail-closed on unknown tool | Every Browser capability explicitly registered, none left to the "unknown" fail-closed default by omission | None if registration is complete | Registration completeness test (all 7 names resolve) |
| 22 | Capability replay | Same grant redeemed twice | §6 | N/A (new mechanism) | Grant is single-use — redeem must invalidate it atomically (mint→redeem is itself an idempotency claim, reusing `IdempotencyStore`'s pattern even if not its literal code path) **[DECISION]** | Low if single-use enforced atomically | Double-redeem-same-grant test must reject the second |
| 23 | Duplicate action | Retried `ToolCall` for `.click` | §3.7 | None today (idempotency key not populated on AI path, §3.7) | Not fixed by B5 alone (cross-cutting); Browser contracts should tolerate at-least-once delivery per §3.7 | Real, pre-existing, disclosed | Flag for cross-cutting fix, not a B5 blocker |
| 24 | Concurrent conflicting actions | Two capability calls, same surface | §17 | None today | Per-surface serialization (new) | Low if implemented | Concurrent navigate+click test resolves deterministically |
| 25 | Browser crash during operation | Process dies mid-redeem | §8 | Surfaces are process-lifetime by construction | Redeem fails closed (`SurfaceNotFound`/connection error) automatically | None | N/A — falls out of existing design |
| 26 | Profile deletion during operation | Profile deleted concurrently | §8/§17 | `BrowserProfileStore`'s existing lock semantics | Redeem-time re-check | Low | Delete-during-redeem race test |
| 27 | Tenant switching race | N/A on desktop (Browser has no "switch tenant" concept mid-session today) | — | — | — | Low — not a currently-possible UI action | N/A |
| 28 | Audit manipulation | Tampered audit context | §14 | `UniversalAuditEntry` is frozen; persisted via `AuditManager` | No change needed | Pre-existing framework guarantee | Inherited |
| 29 | Sensitive data logging | Secret leaks into audit | §14 | `sanitize_for_persistence` | Extend the same discipline to Browser's new audit events (grant mint/redeem/report) | Low if consistently applied | Redaction test on every new event type |
| 30 | Raw WebView2 escape | New command exposes more than intended | §18 | Trait boundary | New command reviewed to expose nothing beyond existing trait methods | Low | Code review invariant, not a runtime test |
| 31 | Raw JavaScript escape | Any contract secretly allows script | §7 | N/A | No contract accepts a script parameter | None | Schema review |
| 32 | Arbitrary native IPC escape | New command over-scoped in capabilities/*.json | §19 | Tauri ACL (`"webviews": ["main"]` precedent, D10) | New command gets its own narrow capability file scope, never widens an existing grant | Low | Capability-file review, mirroring B1's own D10 finding |
| 33 | Prompt injection via extracted content | Same as #2 | §12 | Same | Same | Same | Same |
| 34 | Model-generated malicious target | AI proposes navigating to a plausible-looking but attacker-controlled URL | §10 | B4 policy is content-agnostic — it doesn't care who proposed the URL | Same, unconditionally | OD-B16 unchanged | Already covered by B4's own suite |
| 35 | Malicious extension/page mechanism | Not applicable — WebView2 profiles here don't load third-party browser extensions | — | — | — | None identified | N/A |

---

## 22. Adversarial Test Matrix

Capability authorization (RBAC deny, ABAC tenant-mismatch, ABAC clearance-insufficient) · tenant isolation (cross-tenant grant redeem) · profile isolation (cross-profile grant redeem) · surface binding (unknown/stale/wrong-tenant surface_id) · policy enforcement (every B4 deny case, re-run against AI-originated calls specifically, not just human ones) · prompt injection (page content containing `[[system]]`-style markers, "ignore previous instructions," fake credential-harvesting prompts — through `.read`/`.extract`) · malformed input (every capability's schema, boundary/empty/oversized values) · URL tests (every B4 bypass class re-run through `.navigate`) · redirect tests (redirect into denied destination mid-navigation) · stale reference tests (surface closed / navigated away between grant and redeem, between read and click) · concurrency tests (§17's table, each pair) · timeout tests (each capability's own timeout boundary) · cancellation tests (AI-side cancel vs. desktop-side in-flight action) · secret-redaction tests (`.type` refusal patterns, audit-context scrubbing for all new event types) · audit tests (every new event type present, every secret absent) · download tests (confirm `.download` never bypasses B4's deny — this is a regression test, not a feature test) · screenshot sensitivity tests (no raw bytes in `ToolResult`/audit, storage path is tenant-scoped) · click targeting tests (hidden element rejected, ambiguous match rejected, overlay-disguised target — best-effort, documented residual risk) · type sensitivity tests (password/OTP/API-key-shaped value refused, ordinary text permitted) · grant tests (expired grant rejected, tampered-signature grant rejected, double-redeem rejected, wrong-surface grant rejected).

Every test category must include a negative case — per the brief's own instruction, the happy path proves nothing alone.

---

## 23. Data Flow

```
1. AI proposes ToolCall(browser.navigate, {surface_id, url})
2. ToolGovernanceEvaluator.evaluate_tool_calls -> requires_approval=True (is_mutation)
3. DurableAIApprovalPolicy.requires_approval -> action_fingerprint computed, ApprovalRequest created
4. Human approves (Ed25519-signed decision)
5. AIOrchestrationEngine._on_approval_decided -> fingerprint re-verified -> resume
6. CapabilityDispatcher.dispatch(kortex.browser.navigate) -> RBAC/ABAC -> handler
7. Handler mints signed Capability Execution Grant, scoped to surface_id/tenant/profile, short expiry
8. Grant returned via existing invoke_capability response to the desktop process that is (in this
   design) itself the caller of a NEW capability the AI's own backend session addresses to a
   specific, already-connected desktop instance -- see OPEN QUESTION in Section 29 on exactly how
   the backend identifies *which* desktop instance's grant this is, since AI orchestration runs
   backend-side and is not itself "the caller" of invoke_capability today.
9. Desktop redeem-command verifies grant, re-resolves surface_id live, serializes against
   any in-flight operation on that surface (Section 17)
10. BrowserRuntime.navigate() -> WebView2 NavigationStarting -> BrowserPolicyEngine (Section 10)
11. Allow -> real navigation; Deny -> PolicyDeniedEvent (existing B4 path, unchanged)
12. Desktop reports outcome via kortex.browser.report_execution (ordinary capability call)
13. CapabilityDispatcher audits execution; AIToolInvoker resolves the original ToolCall's
    ToolResult from the reported outcome
14. ToolResult.to_context_entry() (existing) -> back into LLM context, after Section 12's new
    injection-scan pass if the tool was .read/.extract
```

Step 8's caveat is intentionally surfaced here rather than smoothed over — see §29, Q1.

---

## 24. Security Invariants

Refined from the brief's own examples, validated or corrected against source:

- **INV-B5-01**: AI cannot access raw WebView2/native browser APIs, arbitrary JS, or an unrestricted Tauri command. *(Confirmed structurally satisfiable — §18/§19.)*
- **INV-B5-02**: Every browser capability invocation is bound to an authenticated KORTEX principal, sourced only from `AuthenticationManager.verify_token()` — never a caller-supplied identity. *(Existing dispatcher guarantee, §3.1.)*
- **INV-B5-03**: Every capability invocation is tenant-bound, via `principal.tenant_id` — **with the one existing, narrow, unauthenticated-capability exception named in §3.1 explicitly closed for all `kortex.browser.*` names by always setting `requires_authentication=True`.**
- **INV-B5-04**: Every capability invocation targets an explicit, live-re-resolved `surface_id` bound to a specific `tenant_id`/`browser_profile_id` — never "whatever is open." *(New, §8 — this is the invariant most in need of careful implementation; it does not fall out of anything existing.)*
- **INV-B5-05**: `BrowserPolicyEngine` cannot be bypassed by capability execution, because capability execution terminates in the *same* `BrowserRuntime`/WebView2 event path a human action already goes through. *(§10 — the strongest invariant in this document, and the one this whole architecture was shaped around.)*
- **INV-B5-06**: Webpage content has no authority to create KORTEX capability authority — a page's text is data returned by `.read`/`.extract`, never itself a `ToolCall`. *(§12.)*
- **INV-B5-07**: Secrets are never returned through generic browser extraction beyond the existing scrub/sanitize backstop, refined with a mandatory injection-pattern scan (new). *(§3.8/§7, corrected from "never" to "the existing backstop, now extended" — the honest claim is defense-in-depth, not a mathematical guarantee against every possible secret shape.)*
- **INV-B5-08**: Secrets (typed values, page secrets, session tokens) are never written to a Browser capability audit entry. *(§14, extends existing `sanitize_for_persistence` discipline to new event types.)*
- **INV-B5-09**: Browser profile isolation cannot be bypassed through a capability target — `surface_id`/`profile_id`/`tenant_id` are all re-checked live at redeem time, never trusted from the grant's own claim alone. *(§6/§8 — this is *why* redeem-time re-resolution is mandatory, not optional.)*
- **INV-B5-10**: Capability failure is fail-closed — an expired grant, a stale surface, an ambiguous click target, an unrecognized tool, all resolve to denial/error, never to a best-guess action. *(Consistent with every existing fail-closed convention found in this session's research: `ABACEvaluator`'s unset-clearance default, `ToolGovernanceEvaluator`'s unknown-tool default, `UiElementSelector`'s zero-or-many-match default, B4's own malformed-URI default.)*

All ten hold up under review; none needed rejecting outright, though INV-B5-04 and INV-B5-09 required naming a genuinely new mechanism (live re-resolution) rather than assuming an existing one already provides them.

---

## 25. Implementation Plan (proposed sequencing, not a commitment to ship all of it)

- **B5.1** Capability contracts — schemas/types for all seven, per §7, including the new `GrantExpired`/`RefusedSensitiveInput`/`StaleReference` error variants (§15). No execution.
- **B5.2** Authorization integration — register all seven via `RegistryEngine`/`capability_tool_bridge` per §3.4/§9, with real `is_read_only` values from §13's table. Handlers return `NotYetSupported` for everything at this stage — proves the registration/discovery/governance path end-to-end before any WebView2 code is touched.
- **B5.3** Capability Execution Grant primitive (§6) — minting, signing, verification, single-use enforcement. Pure logic, unit-testable without a live WebView2 (same posture as `browser_policy.rs`'s own B4 test suite).
- **B5.4** New desktop Tauri command + capability-file scope (§19) — redeem, live re-resolution (§8), per-surface serialization (§17). This is where OD-B5's `tauri::test` mock-runtime gap (unresolved since B1) will again force a live-preflight-style verification rather than an automated `Window`/`Webview` test, per B4's own established precedent.
- **B5.5** `browser.navigate` — first real capability wired end-to-end (grant → redeem → real `BrowserRuntime.navigate` → B4 policy → report). Proves the whole architecture on the lowest-complexity capability.
- **B5.6** `browser.read`/`.extract` — including the new injection-scan pass (§12/§3.8).
- **B5.7** `browser.click`/`.type` — accessibility-tree targeting (new, non-trivial — no existing code in this repo does this for WebView2 specifically; `agent.proto`'s `UiElementSelector` is precedent for *shape*, not implementation), sensitive-input refusal (§7).
- **B5.8** `browser.screenshot` — storage path, not inline bytes.
- **B5.9** `browser.download` — contract only (§7); explicitly not executed.
- **B5.10** Audit integration — new event types into `AuditManager`, redaction review (§14).
- **B5.11** Adversarial testing — full matrix (§22), against the real implementation, not just the design.

Recommend B5.1–B5.4 as one reviewable unit (foundation, no user-visible behavior change), B5.5 as its own gate (first real capability — highest-value place to catch a design flaw before building six more capabilities on top of it), B5.6–B5.10 as a second unit, B5.11 closing every unit.

---

## 26. Migration / Compatibility Considerations

None required for B0–B4 — the existing human-facing Tauri commands (`browser_navigate` etc.) are untouched; B5 adds a parallel, AI-only path (§19), it does not modify the existing one. `BrowserSurfaceState` gains one new field (a generation counter, §8) — additive, not breaking, to the existing struct already serialized to the frontend.

---

## 27. Deferred Decisions

- Exact `parameters_schema`/`returns_schema` JSON Shape per capability (B5.1 implementation detail, not an architecture decision).
- Exact RBAC permission strings and which roles get them (§9, an operator/owner policy decision, not an architecture one).
- Exact accessibility-tree selector serialization format for `.click`/`.type`/`.extract` targeting (B5.7 implementation detail — needs a live WebView2 accessibility-API preflight, likely another disposable-harness exercise per B4's established methodology).
- Graded autonomy tiers beyond the existing binary `is_mutation` (§13, cross-cutting, explicitly not a B5-only decision).
- `browser.download`'s real implementation (OD-B14, later milestone).
- Popup-to-tab conversion (OD-B13) and permission-confirmation UI (OD-B15) remain B4's own deferred items, untouched by B5's own scope — B5 does not need either to expose its seven capabilities, since none of them require a popup or a native permission grant to function.

---

## 28. Known Limitations (carried forward + new)

- OD-B16 (DNS rebinding) — unchanged, inherited by every `browser.navigate` call regardless of caller.
- OD-B5 (live-WebView2 automated-test gap) — will affect B5.4/B5.7 exactly as it affected B1–B4; expect another disposable live-preflight, not a `cargo test`-only verification.
- §3.5's `action_fingerprint`-optional gap (falsy fingerprint skips TOCTOU check) — pre-existing, cross-cutting; Browser must always populate it, but cannot single-handedly fix the general-purpose skip condition.
- §3.6's per-tenant-only throttling — no per-surface/per-profile concurrency control exists at that layer; B5 must add its own (§17), separate from `TenantConcurrencyThrottler`.
- §3.7's AI-path idempotency gap — real, cross-cutting, not fixed by B5; Browser contracts are designed to tolerate it rather than assuming it away.
- Clickjacking/hidden-overlay residual risk (§21, threat #14) — accessibility-tree name-matching is a strong mitigation, not a mathematical guarantee against a sufficiently well-disguised overlay.
- Secret-shaped-value detection for `.type` (§7) is pattern-based, inheriting the same fundamental limitation `scrub_secrets_from_text` already has for output redaction — a sufficiently unusual secret format could slip through undetected in either direction.

---

## 29. Open Questions Requiring User Decision

1. **How does the backend address a Capability Execution Grant back to the *specific* desktop instance the AI orchestration session is acting on behalf of?** Today, AI orchestration runs entirely backend-side and has no existing concept of "which desktop process is currently connected." `invoke_capability` is desktop-initiated (desktop calls backend), not the reverse — so §6/§23's "grant returned via the existing invoke_capability response" only works cleanly if the *desktop* is the one that made the original request that led to the AI tool call being proposed (e.g., a human-initiated "have the AI do X in my browser" action, where the desktop is already waiting on a response). If the AI can initiate a browser action **without** an active, in-flight desktop-originated request (e.g., a fully autonomous background agent), there is currently no channel to reach that desktop instance at all, and this gate does not have enough evidence to say one exists. **This is the single most important open question in this document** — it may mean B5's practical scope is "AI browser actions triggered from within an active human-initiated session" only, not fully autonomous background browsing, until a real push channel to a specific desktop instance exists (which would need to be modeled on, but not literally be, the Phase 5/6 Desktop Agent Gateway's session-addressing concept — a session-identity mechanism for the *interactive* app, which doesn't exist today).
2. Should graded autonomy tiers be added to the existing `is_mutation` model (§13), and if so, is that a B5-scoped change or a separate, cross-cutting workstream?
3. Exact RBAC permission-string naming and role assignment for the seven capabilities (§9).
4. Whether `browser.download`'s contract should be registered now (discovery-visible, `NotYetSupported`) or withheld entirely until OD-B14 is actually implemented (§7/§27) — this gate recommends "registered now, not executed," but that's a judgment call, not a fact.
5. Whether new Browser audit events belong only in the real backend `AuditManager` (§14) or should also continue writing to B4's local `audit.log` for operational continuity during the transition — this gate recommends "backend AuditManager becomes authoritative for AI-originated actions; B4's local log continues recording WebView2-level enforcement facts as it does today, since those still occur with no backend involvement for human-originated actions."

---

## 30. B5 Readiness Verdict

**READY WITH CONDITIONS.**

- **B5-C01**: Open Question 1 (§29) must be resolved — either a concrete answer for how a backend-initiated grant reaches a specific desktop instance without an in-flight desktop-originated request, or an explicit scope reduction (B5 only supports AI browser actions triggered within an active human-initiated session) — before B5.3/B5.4 implementation begins. Objectively verifiable: a written answer exists and is reflected in this document or a follow-up decision-log entry before code is written.
- **B5-C02**: `requires_authentication=True` must be set on all seven `kortex.browser.*` registrations without exception, closing the §3.1 tenant-binding gap for this capability domain specifically. Objectively verifiable: grep the B5 registration code for `requires_authentication=False` on any `kortex.browser.*` entry — must return zero matches.
- **B5-C03**: Every Browser `ApprovalRequest` created via `DurableAIApprovalPolicy` must have a non-empty `action_fingerprint` — no code path may create one with it unset, given the §3.5 skip-on-falsy gap. Objectively verifiable: a unit test asserting `action_fingerprint is not None` on every Browser-originated `ApprovalRequest`.
- **B5-C04**: The new redeem-command (§6/§19) must re-resolve `surface_id`/`profile_id`/`tenant_id` against live state at execution time — never trust the grant's own claims alone (INV-B5-09). Objectively verifiable: an adversarial test that mints a valid grant, closes/reassigns the surface, then attempts redeem — must fail closed.
- **B5-C05**: The injection-pattern scan for `.read`/`.extract` output (§3.8/§12) must exist and run before any such content reaches `ToolResult.to_context_entry()`. Objectively verifiable: a test asserting known injection-pattern strings are neutralized/flagged in `.read`/`.extract` output specifically, not merely in prompts/completions.
- **B5-C06**: `.type`'s sensitive-input refusal (§7) must be implemented and tested before any B5 capability can accept free-form text input from an AI-originated call. Objectively verifiable: a test asserting password/API-key/OTP-shaped values are refused, ordinary text is accepted.
- **B5-C07**: `browser.download`'s handler must provably never call any `BrowserRuntime`/WebView2 execution path — it must be structurally impossible for it to bypass B4's existing deny-all, not merely policy-configured not to. Objectively verifiable: code review confirms the handler has no reference to any download-triggering primitive.

None of these conditions require re-opening B4. None require implementing B6/B7/B8 behavior early. All are scoped to B5's own foundation.

---

## Appendix: Exact Evidence Ledger

**Files inspected this session** (beyond B0–B4's own already-established baseline): `backend/src/kortex/core/dispatch.py`, `backend/src/kortex/core/idempotency.py` (call sites only), `backend/src/kortex/engines/registry/engine.py`, `backend/src/kortex/engines/security/{engine,authorization,abac,rbac,auth,models,secrets}.py`, `backend/src/kortex/engines/ai/{tools,governance,engine,throttling,memory,bridge}.py`, `backend/src/kortex/engines/workflow/{approval,models}.py`, `backend/src/kortex/engines/connector/models.py`, `backend/src/kortex/api/{schemas,main,capability_tool_bridge,kernel_bootstrap}.py`, `backend/src/kortex/engines/agent_gateway/engine.py`, `backend/src/kortex/engines/agent_gateway/protos/agent.proto` (via find/cat), `backend/src/kortex/engines/desktop_automation/engine.py`, `apps/desktop/src-tauri/src/ipc.rs`, `apps/desktop/src-tauri/src/events.rs`, `docs/architecture/browser_capability_model.md`, `docs/architecture/browser_security_model.md`.

**Existing abstractions reused, not duplicated**: `CapabilityDispatcher`/`RegistryEngine`/`CapabilityDescriptor` (registration + authorization chokepoint), `ToolDefinition.is_mutation`/`ToolGovernanceEvaluator`/`DurableAIApprovalPolicy`/`_on_approval_decided`/`TenantConcurrencyThrottler` (governance/approval/concurrency), `action_fingerprint` pattern + Ed25519 signing (`DurableApprovalManager`) as the template for the new Grant primitive, `scrub_secrets_from_text`/`sanitize_context_content`/`sanitize_for_persistence` (redaction), `CapabilityProjection` (discovery), `BrowserRuntime`/`BrowserPolicyEngine`/`BrowserProfileStore` (execution, unchanged), `invoke_capability` transport (reused in both directions per §6/§19), `UiElementSelector`'s targeting philosophy (design precedent only, not code reuse) from the Phase 5/6 Desktop Agent Gateway.

**New abstractions proposed**: Capability Execution Grant (§6), a per-surface navigation-generation counter (§8), a new narrow desktop Tauri command for AI-originated redeem (§19), per-surface concurrency serialization (§17), an injection-pattern scan applied to tool *results* (§12), an input-side sensitive-value detector for `.type` (§7).

**Final note**: this document does not resolve Open Question 1 (§29). That is deliberate — the evidence does not support an answer, and inventing one would violate this gate's own "derive it from source, do not assume" mandate. It is the one item this gate recommends the user/owner decide before any B5 code is written.
