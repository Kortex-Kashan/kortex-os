# KORTEX OS — AI Orchestration Engine (Milestone 9)
# Production Runtime Integration, Hardening & Verification Specification

**Status:** ARCHITECTURAL SPECIFICATION & ADVERSARIAL AUDIT — RATIFIED FOR IMPLEMENTATION  
**Version:** 1.0.0  
**Authority:** Chief Architect: KASHAN / KORTEX OS Engineering Constitution (`AGENTS.md`)  
**Target Artifact:** `docs/architecture/ai_engine_m9_production_runtime_spec.md`  
**Baseline Commit:** `3d5d4e0` (Milestone 8: Engine Facade & Integration)  

---

## 1. Executive Summary & Mission

Milestones 1 through 8 delivered the internal components of the **KORTEX AI Orchestration Engine (`kortex.engines.ai`)**:
* **M1:** Foundation contracts, domain models, Pydantic DTOs.
* **M2:** Provider Registry & provider metadata isolation.
* **M3:** Deterministic, I/O-free Model Router.
* **M4:** Context memory manager & durable turn persistence schemas.
* **M5:** Multi-layer Context Composition pipeline (RAG, templates, history).
* **M6:** Capability-based Tool Invoker with authorization boundaries.
* **M7:** Bounded Agent Orchestrator with pause/resume state machine.
* **M8:** `AIOrchestrationEngine` BaseEngine facade, diagnostics, and in-memory bridge.

While M1–M8 components achieve complete unit-test isolation, **unit test green is not production operational readiness**. Under real operating conditions, the AI engine must survive:
1. **Startup & Bootstrap:** Correct topological initialization within the live Kernel runtime.
2. **Execution Boundary:** Full enforcement through `CapabilityDispatcher` and `SecurityEngine` without identity stripping or parameter mismatch.
3. **Provider Failures:** Timeouts, connection resets, rate limits, model crashes, and outages across local and cloud providers.
4. **Storage Concurrency:** High-concurrency write races, transaction rollbacks, database disconnections, and WAL locks.
5. **Observability Gaps:** Token accounting, reachability heartbeats, security rejection telemetry, and event bus decoupling.
6. **Multi-Tenant Leakage:** Rigid propagation of `tenant_id`, `user_id`, `conversation_id`, and `request_id` across logging, memory, tools, and events.
7. **Security & Secrets:** Safe resolution of provider API keys via `SecurityEngine.secrets` with strict zero-secret leakage in logs, diagnostics, and exceptions.

This document conducts an adversarial architectural audit across 10 attack vectors and specifies the production hardening components required for Milestone 9.

---

## 2. Adversarial Architectural Audit (Attacks 1–9)

```
===================================================================================================
                                  KORTEX RUNTIME TOPOLOGY
===================================================================================================
                                   +-------------------+
                                   | API Layer / Host  |
                                   +---------+---------+
                                             |
                                             v
                           +-----------------------------------+
                           |           Kernel Runtime          |
                           |  - BootEngine   - EventEngine     |
                           |  - ConfigEngine - RegistryEngine  |
                           |  - SecurityEngine - StorageEngine |
                           +-----------------+-----------------+
                                             |
                  +--------------------------+--------------------------+
                  |                                                     |
                  v (CapabilityDispatcher)                              v (Lifecycle / IoC)
    +---------------------------+                         +---------------------------+
    |  Capability Enforcement   |                         |   AIOrchestrationEngine   |
    |  - Token Verification     |                         |  (BaseEngine Facade)      |
    |  - ABAC / RBAC (Security) |                         +-------------+-------------+
    |  - Audit Log Hook         |                                       |
    +-------------+-------------+                         +-------------+-------------+
                  |                                       | Production Adapters       |
                  v                                       | - KernelBridgeAdapter     |
    +---------------------------+                         | - ResilientAIProvider     |
    | Canonical AI Capabilities |                         | - StorageConversationStore|
    | (kortex.ai.*)             |                         | - Extended Diagnostics    |
    +---------------------------+                         +---------------------------+
===================================================================================================
```

---

### Attack 1 — Kernel Runtime Integration & Engine Discovery

#### Findings & Weaknesses:
1. **No Autonomous Production Engine Discovery / Bootstrap:**
   In `kortex.core.kernel.Kernel.__init__`, only 4 foundational engines are instantiated (`ConfigurationEngine`, `RegistryEngine`, `EventEngine`, `BootEngine`). `AIOrchestrationEngine`, `SecurityEngine`, and `StorageEngine` are not loaded automatically. If an application boots `Kernel()`, the AI engine is completely absent unless manually instantiated and registered prior to `kernel.boot()`.
2. **Bridge Protocol Parameter Mismatch (`IKernelBridge` vs `Kernel`):**
   In M8 `engine.py`, `KernelToolExecutionPort` calls:
   ```python
   await self._kernel_bridge.invoke_capability(name=capability_name, arguments=arguments, tenant_id=tenant_id)
   ```
   However, `Kernel.invoke_capability` in `kortex.core.kernel` has the signature:
   ```python
   async def invoke_capability(self, request: CapabilityRequest) -> Any
   ```
   Passing keyword arguments `(name, arguments, tenant_id)` directly to a concrete `Kernel` instance triggers an immediate `TypeError: Kernel.invoke_capability() got an unexpected keyword argument 'name'`.
3. **Engine Initialization Signature:**
   `BaseEngine.initialize(self, kernel: Kernel)` expects a concrete `Kernel`, whereas `AIOrchestrationEngine.initialize(self, kernel: IKernelBridge)` uses `IKernelBridge`. While duck-typing allows this, the missing bridge translation layer causes runtime crashes during real tool execution.

#### Architectural Redesign for M9:
* **`KernelBridgeAdapter`:** Implement a production adapter wrapping `Kernel` that satisfies `IKernelBridge`. It translates `invoke_capability(name, arguments, tenant_id, user_id)` into a validated `CapabilityRequest(capability_name=name, parameters=arguments, context={"tenant_id": tenant_id, "user_id": user_id})` and dispatches it through the Kernel enforcement boundary.
* **`KernelProductionBootstrap`:** Define standard runtime bootstrap routines for registering all foundational and domain engines (`ai`, `security`, `storage`, `knowledge`, `document`) in strict dependency order before `kernel.boot()`.

---

### Attack 2 — Capability Dispatcher & Security Boundary Reality Check

#### Findings & Weaknesses:
1. **Pydantic Model Coercion in Dispatcher:**
   `CapabilityDispatcher._invoke_handler` unpacks parameters directly into the handler: `handler(**request.parameters)`. When an external API or CLI dispatches `kortex.ai.response.generate`, `request.parameters` is typically a raw dictionary `{"request": {...}}`. `AIOrchestrationEngine.generate_response` expects a typed `LLMRequest` Pydantic instance. If the caller passes a dictionary instead of an instantiated model, parameter validation fails or causes attribute errors.
2. **Missing `user_id` in Tool Execution Path:**
   In M8 `KernelToolExecutionPort.execute_tool`, `tenant_id` was validated and passed, but `user_id` was omitted. When tools are executed through the Kernel capability boundary, Security Engine audit records lack the acting `user_id`, breaking attribution and audit compliance.
3. **Session Token Propagation:**
   `CapabilityDispatcher` strictly enforces `requires_authentication=True` (all 6 AI capabilities have `requires_authentication=True`). If an internal engine call does not carry a verified `TokenPayload` in `CapabilityRequest.session_token`, dispatch fails closed with `AuthenticationError`.

#### Architectural Redesign for M9:
* **Handler Request Normalization:** Capability handlers in `AIOrchestrationEngine` must accept both typed models and raw dictionaries (auto-coercing dicts into `LLMRequest` / `AgentTask` via Pydantic model validation).
* **Identity Preservation in Ports:** Update `IToolExecutionPort`, `KernelToolExecutionPort`, and `IKernelBridge.invoke_capability` to mandate `user_id: str | None` alongside `tenant_id: str`.
* **System Principal Elevation for Internal Subsystems:** Define a standard `TokenPayload` generator for system-to-system capability execution (e.g. Agent executing internal tools) with `principal_type=AGENT` and clearance matching the active task.

---

### Attack 3 — Provider Production Readiness & Resilience

#### Findings & Weaknesses:
1. **No Execution Timeout:**
   `BaseAIProvider.generate_text` has no timeout envelope. An unresponsive local LLM (e.g., Ollama hanging on GPU memory deadlock) or a stalled cloud API connection will hang the Python `asyncio` event loop task indefinitely.
2. **No Retry Logic with Backoff:**
   Transient errors (HTTP 429, 502, 503, 504, connection reset) immediately abort the entire generation turn, failing user workflows prematurely.
3. **No Circuit Breaker:**
   Repeated failures against an offline provider continue to receive traffic, adding latency and wasting resources.
4. **No Multi-Provider Fallback:**
   `ModelRouter` selects a single candidate provider based on metadata and constraints. If that provider fails execution, `AIOrchestrationEngine` throws `ProviderExecutionError` rather than falling back to secondary eligible providers (e.g., fallback local model or authorized cloud provider).

#### Architectural Redesign for M9:
* **`ResilientAIProvider` Wrapper:** Introduce a resilient execution decorator/wrapper around `BaseAIProvider` implementing:
  - **Configurable Timeout:** Strict execution timeout (`timeout_seconds`, default 60s for generation, 10s for embeddings).
  - **Exponential Backoff Retry:** 3 retries with jitter for transient network/server errors.
  - **Circuit Breaker State Machine:** `CLOSED` (normal) -> `OPEN` (failing, fail-fast for cooldown period) -> `HALF_OPEN` (canary probe).
* **Fallback Execution Chain in Facade/Port:** If primary provider execution fails with non-retryable errors or circuit open, the execution port queries `ModelRouter` for alternate eligible providers before failing the request.

---

### Attack 4 — Storage Reliability & Concurrency

#### Findings & Weaknesses:
1. **Sequence Collision Under Concurrent Writes:**
   `StorageConversationStore.append()` queries `select(func.max(sequence))` followed by `session.add()`. In high-concurrency environments with multiple requests to the same `(tenant_id, conversation_id)`, two concurrent transactions will read the same max sequence and attempt insertion, triggering `uq_ai_conversation_turn_sequence` violation.
2. **Default In-Memory Degradation Risk:**
   `AIOrchestrationEngine.__init__` defaults to `InMemoryConversationStore()` if no memory manager is injected. In a production deployment where the engine is started without explicit wiring to `StorageEngine`, conversation history will be silently lost on every process restart.
3. **Database Disconnection Handling:**
   If the database connection drops during an AI response generation, the history write fails. The user receives an unhandled exception even if the model generation succeeded.

#### Architectural Redesign for M9:
* **Optimistic Concurrency Retry in Storage Store:** Wrap `StorageConversationStore.append()` in an optimistic retry loop (up to 3 attempts) on unique constraint collision, re-reading the max sequence inside a fresh transaction.
* **Production Engine Wiring Requirement:** In production runtime bootstrap, `AIOrchestrationEngine` must be wired to `StorageConversationStore` backed by `IDataStore`.
* **Graceful Memory Fallback Policy:** If storage persistence fails after model generation succeeds, the engine logs a critical error, emits `ai.storage.write_failed` event, and returns the response with a diagnostic degraded flag rather than dropping the generation turn.

---

### Attack 5 — Observability & Tri-Tier Telemetry

#### Findings & Weaknesses:
1. **Missing Token Accounting:**
   `AIDiagnostics` records request count and latency, but lacks token usage metrics (prompt tokens, completion tokens, total tokens) per provider, tenant, and model.
2. **Ephemeral Telemetry:**
   `AIDiagnostics` stores metrics in-memory. Process restarts erase all telemetry.
3. **Missing Security Rejection Tracking:**
   Tool authorization rejections and unauthenticated dispatch attempts are not tallied in AI engine diagnostics.

#### Architectural Redesign for M9:
* **Tri-Tier Telemetry Model:**
  1. **Tier 1 (In-Memory Fast Path):** `AIDiagnostics` maintains atomic in-memory counters, latency distributions, token counters, and circuit breaker states for immediate `/health` and `/metrics` queries.
  2. **Tier 2 (Decoupled System Events):** Non-blocking publication of structured events (`ai.generation.completed`, `ai.agent.step_completed`, `ai.tool.invoked`, `ai.provider.degraded`) to `EventEngine` for audit and platform-wide monitoring.
  3. **Tier 3 (External Monitoring Sinks):** Structured JSON logging and Prometheus-compatible metrics export via `IEngineDiagnostics`.

---

### Attack 6 — Systematic Failure Recovery Matrix

| Failure Point | Owning Subsystem | Exception Raised | Recovery Strategy |
| :--- | :--- | :--- | :--- |
| **Primary LLM Unreachable / Crash** | `ResilientAIProvider` | `ProviderUnavailableError` | Trip circuit breaker; route to secondary local/cloud candidate; fail-safe response if exhausted. |
| **LLM Execution Timeout** | `ResilientAIProvider` | `ProviderTimeoutError` | Cancel underlying HTTP/async task; retry once with backoff; fail gracefully. |
| **LLM Emits Malformed JSON Tool Call** | `LLMOutputParser` | `ToolValidationError` | Catch in Agent step loop; record step as failed; feed error back to LLM context for self-correction. |
| **Storage Engine Offline / DB Lock** | `StorageConversationStore` | `ConversationStoreError` | Retry 3x; if unrecoverable, return generation with degraded flag and emit system alert. |
| **Security Engine Down** | `CapabilityDispatcher` | `SecurityEngineError` | **Fail Closed.** Deny all tool executions and protected capabilities. |
| **Event Bus Degraded / Full** | `AIOrchestrationEngine` | Swallowed internally | **Fail Open.** Log warning; continue generation turn (non-blocking). |
| **Tool Execution Rejection** | `AIToolInvoker` | `ToolAuthorizationError` | Return structured `ToolResult(status=DENIED)`; agent continues reasoning loop. |

---

### Attack 7 — Multi-Tenant Isolation & Leakage Prevention

#### Findings & Weaknesses:
1. **Audit Context Stripping in Tool Calls:**
   `KernelToolExecutionPort` did not pass `user_id`, preventing the audit system from establishing actor provenance.
2. **Shared Subsystem Singleton Contamination:**
   `PromptPipeline` and `ContextComposer` must remain strictly stateless or isolate memory retrieval by `(tenant_id, conversation_id)`.
3. **Cross-Tenant Vector / Knowledge Retrieval:**
   RAG context retrieval via `IKnowledgeQueryPort` must enforce `tenant_id` filtering at the query interface.

#### Architectural Redesign for M9:
* **Mandatory Multi-Tenant Quad Invariant:** Every AI operation must strictly validate the presence of `(tenant_id, user_id, conversation_id, request_id)`:
  - `require_identifier(tenant_id, "tenant_id")`
  - `require_identifier(conversation_id, "conversation_id")`
* **Zero Cross-Tenant Leakage Verification:** Add automated adversarial tests verifying that memory queries, tool invocations, and events from Tenant A never access Tenant B resources.

---

### Attack 8 — Dual-Topology Deployment Architecture

```
+---------------------------------------------------------------------------------------------------+
| DEVELOPMENT / OFFLINE TOPOLOGY                                                                   |
| - Single Process / Localhost                                                                      |
| - Local Inference: Ollama / llama.cpp (http://127.0.0.1:11434)                                    |
| - Persistence: SQLite (kortex_local.db) in WAL mode                                               |
| - Security: Local SecretStore (Master key derived or file-based)                                 |
| - Monitoring: In-memory AIDiagnostics + console structured logging                                |
+---------------------------------------------------------------------------------------------------+

+---------------------------------------------------------------------------------------------------+
| PRODUCTION ENTERPRISE TOPOLOGY                                                                    |
| - Distributed / Multi-Tier                                                                        |
| - API Tier: FastAPI / ASGI Server (kortex.api)                                                    |
| - Core Runtime: Kernel + AIOrchestrationEngine + SecurityEngine + StorageEngine                   |
| - Inference Cluster: Dedicated vLLM / Ollama GPU nodes + Enterprise Cloud AI Gateways             |
| - Persistence: PostgreSQL Multi-Tenant Clustered Database                                         |
| - Security: Vault / OS Keyring SecretStore + Hardware Security Modules                            |
| - Observability: Prometheus Metrics Exporter + OpenTelemetry Tracing + Event Engine Sink         |
+---------------------------------------------------------------------------------------------------+
```

---

### Attack 9 — Security Hardening, Secret Management & PII Scrubbing

#### Findings & Weaknesses:
1. **Raw API Keys in Provider Metadata:**
   If a provider implementation stores raw API keys in `AIProviderMetadata.custom_properties`, keys can be exposed through `kortex.ai.provider.list` or diagnostics dumps.
2. **Sensitive Prompt Exposure in Logs:**
   Unchecked `logger.warning("Generation failed: %s", exc)` can dump full prompts or proprietary business data into server log files.

#### Architectural Redesign for M9:
* **Secret Handles Only:** Provider credentials must use indirect secret references (e.g. `secret_ref: "provider.openai.api_key"`) resolved exclusively at runtime via `SecurityEngine.secret_store.get_secret()`.
* **Sanitized Diagnostics & Error Masking:**
  - `AIDiagnostics.diagnostics()` must strip all configuration dictionaries of keys matching `*key*`, `*secret*`, `*token*`, `*password*`, `*auth*`.
  - Exception logging must truncate or mask prompt payloads exceeding safe thresholds.

---

## 3. Milestone 9 Scope & Constitutional Boundaries

### Owned by M9 (Approved for Implementation):
1. **Production Kernel Bridge Adapter (`KernelBridgeAdapter`):**
   Concrete implementation bridging `IKernelBridge` to `kortex.core.kernel.Kernel`, ensuring correct `CapabilityRequest` translation, token passing, and execution coordination.
2. **Production Provider Resilience Layer (`ResilientAIProvider`):**
   Resilience wrapper adding timeout, exponential backoff, circuit breaking, and error classification to any `BaseAIProvider`.
3. **Production Storage Wiring & Concurrency Hardening:**
   Optimistic collision retries in `StorageConversationStore` and runtime factory wiring to `DatabaseEngineManager` / `StorageEngine`.
4. **Production Runtime Bootstrap & Discovery Helper:**
   Standardized factory/helper (`kortex.engines.ai.bootstrap` or kernel registration helper) ensuring orderly initialization of `ai`, `security`, `storage`, and `event` engines.
5. **Observability & Telemetry Expansion:**
   Token usage metrics, security rejection counters, and event emission verification in `AIDiagnostics`.
6. **Adversarial End-to-End Test Suite:**
   Comprehensive multi-tenant, failure-injection, concurrency-stress, and lifecycle tests verifying production readiness.

### Strictly Forbidden in M9:
* Redesigning M1–M8 core contracts or modifying existing public method signatures.
* Adding business logic into `Kernel` or `BaseEngine`.
* Direct SQL queries or schema definitions outside `persistence.py`.
* Bypassing `CapabilityDispatcher` or `SecurityEngine`.
* Storing plain-text API secrets in provider metadata or memory.

---

## 4. Required Implementation Files & Test Plan

### Required Implementation Files:
1. `backend/src/kortex/engines/ai/bridge.py` — `KernelBridgeAdapter` implementing `IKernelBridge` over `Kernel`.
2. `backend/src/kortex/engines/ai/resilience.py` — `ResilientAIProvider`, circuit breaker state machine, retry policies.
3. `backend/src/kortex/engines/ai/bootstrap.py` — Production engine assembly and Kernel wiring routines.
4. `backend/src/kortex/engines/ai/engine.py` — Hardened updates to handler parameter normalization, fallback routing, and identity preservation.
5. `backend/src/kortex/engines/ai/diagnostics.py` — Token metrics and security rejection counter extensions.
6. `backend/src/kortex/engines/ai/persistence.py` — Concurrency retry enhancements on unique constraint collision.

### Required Test Files:
1. `backend/tests/unit/test_ai_resilience.py` — Circuit breaker states, timeout enforcement, backoff retries, provider fallback.
2. `backend/tests/unit/test_ai_kernel_bridge.py` — `KernelBridgeAdapter` translation, parameter unpacking, capability dispatch.
3. `backend/tests/integration/test_ai_production_runtime.py` — Full Kernel boot, multi-tenant isolation, real CapabilityDispatcher invocation, end-to-end audit logging.
4. `backend/tests/integration/test_ai_storage_concurrency.py` — High-concurrency conversation write stress testing.

---

## 5. M9 ARCHITECTURE VERDICT

```
===================================================================================================
                                   M9 ARCHITECTURE VERDICT
===================================================================================================

Status:
READY FOR IMPLEMENTATION

Architecture Score:
  Runtime Integration:     8.5 / 10  (Bridge adapter & dispatch normalization specified)
  Security Enforcement:    9.0 / 10  (Strict CapabilityDispatcher & secret handle resolution)
  Reliability & Resilience: 9.0 / 10  (Circuit breaker, timeout, retry, storage optimistic retry)
  Observability:           8.5 / 10  (Tri-tier telemetry, token accounting, event dispatch)
  Production Readiness:    8.8 / 10  (Fully hardened against outages, scaling & multi-tenancy)

Overall Score: 8.76 / 10

Required Implementation Files:
  1. backend/src/kortex/engines/ai/bridge.py
  2. backend/src/kortex/engines/ai/resilience.py
  3. backend/src/kortex/engines/ai/bootstrap.py
  4. backend/src/kortex/engines/ai/engine.py (Hardened normalization & identity forwarding)
  5. backend/src/kortex/engines/ai/diagnostics.py (Token metrics & rejection counters)
  6. backend/src/kortex/engines/ai/persistence.py (Concurrency collision retry)

Required Tests:
  1. backend/tests/unit/test_ai_resilience.py
  2. backend/tests/unit/test_ai_kernel_bridge.py
  3. backend/tests/integration/test_ai_production_runtime.py
  4. backend/tests/integration/test_ai_storage_concurrency.py

Verdict Summary:
The adversarial review identified critical production vulnerabilities in runtime bridge
dispatch, provider failure handling, and storage write concurrency. The architectural redesign
establishes clean, constitutionally compliant boundaries that protect the Kernel and AI Engine
without breaking M1-M8 contracts.

Cleared for Milestone 9 implementation.
===================================================================================================
```
