# KORTEX OS — AI Orchestration Engine: Authoritative Reference & Closeout

**Status: CLOSED.** Milestones M1–M13 complete and verified.

This is the authoritative, as-built reference for `kortex.engines.ai`. Where an earlier milestone spec disagrees with this document, this document describes what the code actually does; the milestone specs remain valid as historical design records and as the source of the requirements verified here.

---

## 1. Responsibilities

The AI Orchestration Engine is the platform's AI coordinator. Per Article 13 of the Engineering Constitution, **AI orchestrates — it never bypasses the Kernel, never touches storage directly, and never executes business logic.**

It owns: provider registration and selection, resilient provider execution, prompt/context composition, conversation memory, tool-call translation, bounded agent reasoning loops, agent task lifecycle, and its own diagnostics/telemetry.

It does **not** own: authorization decisions (Security Engine, via Kernel), capability dispatch (Kernel), durable transaction management (Storage Engine), or knowledge retrieval implementation (Knowledge Engine, behind a port).

## 2. Module Map

| Module | Responsibility |
|---|---|
| `engine.py` | `AIOrchestrationEngine` facade (`BaseEngine` + `IEngineDiagnostics`); production port adapters; capability registration |
| `interfaces.py` | Protocol contracts (`IAIOrchestrationEngine`, `IBaseAIProvider`, `IModelRouter`, `IAIMemoryManager`, `IAIToolInvoker`, `IEngineDiagnostics`, `IKernelBridge`) |
| `models.py` | Frozen Pydantic DTOs (`AIProviderMetadata`, `LLMRequest`, `LLMResponse`, `TokenUsage`) |
| `registry.py` | `ProviderRegistry` — thread-safe (`threading.RLock`) provider store |
| `router.py` | `ModelRouter` — stateless, I/O-free provider selection |
| `resilience.py` | `ResilientAIProvider` (timeout/retry/circuit breaker), `ProviderFallbackChain` |
| `memory.py` | `AIMemoryManager`, `IConversationStore`, sanitization + identifier guards |
| `pipeline.py` | `PromptPipeline` (pure assembly), `ContextComposer` (async fetch + compose) |
| `retrieval.py` | `IKnowledgeQueryPort`, `RetrievedDocument`, classification allowlist |
| `tools.py` | `ToolRegistry`, `AIToolInvoker`, output bounding, secret scrubbing |
| `agent.py` | `AgentOrchestrator`, `AgentTask`, `ResumeToken` (HMAC), `IAgentTaskStore` |
| `persistence.py` | **Sole infrastructure adapter.** SQLAlchemy rows + storage-backed stores |
| `bridge.py` | `KernelBridgeAdapter` — the only route to `CapabilityDispatcher` |
| `telemetry.py` / `telemetry_ports.py` | Tri-tier telemetry emission and external exporter port |
| `diagnostics.py` | `AIDiagnostics` — in-memory operational counters |
| `bootstrap.py` | `KernelProductionBootstrap` — dependency assembly and wiring validation |

## 3. Dependency Direction (AST-enforced)

`test_ai_package_imports_no_forbidden_dependency` scans every module unconditionally (including `TYPE_CHECKING` blocks).

- **Allowed everywhere:** `kortex.engines.ai.*`, `kortex.core.exceptions`, `kortex.core.base_engine`
- **Allowed only in `persistence.py`:** `kortex.core.db`, `kortex.engines.storage`, `sqlalchemy`
- **Forbidden everywhere, no exceptions:** `kortex.engines.security`, `kortex.core.kernel`, `kortex.core.container`, `kortex.engines.knowledge`

The Kernel and Security Engine are reached only through the `IKernelBridge` port, whose sole production implementation is `bridge.KernelBridgeAdapter`.

## 4. Kernel Capabilities (9)

| Capability | Permissions | Classification |
|---|---|---|
| `kortex.ai.response.generate` | `ai:generate` | INTERNAL |
| `kortex.ai.agent.orchestrate` | `ai:orchestrate` | INTERNAL |
| `kortex.ai.agent.resume` | `ai:orchestrate` | INTERNAL |
| `kortex.ai.agent.cancel` | `ai:orchestrate` | INTERNAL |
| `kortex.ai.agent.status` | `ai:read` | INTERNAL |
| `kortex.ai.agent.list` | `ai:read` | INTERNAL |
| `kortex.ai.tool.invoke` | `ai:execute` | INTERNAL |
| `kortex.ai.provider.register` | `ai:manage` | RESTRICTED |
| `kortex.ai.provider.list` | `ai:read` | INTERNAL |

## 5. Generation Pipeline

```
LLMRequest
  → identifier validation (fail closed on blank tenant/conversation)
  → tenant concurrency slot (TenantQuotaExceededError if over limit)
  → context composition (history + optional knowledge, sanitized)
  → provider enumeration (ModelRouter.select_candidates, ranked)
  → resilient execution with failover (ProviderFallbackChain)
  → history persistence (degraded response if it fails)
  → LLMResponse
```
The whole chain is wrapped in a global `asyncio.wait_for` deadline.

## 6. Routing

Selection criteria, as built: endpoint-type filter, fail-closed `allow_cloud` boolean, optional explicit `provider_id` pin, executability check, then rank order `local_host → network → cloud`.

`ModelRouter` reads **no field of `LLMRequest`** — injected content therefore cannot influence provider choice or cloud egress. `RoutingContext` uses `strict=True` so `allow_cloud=1`/`"yes"` cannot coerce to `True`.

Classification-aware, connectivity-aware, and task-type-aware routing were formally deferred by the M3 spec (D2–D4) as dependencies on services that do not exist platform-wide.

## 7. Resilience

Two independent layers:
- **Depth (per provider):** `ResilientAIProvider` — timeout, exponential backoff with jitter, transient/permanent classification, circuit breaker (`CLOSED → OPEN → HALF_OPEN`).
- **Breadth (across providers):** `_generate_with_fallback` enumerates every eligible candidate and runs them through `ProviderFallbackChain`. Exhaustion raises `ProviderFallbackExhaustedError`.

Breadth uses `max_attempts=1` per candidate so it never duplicates the depth layer's retry policy for already-wrapped providers.

## 8. Context & Knowledge Boundary

Prompt assembly order: caller `context_documents` → `[[knowledge]]` documents → conversation history.

Trust model: `system_instruction` is the only trusted layer and is never written by the engine. Everything entering `context_documents` is sanitized with **no exemptions** (`[[` → `[ [`), except already-rendered history, which is inserted verbatim because re-sanitizing would destroy its markers. **No `[[system]]` marker exists anywhere in the package** — there is no in-band token to forge.

Knowledge retrieval is opt-in, tenant-scoped, bounded by `max_documents`, deduplicated, and filtered by a fail-closed classification allowlist (`{PUBLIC, INTERNAL}` by default) that rejects non-ASCII input *before* case-folding (`"publıc".upper() == "PUBLIC"` is a real bypass).

**The real Knowledge Engine adapter is an external dependency — see §13.**

## 9. Agent Lifecycle

`AgentTask` (frozen, bounded: `max_steps ≤ 30`, `timeout_seconds ≤ 600`) → bounded ReAct loop → terminal `AgentExecutionResult`.

- **Approval:** `IApprovalPolicy` decides; the engine only responds to the boolean. `require_human_approval_for_mutations` defaults to `True`.
- **Pause/resume:** pausing issues an HMAC-SHA256 `ResumeToken` binding task id, step count, and a SHA-256 hash of the pending calls, with a 1-hour TTL. Resume verifies all four, then **atomically claims** the record (`PAUSED_FOR_APPROVAL → RESUMING` with version CAS). A resume with no persisted record is refused (`AgentNotFoundError`), and a replayed token is refused (`AgentStateConflictError`) — one approval authorizes exactly one execution.
- **Tool allowlist:** `AgentTask.allowed_tools` is enforced before approval or execution.
- **Cancellation:** durable, cross-process, via `cancel_task`; cancelled tasks cannot be resumed.
- **Token accounting:** `TokenUsage` accumulates across all steps and is persisted.

## 10. Security Summary

| Control | Mechanism |
|---|---|
| Tenant isolation | `require_identifier` before every tenant-scoped store/port access; tenant predicate on every query |
| Authorization | Kernel `CapabilityDispatcher` → Security Engine. The AI engine makes no authority decision. |
| Approval integrity | HMAC-SHA256 token + call hash + CAS single-use claim |
| Prompt injection | Marker-sentinel neutralization, no `[[system]]` marker, single sanitizer implementation |
| Secret handling | Security Engine `secret_handle` only; regex scrubbing of tool output and step context; no credential in any exception |
| Tool output | Double-bounded (50k chars, then 64KB UTF-8) before entering context |
| Resource exhaustion | Per-tenant generation/agent concurrency limits; step/timeout/batch caps |
| Production wiring | Bootstrap refuses to assemble a production engine on in-memory test doubles |

## 11. Observability

Tri-tier: `AIDiagnostics` (in-memory counters) → `AITelemetryEmitter` (Event Engine) → `ITelemetryExporter` (external sinks). Event publication is fail-open: a telemetry failure never breaks a generation turn.

Fifteen event types: the four spec-mandated ones (`ai.generation.started`/`.completed`, `ai.tool.invoked`, `ai.agent.completed`) plus eleven operational ones covering generation/agent failure, provider resilience (failure/timeout/fallback), security denial, tool failure/denial, and storage durability.

## 12. Degraded-Response Semantics

Two operations return a result flagged `degraded=True` rather than failing or lying:

- `LLMResponse.degraded` — generation succeeded but conversation-history persistence failed.
- `AgentExecutionResult.degraded` — the agent reached a terminal state but that state could not be persisted, so `get_task`/`list_tasks` will disagree until reconciled.

Both emit `ai.storage.write_failed`. The principle: never discard completed work, and never report success for a durability gap.

## 13. External / Platform Dependencies

These are genuinely outside the AI Engine's architectural boundary. Each has a defined interface, verified engine-side behavior, and does not leave the engine incomplete.

### 13.1 Knowledge Engine adapter (`IKnowledgeQueryPort`)

- **Owner:** M8-era wiring layer / platform composition root — *not* the AI Engine.
- **Why it cannot live here:** `kortex.knowledge.query.search`'s handler signature is `search(query: KnowledgeQuery)`, and the dispatcher splats `handler(**parameters)`. Constructing a real `KnowledgeQuery` requires importing `kortex.engines.knowledge`, which is forbidden in this package without exception. The capability is also `requires_authentication=True` and needs a session token `LLMRequest` does not carry.
- **Contract the adapter must honour:** return records only (nodes are not trust-filtered and carry no classification); cap the **union** (Knowledge Engine applies `max_results` per sub-search, so asking for N can yield 2N); deliver `content` as an already-rendered `str`; bind to the **requesting principal's** authority, never a long-lived service principal.
- **Engine-side behavior verified without it:** retrieval is opt-in, so its absence changes nothing unless requested; requesting retrieval with no port configured raises `ContextCompositionError` (the anti-dead-port rule) rather than silently returning nothing; adapter failure raises `KnowledgeRetrievalError`; an over-returning adapter is rejected rather than silently truncated. `InMemoryKnowledgeQueryPort` provides a contract-conformant implementation for development and tests.
- **Why it does not block closure:** the port, its contract, its failure semantics, and its tests are all complete. What is missing is a *binding to another engine*, which cannot be written from inside this one.

### 13.2 Platform composition root

- **Owner:** platform.
- **Status:** `Kernel()` is never instantiated in production `backend/src` — for *any* engine, not just AI. Connector Engine's analogous `secret_resolver` port has likewise been unwired since it shipped.
- **Engine-side behavior verified:** `KernelProductionBootstrap.create_ai_engine` assembles a complete, Kernel-ready engine from an injected `IKernelBridge`, and **refuses to assemble a production engine without one**. `test_ai_production_runtime.py` exercises the full assembly and Kernel lifecycle.
- **Why it does not block closure:** the engine is independently closable at its boundary — it exposes a bridge port and a validated assembler, which is exactly what a composition root consumes.

### 13.3 Pre-existing flaky test — Workflow Engine (not AI, deliberately untouched)

`tests/integration/test_workflow_integration.py::test_workflow_engine_kernel_integration` fails intermittently (~50%).

- **Owner:** Workflow Engine. The test contains **zero AI references**.
- **Cause:** it gates assertions on async completion with two bare `await asyncio.sleep(0.05)` calls, then asserts `workflow.completed` was published. Under any load the workflow reaches `workflow.resumed` but not `workflow.completed` inside 50 ms.
- **Proof it is pre-existing and unrelated to the AI Engine:** run in isolation, in its own process, with no AI test in the session and therefore no AI code executed — **M13 baseline: 3 passed / 5 failed; with this pass's changes: 4 passed / 4 failed.** It flakes *slightly more often at the baseline*. An earlier single green baseline full-suite run was a coin flip, not evidence of stability.
- **Left untouched deliberately**, per the rule that the AI Engine must not modify another engine to make itself appear complete. Fixing it belongs to whoever owns Workflow Engine (replace the fixed sleeps with a condition/event wait).

### 13.4 Tokenizer / context-window metadata

- **Owner:** platform / M1 contract amendment.
- **Status:** no tokenizer exists anywhere in the platform, and `AIProviderMetadata` carries no context-window field.
- **Engine-side behavior:** context budgeting uses an explicit, documented ~4-chars-per-token heuristic and turn-count truncation. The engine emits no size guarantee it cannot honour; oversized-context failures surface from the provider.

## 14. Configuration

`AIEngineRuntimeConfig` — `environment`, `storage_backend`, `enable_cloud_models` (default `False`), `max_context_tokens` (8192), `max_tool_result_bytes` (64KB), `default_generation_timeout_seconds` (60), per-tenant concurrency limits (10 generations / 5 agents), step-history window (10), retry attempts (3), circuit-breaker threshold (3) and recovery timeout (30s).

In the `production` profile, `create_ai_engine` requires both `data_store` and `kernel_bridge`; `development` permits in-memory fallbacks and logs a warning for each.

---

# CLOSEOUT CHANGELOG — Final Pass

Every substantive change made during the final closeout pass, why it was required, and how it was verified. All fixes are AI-Engine-owned; no other engine was modified.

### F1 — Provider fallback routing wired into the live request path
**Why:** M9's ratified Recovery Matrix requires "route to secondary local/cloud candidate" on primary failure. `ProviderFallbackChain` existed and was unit-tested since M9.2 but was never referenced by `engine.py` or `bootstrap.py`; both execution paths selected exactly one provider and failed outright.
**Change:** new shared helper `_generate_with_fallback` in `engine.py`, used by both `RouterLLMExecutionPort.generate_step` (agent steps) and `generate_response` (direct generation). `RouterLLMExecutionPort` gained an optional `telemetry` parameter, threaded from both construction sites.
**Behavior change:** exhausting all candidates now raises `ProviderFallbackExhaustedError` rather than the last provider's own exception.
**Tests:** 3 added; mutation-verified.

### F2 — Graceful degradation when conversation-history persistence fails
**Why:** M9's Recovery Matrix requires returning the generation "with degraded flag and emit system alert". A post-generation storage failure was caught by the generic handler, emitted `ai.generation.failed`, and re-raised — discarding a successful generation.
**Change:** `LLMResponse.degraded` field; `AIStorageWriteFailedEvent` (`ai.storage.write_failed`); `AITelemetryEmitter.emit_storage_write_failed`; `_execute_generation` wraps only `append_history` and returns a degraded copy on failure.
**Tests:** 3 added; mutation-verified.

### F3 — Agent terminal state no longer silently lies about persistence
**Why:** `_terminal()` swallowed `update_task` failures with a warning, returning a terminal result while the stored row stayed `RUNNING`/`PAUSED_FOR_APPROVAL` forever — a zombie task that the M13 `status`/`list` capabilities would misreport indefinitely. Asymmetric with the COMPLETED path, which persists unguarded.
**Change:** `AgentExecutionResult.degraded` (mirroring `LLMResponse.degraded`); `logger.critical` instead of `warning`; emits `ai.storage.write_failed`.
**Files:** `agent.py`.

### F4 — Production bootstrap refuses to assemble on test doubles
**Why:** M9 ratified a "Production Engine Wiring Requirement" that was never implemented. `environment="production"` with no `data_store` silently produced `InMemoryConversationStore` + `InMemoryAgentTaskStore` (history and agent state lost on restart); with no `kernel_bridge` it produced `InMemoryToolExecutionPort` — a reference fake that runs canned handlers and **never reaches `CapabilityDispatcher`**, so every tool call would bypass Security Engine authorization.
**Change:** `validate_production_wiring()` raises `AIBootstrapError` listing every missing production dependency; called before assembly. Development profile unchanged but now logs an explicit warning per fallback.
**Tests:** 4 added. One existing test (`test_production_profile_configuration`) was building an unwired production engine; it now supplies real wiring, which is what it always meant to test.

### F5 — Approval tokens are single-use; resume requires a persisted record
**Why:** `resume_task` had a branch that proceeded when no stored record existed, bypassing the atomic `PAUSED_FOR_APPROVAL → RESUMING` CAS that *is* the single-use guarantee. Within the token's 1-hour TTL this permitted replaying already-approved mutating tool calls — precisely what the approval workflow exists to prevent for payments, terminations, and transfers. It also silently discarded the execution trace (`initial_steps = []`), and contradicted the store contract (`claim_task_for_resumption` already raises `AgentNotFoundError` for a missing record) and M11's own stated design.
**Change:** missing record now raises `AgentNotFoundError`.
**Tests:** 2 added (`..._refuses_when_no_persisted_record_exists`, `..._cannot_be_replayed_after_a_successful_resume`). Two pre-M11 tests used two orchestrators with *separate* in-memory stores; both now share one store, which is how production behaves and how the M11 crash-recovery test already modelled it.

### F6 — Replaced a tautological security assertion
**Why:** `test_agent_exception_message_never_contains_tenant_data` asserted `"tenant" not in msg or "tenant" == "tenant"`. The right disjunct is always true, so the test passed unconditionally while reading as though it enforced exception hygiene.
**Change:** rewritten as `test_agent_exception_messages_never_contain_task_goal_or_tenant_values` — plants sentinel values in goal/tenant/user/conversation, triggers real validation failures, asserts no sentinel value appears. Mutation-verified.

### F7 — Replaced a vacuous persistence-boundary probe
**Why:** `test_no_persistence_calls_in_source` grepped for `append_history`/`save_steps` — names that never appear in `agent.py` — so it passed vacuously while claiming to enforce "zero persistence writes", a boundary M11 legitimately superseded with `IAgentTaskStore`.
**Change:** kept the still-valid conversation-history assertion with corrected reasoning, and added `test_agent_writes_only_through_the_task_store_port`, which asserts the boundary that actually holds (no SQLAlchemy/session/raw SQL; writes only via known `IAgentTaskStore` methods).

### F8 — `IAIOrchestrationEngine` Protocol reconciled with the implementation
**Why:** three of four method signatures still carried M1 `dict[str, Any]` placeholders and a docstring claiming "no implementation exists yet". Separately, the Protocol declared `invoke_tool`'s `authorizer` mandatory while the facade has always defaulted it to `None` — a contract/implementation contradiction with a test locking in the false side.
**Change:** signatures narrowed to the real types (`AgentTask`, `AgentExecutionResult`, `ToolCall`, `ToolResult`, `BaseAIProvider`, `RoutingContext`) via `TYPE_CHECKING` imports (a runtime import would be circular, since `tools.py` imports `interfaces.py`). The `authorizer` contradiction is resolved **in favour of the Kernel being the mandatory gate** — documented explicitly on the method — because spec §18 and the Constitution both place authorization in Security Engine via `CapabilityDispatcher`, and M1's own `ToolAuthorizer` docstring warns against engine-local authorization. `IAIToolInvoker.invoke`'s mandatory authorizer is unchanged and still guarded.
**Tests:** the superseded assertion was replaced with a **stronger** one — `test_orchestration_engine_tool_path_cannot_execute_without_the_kernel` asserts `KernelToolExecutionPort.execute_tool` has exactly one execution path and it is `kernel_bridge.invoke_capability`, with no handler-bypass. Mutation-verified.

### F9 — Missing invariant coverage closed
- `test_blank_identifiers_rejected_on_append_history` — M4 invariant M3 was only covered on the read path; removing either guard in `append_history` previously failed no test. Mutation-verified.
- `test_assemble_performs_no_io` — M5 invariant P3 (assembly purity) had no test.
- `test_invoker_never_reenters_the_execution_port_for_a_single_call` + `test_invoker_source_contains_no_self_recursion` — M6 §2.2 (zero nested tool execution) had no test; a tool output that *looks* like another tool call must never be acted on.

### F10 — Hygiene
Pre-existing `no-any-return` in `StorageAgentTaskStore.cancel_task` fixed. CRLF line endings normalized to LF in `engine.py` and `test_ai_agent.py` (repo convention). New test sentinels renamed off the `secret_*` prefix to avoid false-positive `S105` findings.

## Files Changed (final pass)

**Source:** `agent.py` (F3, F5), `bootstrap.py` (F1, F4), `engine.py` (F1, F2), `events.py` (F2), `interfaces.py` (F8), `models.py` (F2), `telemetry.py` (F2), `persistence.py` (F10)

**Tests:** `test_ai_agent.py` (F5, F6, F7), `test_ai_bootstrap.py` (F4), `test_ai_engine.py` (F1, F2, F5), `test_ai_memory.py` (F9), `test_ai_models.py` (F8), `test_ai_pipeline.py` (F9), `test_ai_tools.py` (F9)

**Docs:** this file; `ai_engine_m9_remediation_fallback_and_degradation.md` (F1, F2 detail); `ai_engine_m13_agent_lifecycle_api_spec.md` (M13)
