# KORTEX OS — Process Intelligence Engine Implementation Specification

Version: 1.0.0 (Phase 5 Milestone)  
Status: RATIFIED SPECIFICATION  
Author: KORTEX AI Engineering / Chief Architect KASHAN

---

## 1. Executive Summary & Mission

The **Process Intelligence Engine** (`kortex.engines.process_intelligence`) provides business process execution telemetry, deterministic Directly-Follows Graph (DFG) mining, trace variant extraction, step bottleneck diagnostics, and throughput KPIs for KORTEX OS.

It operates strictly as an analytical read-projection engine over the platform's durable relational database (`IDataStore`). It possesses zero write paths into workflow runtime state, introduces zero database migrations, enforces structural multi-tenant isolation, and guarantees bounded resource consumption for local desktop environments.

---

## 2. Canonical Classification & Authority

| Architectural Element | Classification Authority | Rationale |
| :--- | :--- | :--- |
| `requires_execution_context=True` | **Security Architecture Requirement** (Commit 35822ae / Article 8) | Enforces trusted principal identity and tenant authority; eliminates caller parameter spoofing. |
| `workflow:read` permission | **Existing KORTEX Convention** | Standard authorization permission established in `WorkflowEngine` and `SecurityEngine`. |
| Zero Workflow Engine direct imports | **Architecture v1.0.0 Requirement** (Constitution / Clean Architecture) | Engines communicate through Capabilities; direct cross-engine coupling to internal ORM/engine classes is forbidden. |
| Local Core table descriptors (`tables.py`) | **Phase 5 Owner-Approved Decision** | Decouples read projections from Workflow ORM write-model implementation details. |
| 4 Public Capabilities (`summary.get`, etc.) | **Phase 5 Owner-Approved Decision** | Minimal, non-redundant capability surface covering process mining and execution telemetry. |
| Graph Bounding ($\le 100$ nodes, $\le 500$ edges) | **Phase 5 Owner-Approved Decision** | Mathematical guarantee preventing runaway graph complexity and UI render crashes. |
| Truthful Retry Semantics (unpersisted) | **Empirical Grounding / Implementation Detail** | StepEvaluator retries are in-memory only; reports unpersisted retry state truthfully as `None`. |
| Stable Cycle Time (`updated_at - created_at`)| **Empirical Grounding / Implementation Detail** | Workflow state machine guarantees `COMPLETED` instances are terminal and never modified again. |
| Human Approval Turnaround (`approval_requests`)| **Empirical Grounding / Implementation Detail** | Step-run latency measures dispatch pause; `approval_requests.updated_at - created_at` measures true human wait time. |
| 5.0s Application Operation Timeout | **Phase 5 Owner-Approved Decision** | `asyncio.wait_for` guard protecting callers and AI agents from unbounded query delays. |

---

## 3. Architecture & Engine Decoupling

```
┌────────────────────────────────────────────────────────┐
│                   Kernel Runtime                       │
└───────────────────────────┬────────────────────────────┘
                            │ Kernel.invoke_capability(CapabilityRequest)
                            ▼
┌────────────────────────────────────────────────────────┐
│             Kernel Capability Dispatcher               │
│ - Verifies session_token via SecurityEngine            │
│ - Authorizes "workflow:read" permission                │
│ - Injects immutable CapabilityExecutionContext         │
│ - Rejects reserved parameters                          │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│       ProcessIntelligenceEngine (BaseEngine)           │
│ - Resolves time window & handles clamping              │
│ - Binds execution_context.tenant_id to Repository      │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│      TenantScopedProcessAnalyticsRepository            │
│ - Queries local SQLAlchemy Core descriptors (tables.py)│
│ - Roots all SQL queries on workflow_instances.tenant_id│
│ - Executes via IDataStore.execute_in_transaction()     │
│ - Enforces 5.0s asyncio.wait_for operation timeout     │
└───────────────┬────────────────────────┬───────────────┘
                │                        │
                ▼                        ▼
┌───────────────────────────┐ ┌──────────────────────────┐
│       ProcessMiner        │ │     ProcessAnalyzer      │
│ - Deterministic DFG       │ │ - NIST linear percentiles│
│ - Bounded <=100 nodes     │ │ - Bottleneck ranking     │
│ - Bounded <=500 edges     │ │ - KPI calculations       │
│ - Trace variant extraction│ └──────────────────────────┘
└───────────────────────────┘
```

---

## 4. Capability Surface

All capabilities are registered under provider `"process_intelligence"`, require permission `workflow:read`, and enforce `requires_execution_context=True`:

1. **`kortex.process_intelligence.summary.get`**
   - Returns: `ProcessSummaryKPIs`
   - Parameters: `definition_id` (optional), `since` (optional), `until` (optional), `tenant_id` (optional validation parameter).
   - Semantics: Scalar SQL aggregation of total runs, completed, failed, cancelled, active, throughput per day, success/failure rate, and cycle time percentiles.
2. **`kortex.process_intelligence.bottlenecks.get`**
   - Returns: `BottlenecksResult`
   - Parameters: `definition_id` (optional), `since` (optional), `until` (optional), `limit` (default 20, max 50).
   - Semantics: Step-level performance ranked by p90 duration, failure count/rate, and correlated human approval wait times.
3. **`kortex.process_intelligence.process_graph.get`**
   - Returns: `ProcessGraph`
   - Parameters: `definition_id` (required), `version` (optional), `since` (optional), `until` (optional), `max_instances` (default 500, max 1000).
   - Semantics: Bounded directly-follows graph with transition probabilities and latencies.
4. **`kortex.process_intelligence.variants.list`**
   - Returns: `VariantListResult`
   - Parameters: `definition_id` (required), `version` (optional), `since` (optional), `until` (optional), `limit` (default 20, max 50).
   - Semantics: Distinct sequential execution paths ranked by frequency and percentage share.

---

## 5. Security & Structural Tenant Isolation

1. **Authoritative Context Binding:**
   - Authoritative identity is derived strictly from `execution_context.principal`.
   - Authoritative tenant is derived strictly from `execution_context.tenant_id`.
   - Any caller-supplied parameter `tenant_id` is compared: if non-null and mismatched, raises `AuthorizationDeniedError`.
2. **Structural Repository Boundary:**
   - The repository constructor requires `tenant_id: str`. Individual query methods do not take a caller-controlled tenant argument.
   - All SQL queries automatically include `WHERE workflow_instances.tenant_id == self._tenant_id`.
3. **Fail-Closed Unattended Execution:**
   - Unattended scheduled workflows lacking an authenticated session token fail closed at the `CapabilityDispatcher` with `AuthenticationError`.

---

## 6. Schema Projection & Compatibility Contract

Process Intelligence declares local SQLAlchemy Core `Table` objects in `tables.py` using engine-local metadata:
- `t_workflow_instances`: 8 projection columns (`id`, `tenant_id`, `definition_id`, `definition_version`, `state`, `status`, `created_at`, `updated_at`).
- `t_workflow_step_runs`: 7 projection columns (`id`, `instance_id`, `step_id`, `attempt`, `status`, `started_at`, `completed_at`).
- `t_approval_requests`: 7 projection columns (`id`, `tenant_id`, `instance_id`, `step_id`, `state`, `created_at`, `updated_at`).

An architecture test (`test_process_intelligence_architecture.py`) uses test-only reflection to ensure that every projection column matches the authoritative Workflow schema in existence, type, and nullability, catching any schema drift without requiring database migrations.

---

## 7. Retry Semantics

Empirical tracing of `WorkflowEngine._run_instance_steps` and `StepEvaluator` reveals that retries occur in an in-memory loop. The step run start is recorded once at `attempt=1`, and completion is updated once. Intermediate failed attempts are not written to disk.

- **Phase 5 Truth Invariant:** Process Intelligence does not fabricate retry counts. `StepBottleneck.retry_count` is set to `None` with an explicit docstring stating that retries are not independently persisted in the Phase 2 workflow ledger. Wall-clock duration accurately captures the full interval including retry delays.

---

## 8. Graph Bounding Algorithm

To prevent runaway graph sizes from crashing visualizers or overwhelming memory:
1. **Nodes Guarantee ($\le 100$):**
   - Reserves 4 virtual terminal nodes (`[START]`, `[END_SUCCESS]`, `[END_FAILED]`, `[END_CANCELLED]`).
   - Retains at most top 95 real step nodes ranked by visitation count descending (lexicographical step_id tie-breaker).
   - Collapses remaining steps into synthetic node `[__OTHER_STEPS__]`.
   - Guaranteed maximum: $4 + 95 + 1 = 100$.
2. **Edges Guarantee ($\le 500$):**
   - Remaps edges into collapsed nodes and aggregates weights.
   - If distinct edges $> 500$, ranks by transition count descending (source, target tie-breaker).
   - Retains top 499 edges and prunes low-frequency overflow edges.
   - Guaranteed maximum: $\le 500$.
3. **Probability Normalization:**
   - Recomputes outgoing transition probabilities over retained outbound edges:
     $$P(u \to v) = \frac{C(u, v)}{\sum_{w \in \text{retained\_outbound}(u)} C(u, w)}$$
     Ensuring $\sum P = 1.0$ for all nodes with outbound transitions.

---

## 9. KPI & Percentile Mathematics

1. **Cycle Time:** Evaluated for completed instances (`state == 'COMPLETED'`) as `(updated_at - created_at) * 1000.0`. Stable and terminal because completed state has no valid transitions.
2. **Percentiles:** Standard NIST Method 8 / NumPy linear interpolation:
   - Rank $R = \frac{P}{100} \times (N - 1)$, $k = \lfloor R \rfloor$, $d = R - k$.
   - $\text{value} = x_k + d \times (x_{k+1} - x_k)$.
   - Negative and null durations are discarded. Empty dataset returns 0.0. Results rounded to 2 decimal places.
3. **Human Approval Turnaround:** Computed from `approval_requests.updated_at - approval_requests.created_at` for decided tickets (`state IN ('APPROVED', 'REJECTED')`). Pending tickets are excluded from completed averages.

---

## 10. Resource Limits & Operation Timeout

1. **Time Window:** Default 30 days, maximum 90 days. Clamped with explicit response metadata: `"window_clamped": True`, `"effective_since": ...`.
2. **Sampling:** Instance caps of 1,000 instances for graph and variant queries, ordered deterministically by `created_at DESC, id DESC`. Scalar summary KPIs are complete SQL aggregations (never sampled).
3. **Operation Timeout:** 5.0 seconds enforced via `asyncio.wait_for`. Disclosed as an application-level operation timeout (not database statement cancellation). Raises `ProcessAnalyticsTimeoutError`.

---

## 11. Known Limitations

1. **In-Memory Retry Visibility:** Step-level retry counts cannot be reported until the Workflow Engine persists individual retry attempt records.
2. **SQLite Thread Cancellation:** In local SQLite, `asyncio.wait_for` unblocks the Python caller, but the SQLite synchronous thread executes until the current statement finishes. Indexing and `LIMIT 1000` ensure queries finish in < 10ms.
