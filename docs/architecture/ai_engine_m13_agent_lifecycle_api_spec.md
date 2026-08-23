# KORTEX OS — AI Orchestration Engine Milestone 13: Agent Task Lifecycle Kernel Exposure

**Status: IMPLEMENTED.**

Baseline: M12 commit `7c145b0` (Production Agent Safety Guardrails, Context Budgeting & Tenant Concurrency Throttling).

## 1. Problem

M12 built durable, tenant-isolated agent task control (`cancel_task`, `get_task`, `list_tasks`) all the way down to both `IAgentTaskStore` implementations, and exposed `cancel_agent_task`/`get_agent_task` as plain Python methods on the `AIOrchestrationEngine` facade. Neither method was ever registered as a Kernel capability, and `list_tasks` had no orchestrator or facade wrapper at all. The result: every other AI capability (`generate`, `orchestrate`, `resume`, `tool.invoke`, `provider.register`, `provider.list`) is reachable through the sanctioned Kernel enforcement boundary — the only sanctioned entry point into this engine — but task lifecycle control and observability were reachable only by holding a direct Python reference to the engine instance. A hardened capability was built and then left unreachable.

This is a completion gap, not a new capability: `IAgentTaskStore.list_tasks(tenant_id, status=None, limit=50)` already existed, already tenant-isolated, already tested on both `InMemoryAgentTaskStore` and `StorageAgentTaskStore` (`test_task_store_strict_tenant_isolation_storage`/`_in_memory`, M11). The gap was entirely in the two layers above the store.

## 2. Scope

Owns: wiring three existing/newly-thin operations through `AgentOrchestrator` → `AIOrchestrationEngine` → Kernel capability registration. Does not own: any new persistence, any new domain concept, any change to cancellation/status semantics (both were already correct and certified in M11/M12).

## 3. Changes

| File | Change |
|---|---|
| `backend/src/kortex/engines/ai/agent.py` | ADDITIVE — `AgentOrchestrator.list_tasks(tenant_id, status=None, limit=50)`, a one-line delegation to `self._task_store.list_tasks`, mirroring the existing `cancel_task`/`get_task` delegation style exactly. |
| `backend/src/kortex/engines/ai/engine.py` | ADDITIVE — `AIOrchestrationEngine.list_agent_tasks(tenant_id, status=None, limit=50)`; three new `kernel.register_capability(...)` calls in `initialize()`. |
| `backend/src/kortex/engines/ai/diagnostics.py` | ADDITIVE — `CANONICAL_CAPABILITIES` extended from 6 to 9 entries. |
| `backend/src/kortex/engines/ai/persistence.py` | FIX (pre-existing, unrelated to this milestone's feature work, found while verifying a clean mypy run — see §5) — `StorageAgentTaskStore.cancel_task` now wraps its return in `bool(...)`. |
| `backend/tests/unit/test_ai_engine.py` | ADDITIVE — capability-registration assertions extended to 9; six new tests (§4). |

## 4. New Kernel Capabilities

| Capability | Handler | Permissions | Classification |
|---|---|---|---|
| `kortex.ai.agent.cancel` | `cancel_agent_task` (existing, now registered) | `["ai:orchestrate"]` | INTERNAL |
| `kortex.ai.agent.status` | `get_agent_task` (existing, now registered) | `["ai:read"]` | INTERNAL |
| `kortex.ai.agent.list` | `list_agent_tasks` (new) | `["ai:read"]` | INTERNAL |

`cancel` is classified at `ai:orchestrate` (matching `orchestrate`/`resume`) since it mutates a running workflow's state; `status`/`list` are classified at `ai:read` (matching `provider.list`) since they are pure observability.

## 5. `list_agent_tasks` status-parameter normalization

`AgentStatus` is a `StrEnum`. A Kernel capability handler is invoked as `handler(**request.parameters)` — a plain, JSON-shaped call, so a caller can only ever supply `status` as a raw `str`, never an `AgentStatus` member. Left unhandled, this diverges by backend: `InMemoryAgentTaskStore`'s `r.status == status` comparison happens to keep working (StrEnum instances compare equal to their string value), while `StorageAgentTaskStore`'s `AIAgentTaskRow.status == status.value` would raise `AttributeError` on a plain string, which has no `.value`. `list_agent_tasks` therefore coerces `status: AgentStatus | str | None` via `AgentStatus(status)` before it reaches the orchestrator, so both backends see a real enum member, and an unrecognized string fails fast and loudly (`ValueError`, no sensitive content) at the facade rather than failing inconsistently two layers down.

## 6. Testing

Six tests added to `test_ai_engine.py` §4.5, all exercising the registered `kernel.capabilities[name]["handler"]` directly where reachability itself is under test (i.e. calling the handler exactly as `CapabilityDispatcher._invoke_handler` calls it: `handler(**parameters)`):

- Registration reachability: status/list/cancel invoked as bound Kernel handlers, not as engine methods, proving the capability wiring itself (not just the underlying method) works.
- Adversarial — cross-tenant list: tenant A's listing never contains tenant B's tasks, verified against a wider record set than a single task.
- Adversarial — cross-tenant cancel: cancelling `task_id` under the wrong `tenant_id` returns `False` and leaves the real task's status untouched, rather than cancelling by ID alone.
- Status filter correctness against a mixed-status task set.
- Raw-string status acceptance from the capability boundary (§5), proving the coercion.
- Invalid status string rejection (`ValueError`), proving the fail-fast boundary.

## 7. Verification

```
pytest tests/unit/test_ai_*.py -q          564 passed (558 baseline + 6 new)
mypy src/kortex/engines/ai/                Success: no issues found in 22 source files
ruff check <files touched by this milestone>   All checks passed
pytest tests/unit/test_ai_model_router.py -k forbidden_dependency   PASSED (unchanged)
pytest -q (full backend)                   see final completion report
```

25 pre-existing ruff findings across other, untouched AI test files (confirmed identical via `git stash` against the exact `7c145b0` baseline) are unrelated to this milestone and were left untouched, consistent with the standing rule to prove and leave pre-existing issues rather than expand scope.

## 8. Non-goals / deferred

Real Knowledge Engine retrieval wiring (`IKnowledgeQueryPort` still has no production adapter — `ContextComposer` is constructed with `knowledge=None` in `bootstrap.py`) was evaluated for this milestone and rejected: Knowledge Engine's `search(query: KnowledgeQuery)` capability handler requires a real `KnowledgeQuery` instance (the dispatcher splats `handler(**request.parameters)`), so a production adapter must either import `kortex.engines.knowledge` from inside the AI package (violates the AST-enforced dependency quarantine, which lists Knowledge Engine as forbidden everywhere without exception) or live in a cross-engine composition root — and no composition root exists anywhere in the platform yet (`Kernel()` is never instantiated in production `backend/src`, confirmed unchanged since M5). Building one is a platform-wide initiative outside this engine's boundary, not an AI-engine milestone. Recorded here as a known, unresolved production limitation rather than worked around.
