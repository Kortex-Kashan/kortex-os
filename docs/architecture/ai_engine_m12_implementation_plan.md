# KORTEX AI Orchestration Engine — Milestone 12 Implementation Plan

**Title**: Production Agent Safety Guardrails, Context Budgeting & Tenant Concurrency Throttling  
**Milestone**: M12  
**Target Engine**: `kortex.engines.ai`  
**Author**: Principal AI Systems Architect & Security Engineer  
**Status**: APPROVED FOR IMPLEMENTATION  

---

## 1. Problem Statement & Motivation

Following the completion and certification of Milestones 1 through 11, the KORTEX AI Orchestration Engine possesses durable SQLite/PostgreSQL persistence, optimistic CAS crash recovery, prompt delimiter neutralization, tool result byte bounding, and tri-tier telemetry.

However, an adversarial gap analysis reveals five remaining security, reliability, and lifecycle vulnerabilities:

1. **Unbounded & Unscrubbed Agent Step Context Inflation**: `EngineAgentContextPort.build_step_context` concatenates all previous step thoughts and raw tool results (`tr.output`) into the prompt without token bounding, step windowing, secret scrubbing, or delimiter neutralization.
2. **Tenant Resource Starvation & Concurrency Abuse**: The engine facade accepts unbounded concurrent requests per tenant, allowing a single rogue or looping tenant to exhaust memory, database connection pools, and inference concurrency.
3. **Missing Tool Capability Allowlisting & Role Guardrails**: `AgentTask` specifies `agent_role` and potential tool restrictions, but tool invocation lacks an explicit `allowed_tools` allowlist enforcement, permitting prompt-injected agents to invoke any registered capability.
4. **Missing Cumulative Token Usage Tracking**: Multi-step agent executions discard per-step token consumption, returning zero aggregate token usage in `AgentExecutionResult` and `PersistedAgentTaskRecord`.
5. **Missing Facade Task Lifecycle Management**: No standard method exists on `AIOrchestrationEngine` or `AgentOrchestrator` to cancel or inspect long-running / paused tasks durably.

---

## 2. Proposed Architecture & Subsystems

```
                                 AIOrchestrationEngine
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
      TenantConcurrencyThrottler                         AgentOrchestrator
      (Per-Tenant Active Slot & Quota)                            │
                                                  ┌───────────────┴───────────────┐
                                                  ▼                               ▼
                                       EngineAgentContextPort             AIToolInvoker
                                    (Bounded, Scrubbed, Sanitized)     (Allowed Tools Policy)
                                                  │                               │
                                                  ▼                               ▼
                                            PromptPipeline               IToolExecutionPort
```

### 2.1 M12.1: Bounded & Sanitized Agent Step Context (`EngineAgentContextPort`)
- Bounded step history window: Keep only the most recent $N$ steps (default: 10 steps, configurable).
- Step tool results rendered via `scrub_secrets_from_text` and `sanitize_context_content`.
- Maximum character / token limit per step result preventing prompt explosion.

### 2.2 M12.2: Tenant Concurrency Throttling & Rate Limiting (`TenantConcurrencyThrottler`)
- Thread-safe, async semaphore-based concurrency control per `tenant_id`.
- Configurable `max_concurrent_generations_per_tenant` (default: 10) and `max_concurrent_agents_per_tenant` (default: 5).
- Rejects excess requests immediately with `TenantQuotaExceededError` (fast-fail, no resource hoarding).

### 2.3 M12.3: Role-Based Tool Allowlisting (`AgentTask.allowed_tools` Policy)
- Optional `allowed_tools: list[str] | None = None` on `AgentTask`.
- If `allowed_tools` is specified, `AIToolInvoker` and `AgentOrchestrator` validate proposed tool calls against the allowlist before execution or human approval pause.
- Unpermitted tool calls are rejected with `ToolAuthorizationError` (fail-closed).

### 2.4 M12.4: Cumulative Token Usage & Accounting Across Multi-Step Loops
- `AgentExecutionResult` and `PersistedAgentTaskRecord` include `total_token_usage: TokenUsage`.
- `AgentOrchestrator._run_loop` aggregates `prompt_tokens`, `completion_tokens`, `total_tokens` from every `LLMResponse.token_usage`.
- Emitted in `AgentTaskCompletedEvent`.

### 2.5 M12.5: Durable Task Cancellation & Status Inspection
- Added `cancel_task(task_id: str, tenant_id: str)` to `IAgentTaskStore`, `StorageAgentTaskStore`, `InMemoryAgentTaskStore`, `AgentOrchestrator`, and `AIOrchestrationEngine`.
- Added `get_task(task_id: str, tenant_id: str)` to `AgentOrchestrator` and `AIOrchestrationEngine`.
- Transactionally transitions `PAUSED_FOR_APPROVAL` or `RUNNING` tasks to `CANCELLED`.

---

## 3. Invariants & Security Guarantees

1. **Tenant Quota Invariant**: A tenant exceeding its active concurrency limit receives `TenantQuotaExceededError` without entering prompt composition or provider execution.
2. **Context Defense Invariant**: Tool outputs and thoughts in multi-step agent histories must never leak unscrubbed secrets or raw prompt delimiters into subsequent turns.
3. **Tool Guardrail Invariant**: If `AgentTask.allowed_tools` is non-empty, any call to an unlisted tool is blocked and recorded as a failed step.
4. **Token Accounting Invariant**: `AgentExecutionResult.total_token_usage` equals the exact sum of all step token counts.
5. **Clean Architecture & Quarantine**: No SQL or relational models outside `persistence.py`. Zero third-party SDK dependencies in core domain logic.

---

## 4. Test & Verification Matrix

| Test Category | Target Module | Scope |
| :--- | :--- | :--- |
| Agent Step Context Defense | `test_ai_m12_guardrails.py` | Sliding step windowing, tool result secret scrubbing & delimiter defense in `EngineAgentContextPort` |
| Tenant Concurrency Throttling | `test_ai_m12_guardrails.py` | Concurrent flood tests, per-tenant slot limits, `TenantQuotaExceededError` fast rejection |
| Tool Allowlist Guardrails | `test_ai_m12_guardrails.py` | `allowed_tools` allowlist enforcement, unauthorized tool rejection in orchestrator and invoker |
| Cumulative Token Accounting | `test_ai_m12_guardrails.py` | Multi-step token aggregation across steps in `AgentExecutionResult` and persistence records |
| Task Cancellation & Lifecycle | `test_ai_m12_guardrails.py` | External task cancellation across stores, state transitions, rejection of resumption on cancelled tasks |

---

## 5. Non-Goals

- Replacing the existing `ModelRouter` or `ProviderRegistry`.
- Introducing distributed Redis caching in this milestone (SQLite / in-memory concurrency controls first).
- Modifying Kernel Bridge communication protocols.
