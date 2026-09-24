# KORTEX Browser — Capability Model

**Status**: Living document — established Browser-B0 (design only; no capabilities registered yet). Implemented starting Browser-B5.

## 1. Governed actions (target set, per the task brief)

`browser.navigate`, `browser.read`, `browser.click`, `browser.type`, `browser.extract`, `browser.download`, `browser.screenshot`. None of these are implemented in B0. This document records where and how they should register once B5 begins.

## 2. Where they plug into the existing capability system

KORTEX already has one uniform capability-registration pattern, proven identically across Connector (M7.3), Document (M7.4), and Knowledge (M7.5) actions (`backend/src/kortex/engines/registry/engine.py`, `api/capability_tool_bridge.py`, `api/kernel_bootstrap.py`). `kortex.browser.*` should be a new `owner_domain` following it exactly:

1. Register each action as a plain Kernel capability via `Kernel.register_capability(name="kortex.browser.<action>", ...)`, with a real `parameters_schema` and an explicit `is_read_only` value — never left at the fail-closed default without a deliberate choice.
2. Convert each `CapabilityDescriptor` to a `ToolDefinition` via the existing `generate_tool_definition_from_capability()` (`api/capability_tool_bridge.py:63-86`) — this derives `is_mutation = not is_read_only` automatically; do not hand-write a parallel `ToolDefinition`.
3. Register idempotently into the shared `ToolRegistry` via `_register_tool_if_absent()` (`api/kernel_bootstrap.py:381-397`), following `register_connector_action_ai_tools`'s existing loop (`kernel_bootstrap.py:514-533`) as the template.

No new approval engine, no new throttler, no new orchestration path is needed — `DurableAIApprovalPolicy`, `AIOrchestrationEngine._on_approval_decided`, and `TenantConcurrencyThrottler` (`ai/engine.py:2410-2520`, `ai/throttling.py`) already handle every governed mutating action in KORTEX today and should handle Browser's identically.

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

Exact `parameters_schema` per action, exact target-selector format for `browser.click`/`.type` (CSS selector? accessibility-tree reference?), and the exact approval UX shown to a human when a `browser.navigate` requires approval. Deferred to B5 design.
