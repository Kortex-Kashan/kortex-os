# KORTEX Browser — Capability Model

**Status**: Living document — established Browser-B0 (design only). **Registered as of Browser-B5.0-B5.4** (`backend/src/kortex/engines/browser/`) — every capability below mints a Capability Execution Grant (or, for `.download`, always refuses); none of them execute a real browser action yet. See `docs/architecture/browser_b5_architecture_gate.md` and `browser_decision_log.md` D34-D39 for the full account of what changed between this document's original B0 sketch and what B5 actually implemented.

## 1. Governed actions (target set, per the task brief)

`browser.navigate`, `browser.read`, `browser.click`, `browser.type`, `browser.extract`, `browser.download`, `browser.screenshot` — plus one infrastructure capability B0 did not anticipate, `browser.grant_verification_key` (lets the desktop fetch the public key it needs to verify a Grant's signature; never itself an AI-invocable tool). All eight are registered as of B5.0-B5.4; none execute yet (D36).

## 2. Where they plug into the existing capability system

KORTEX already has one uniform capability-registration pattern, proven identically across Connector (M7.3), Document (M7.4), and Knowledge (M7.5) actions (`backend/src/kortex/engines/registry/engine.py`, `api/capability_tool_bridge.py`, `api/kernel_bootstrap.py`). `kortex.browser.*` should be a new `owner_domain` following it exactly:

1. Register each action as a plain Kernel capability via `Kernel.register_capability(name="kortex.browser.<action>", ...)`, with a real `parameters_schema` and an explicit `is_read_only` value — never left at the fail-closed default without a deliberate choice.
2. Convert each `CapabilityDescriptor` to a `ToolDefinition` via the existing `generate_tool_definition_from_capability()` (`api/capability_tool_bridge.py:63-86`) — this derives `is_mutation = not is_read_only` automatically; do not hand-write a parallel `ToolDefinition`.
3. Register idempotently into the shared `ToolRegistry` via `_register_tool_if_absent()` (`api/kernel_bootstrap.py:381-397`), following `register_connector_action_ai_tools`'s existing loop (`kernel_bootstrap.py:514-533`) as the template.

No new approval engine, no new throttler, no new orchestration path is needed — `DurableAIApprovalPolicy`, `AIOrchestrationEngine._on_approval_decided`, and `TenantConcurrencyThrottler` (`ai/engine.py:2410-2520`, `ai/throttling.py`) already handle every governed mutating action in KORTEX today and should handle Browser's identically.

**Implemented exactly as sketched above, as of Browser-B5.0-B5.4** (`backend/src/kortex/engines/browser/engine.py`, `backend/src/kortex/api/kernel_bootstrap.py::register_browser_ai_tools`) — steps 1-3 are real, tested code, not merely a plan. **The one thing this B0-era sketch did not anticipate**: a capability handler executing entirely backend-side has no way to reach a WebView2 surface, which lives in the desktop process. The B5 architecture gate (§6) resolved this with the Capability Execution Grant — every handler mints a signed, short-lived authorization artifact and returns it rather than executing directly; see `browser_b5_architecture_gate.md` and `browser_decision_log.md` D36-D37 for the full mechanism and a real cross-language bug it caught.

## 3. Read-only classification — deliberately conservative

| Capability | Recommended `is_read_only` | Rationale |
|---|---|---|
| `browser.read` | `true` | Reads rendered content only. |
| `browser.extract` | `true` | Structured read of page content only. |
| `browser.screenshot` | `true` | Captures pixels only. |
| `browser.navigate` | `false` | A page load can trigger arbitrary side effects (forms auto-submitting, beacons firing, session state changing) a static "just navigation" label can't rule out. |
| `browser.click` | `false` | Same reasoning — a click's effect on an untrusted page is not statically knowable. |
| `browser.type` | `false` | Can submit credentials, trigger form actions, or otherwise mutate remote state. |
| `browser.download` | `false` | Writes a file to disk and may exfiltrate or import untrusted content. |

This means `browser.navigate`, `.click`, `.type`, `.download` are `is_mutation=True` by construction and automatically routed through the existing approval-required path (`ToolGovernanceEvaluator.evaluate_tool_calls`) — no bespoke Browser-specific governance logic needs to be written for this.

## 4. Security classification

Recommend `security_classification="CONFIDENTIAL"` at minimum for every `kortex.browser.*` capability (matching the precedent set by `kortex.desktop.*`, `desktop_automation/engine.py:119-162`) and `requires_execution_context=True` for all of them — page-originated calls must never bypass the dispatcher-constructed execution context (see `browser_security_model.md` §2).

## 5. Tool output handling

Content returned by `browser.read`/`.extract` must go through the existing secret-scrubbing/truncation backstop (`ToolResult.to_context_entry`, `ai/tools.py`) **plus** a new prompt-injection-aware sanitization step (see `browser_security_model.md` §11) before re-entering LLM context. This sanitizer does not exist anywhere in the codebase today and is the one genuinely new component this capability set requires beyond what's reusable as-is.

## 6. Tenant-scoped visibility

`kortex.browser.*` capabilities are filtered by the existing `CapabilityProjection`/`project_tools_for_tenant()` (`core/projection.py`) exactly like every other capability — no bespoke visibility logic. The desktop frontend's `CapabilityPalette` (`apps/desktop/src/features/workflow/components/CapabilityPalette.tsx`) currently uses a hardcoded `CURATED_CAPABILITY_NAMES` allowlist (`CapabilityPalette.tsx:13-17`); B5 or B8 must add the new `kortex.browser.*` names to that allowlist for them to appear in the visual workflow builder — a one-line addition, not a new mechanism.

## 7. Relationship to Desktop Automation (`kortex.desktop.*`)

Conceptually similar (both are "act on a UI on the user's behalf" capabilities) but architecturally distinct in transport: Desktop Automation's capabilities are dispatched over an mTLS gRPC channel to a separately-installed .NET Windows Service, because that action executes against arbitrary native Windows applications outside KORTEX's own process. Browser capabilities act on a runtime (`IKortexBrowserRuntime`) that lives in-process with the Tauri app — they should call into it directly (or via Tauri's existing IPC if the runtime process is split out), through a leaner engine analogous to `DesktopAutomationEngine`, without standing up a second `AgentGatewayEngine`/mTLS session-authorization layer that exists to solve a remote-machine-identity problem Browser doesn't have.

## 8. Not yet answered

**Resolved by Browser-B5.0-B5.4**: `parameters_schema` per action (real JSON schemas, `engine.py`, derived from `models.py`'s typed contracts); target-selector format for `.click`/`.type`/`.extract` (`BrowserElementSelector` — role/accessible-name/node-ref, deterministic, never a raw CSS/XPath selector or pixel coordinate; see `browser_b5_architecture_gate.md` §7/§12).

**Still open, deferred to Browser-B5.5+**: the exact approval UX shown to a human when a mutating `kortex.browser.*` call requires approval; wiring real `BrowserRuntime` execution behind a verified Grant, per capability (OD-B17); real implementation of `browser.download` (OD-B14).
