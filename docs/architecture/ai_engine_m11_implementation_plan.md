# KORTEX AI Orchestration Engine — Milestone 11 Implementation Plan
## Production Agent Persistence, Crash Recovery & Context Defense

**Target Component:** `kortex.engines.ai`  
**Status:** IMPLEMENTATION SPECIFICATION & PLAN  
**Version:** 1.0.0  
**Authority:** Chief Architect: KASHAN / KORTEX OS Engineering Constitution (`AGENTS.md`)  
**Baseline Commit:** `dcd2ace` (M10 Adversarial Certification & Remediation)  
**Target Release:** KORTEX OS Phase 2: Business Foundation  

---

## 1. Executive Summary & Problem Statement

Milestone 10 achieved full adversarial certification of the foundational AI engine components (cryptographic token signing, sliding-window token budgets, global generation timeouts, and metrics consistency).

However, rigorous reconnaissance and adversarial analysis of enterprise production operations identified four operational gaps:

1. **In-Memory Agent Ephemerality & Crash Vulnerability (M11.1 & M11.2):**
   `AgentOrchestrator` maintains active tasks purely in process memory (`_active_tasks: dict[str, AgentTask]`). When an agent task enters `PAUSED_FOR_APPROVAL` for human review, any node recycle, pod restart, or server reboot destroys all task state. Subsequent calls to `resume_agent_task(ResumeToken)` fail with `AgentNotFoundError`. Furthermore, under distributed execution, two worker nodes could race to resume the same paused task.
2. **Prompt Boundary Delimiter Injection (M11.3):**
   `PromptPipeline` uses structural markers (`[[user]]`, `[[assistant]]`, `[[system]]`, `[[tool]]`, `[[knowledge]]`). While `sanitize_context_content` neutralized `[[`, attackers could craft variant encodings, whitespace variations, or delimiter sequences in RAG knowledge and tool outputs to forge trusted system boundaries.
3. **Unbounded Tool Output & Secret Scrubbing (M11.4):**
   `AIToolInvoker` lacks a hard UTF-8 byte boundary on serialized tool output entering model context, risking context overflow, memory exhaustion, and potential secret leakage.
4. **Unbounded History Query Overhead (M11.5):**
   `StorageConversationStore.recent_turns()` lacks pagination windowing (`limit`, `offset`), which can lead to high database I/O on lifelong conversations.

---

## 2. Architecture & State Machine

```
===================================================================================================
                                      M11 SYSTEM TOPOLOGY
===================================================================================================

    [ Kernel / Caller ]
            |
            v
  +-------------------------------------------------------------------------------+
  |                             AIOrchestrationEngine                             |
  |                                                                               |
  |   +---------------------+   +---------------------+   +---------------------+ |
  |   | PromptPipeline      |   | AIToolInvoker       |   | AgentOrchestrator   | |
  |   | - Delimiter Defense |   | - 64KB Hard Ceiling |   | - State Machine     | |
  |   | - Multi-Layer Norm  |   | - Secret Scrubbing  |   | - Optimistic Lock   | |
  |   +----------+----------+   +----------+----------+   +----------+----------+ |
  |              |                         |                         |            |
  +--------------|-------------------------|-------------------------|------------+
                 |                         |                         |
                 v                         v                         v
        [ ContextComposer ]       [ IToolExecutionPort ]     [ IAgentTaskStore ]
                 |                         |                         |
                 v                         v            +------------+------------+
        [ AIMemoryManager ]       [ Kernel / Local ]    |                         |
                 |                                      v                         v
        [ StorageConvStore ]                [ StorageAgentTaskStore ]   [ InMemoryTaskStore ]
        (limit / offset)                                |
                 |                                      v
                 +----------------------------> [ StorageEngine (DB) ]
===================================================================================================
```

### Agent State Transition Lifecycle:
```
           +----------------+
           |    PENDING     |
           +-------+--------+
                   |
                   v
           +----------------+
           |    RUNNING     | <----------------+
           +-------+--------+                  |
                   |                           |
         +---------+---------+                 |
         |                   |                 |
         v                   v                 |
+-----------------+   +----------------+       |
| PAUSED_FOR_APP. |   |   (Terminal)   |       |
+--------+--------+   | - COMPLETED    |       |
         |            | - FAILED       |       |
 (atomic | CAS)       | - TIMED_OUT    |       |
         v            | - LOOP_DET.    |       |
+-----------------+   | - STEP_LIMIT   |       |
|    RESUMING     |   +----------------+       |
+--------+--------+                            |
         |                                     |
         +-------------------------------------+
```

---

## 3. Detailed Component Specifications

### 3.1 M11.1 & M11.2 — Durable Agent Task Persistence & Concurrency
- **Protocol Port (`interfaces.py` / `agent.py`):**
  ```python
  @runtime_checkable
  class IAgentTaskStore(Protocol):
      async def save_task(self, task: AgentTask, record: PersistedAgentTaskRecord) -> None: ...
      async def get_task(self, task_id: str, tenant_id: str) -> PersistedAgentTaskRecord | None: ...
      async def update_task_status(
          self,
          task_id: str,
          tenant_id: str,
          expected_version: int,
          new_status: AgentStatus,
          steps: list[AgentStep] | None = None,
          pending_tool_calls: list[ToolCall] | None = None,
          resume_token: ResumeToken | None = None,
      ) -> PersistedAgentTaskRecord: ...
  ```
- **Persisted Record Model (`agent.py`):**
  ```python
  class PersistedAgentTaskRecord(BaseModel):
      model_config = ConfigDict(frozen=True)
      task: AgentTask
      status: AgentStatus
      current_step: int = 0
      steps: list[AgentStep] = Field(default_factory=list)
      pending_tool_calls: list[ToolCall] = Field(default_factory=list)
      resume_token: ResumeToken | None = None
      version: int = 1
      created_at: datetime.datetime
      updated_at: datetime.datetime
  ```
- **Durable Relational Storage Model (`persistence.py`):**
  `AIAgentTaskRow(BaseModel)` inside `persistence.py`:
  - `__tablename__ = "ai_agent_tasks"`
  - `tenant_id`: String(64)
  - `task_id`: String(64)
  - `status`: String(32)
  - `current_step`: Integer
  - `version`: Integer (for optimistic locking)
  - `task_json`: Text (serialized AgentTask)
  - `steps_json`: Text (serialized list[AgentStep])
  - `pending_calls_json`: Text (serialized list[ToolCall])
  - `resume_token_json`: Text (serialized ResumeToken)
  - Unique constraint & index on `(tenant_id, task_id)`
- **Concurrency Control & Atomic Resumption:**
  - `resume_task()` executes atomic CAS: `UPDATE ai_agent_tasks SET status = 'RESUMING', version = version + 1 WHERE tenant_id = :t AND task_id = :id AND status = 'PAUSED_FOR_APPROVAL' AND version = :v`.
  - Exactly one worker wins; the losing worker raises `AgentStateConflictError` and executes zero tool calls.

### 3.2 M11.3 — Prompt Boundary / Delimiter Defense
- **Normalization Strategy (`pipeline.py` & `memory.py`):**
  - Canonical delimiter neutralization handles:
    - Standard tags: `[[system]]`, `[[assistant]]`, `[[user]]`, `[[tool]]`, `[[knowledge]]`
    - Whitespace variations: `[ [system] ]`, `[  [system]]`, `[[ system ]]`
    - Case variations: `[[SYSTEM]]`, `[[System]]`, `[[Assistant]]`
    - Escaped / Unicode variants: `\[\[system\]\]`, fullwidth brackets `［［system］］`
  - Replaces all variants with neutralized `[ [safe_tag] ]` before rendering into `context_documents`.

### 3.3 M11.4 — Tool Output Bounding & Secret Scrubbing
- **Output Sizing & Scrubbing Pipeline (`tools.py`):**
  - Hard cap: `max_tool_result_bytes: int = 65536` (64 KiB).
  - Pipeline order:
    1. Tool executes across `IToolExecutionPort`.
    2. Normalize and serialize result to JSON/text.
    3. Apply recursive secret scrubbing on known patterns (`api_key`, `token`, `password`, `secret`, `bearer`, `credential`, `authorization`).
    4. Encode to UTF-8 and check byte length.
    5. If length > `max_tool_result_bytes`, truncate at safe UTF-8 character boundary.
    6. Attach structured metadata: `{"truncated": True, "original_bytes": N, "returned_bytes": M}`.

### 3.4 M11.5 — Conversation History Pagination Windowing
- **Pagination Query (`persistence.py` & `memory.py`):**
  - `StorageConversationStore.recent_turns(tenant_id, conversation_id, limit, offset=0)`.
  - SQL query applies `.offset(offset).limit(limit)`.
  - M10 `max_context_tokens` sliding window remains active downstream.

---

## 4. Security & Invariant Verification Matrix

| # | Invariant | Enforcement Mechanism | Verification Test |
|---|---|---|---|
| 1 | Tenant Isolation in Task Store | Storage queries always filter `(tenant_id, task_id)` | `test_cross_tenant_task_access_rejected` |
| 2 | Forged ResumeToken Rejection | Cryptographic HMAC-SHA256 signature verification | `test_forged_resume_token_rejected` |
| 3 | Concurrent Resume Race Prevention | Atomic optimistic lock (`status=PAUSED_FOR_APPROVAL` -> `RESUMING`) | `test_concurrent_resume_single_winner` |
| 4 | Restart & Crash Recovery | `StorageAgentTaskStore` state restored from DB | `test_paused_task_survives_process_restart` |
| 5 | Prompt Injection Neutralization | Comprehensive regex/sentinel normalization | `test_prompt_delimiter_injection_neutralized` |
| 6 | 64KB Tool Output Hard Bound | UTF-8 byte truncation with metadata | `test_tool_output_strictly_bounded_at_64kb` |
| 7 | Secret Redaction Before/After Truncation | Telemetry scrubber applied before byte truncation | `test_tool_secrets_redacted_across_truncation` |
| 8 | History Pagination & Windowing | SQL limit + offset bounded query | `test_conversation_history_pagination` |

---

## 5. Backward Compatibility & AST Quarantine

- **No Breaking Changes:** `AgentOrchestrator` defaults to `InMemoryAgentTaskStore()` if no store is supplied, preserving 100% compatibility with existing M7/M8/M9/M10 tests.
- **AST Quarantine Compliance:** Relational tables and SQLAlchemy dependencies stay strictly isolated inside `persistence.py`.
