# KORTEX OS — AI Orchestration Engine Implementation Specification

Status: Approved for Implementation
Version: 3.0.0
Authority: KORTEX OS Engineering Constitution & Phase 2 Architecture Design
Target Release: KORTEX OS Phase 2: Business Foundation
Target File: `docs/architecture/ai_orchestration_engine_implementation_spec.md`

Depends On:
- Phase 1 Foundation (Kernel Engine, Event Engine, Registry Engine, Configuration Engine)
- Storage Engine (`kortex.engines.storage`)
- Knowledge Engine (`kortex.engines.knowledge`)
- Security Engine (`kortex.engines.security`)
- Universal Shared Domain Models (`docs/architecture/shared_domain_models.md`)
- Platform Service Contracts (`docs/architecture/platform_service_contracts.md`)

---

## 1. Scope

The AI Orchestration Engine (`kortex.engines.ai`) is the central AI coordinator for KORTEX OS responsible for provider registration, model routing (local offline models and optional cloud models), prompt pipeline execution, context window management, memory integration, tool/capability invocation, and agent orchestration.

As defined in Article 13 of the KORTEX OS Engineering Constitution, **AI is an orchestrator**. AI plans, explains, and reviews, but **never bypasses the Kernel**, never accesses storage directly, and never executes business logic directly.

Phase 2 implementation scope:
1. **AI Provider Registry (`AIProviderRegistry`)**: Registry managing interchangeable local and cloud AI providers implementing a single unified provider abstraction (`BaseAIProvider`).
2. **Model Router (`ModelRouter`)**: Smart routing component selecting model providers based on task type, latency, cost, privacy level, and offline availability.
3. **Prompt Pipeline Engine (`PromptPipeline`)**: Sandboxed prompt template compiler, system instruction builder, and context injector.
4. **Context & Memory Manager (`AIMemoryManager`)**: Context window manager interfacing with Knowledge Engine for retrieval-augmented generation (RAG) and session history.
5. **Tool Invocation Handler (`AIToolInvoker`)**: Translates LLM function calls into canonical Kernel capability requests (`kortex.<domain>.<resource>.<action>`).
6. **Agent Orchestration Framework (`AgentOrchestrator`)**: Coordinates multi-agent planning loops, human-in-the-loop approval requests, and goal decomposition.
7. **AI Orchestration Engine Facade (`AIOrchestrationEngine`)**: Core facade inheriting `BaseEngine`, implementing capability handlers and diagnostic telemetry.
8. **Common Diagnostics Interface (`IEngineDiagnostics`)**: Implementation of standard diagnostics (`health()`, `metrics()`, `diagnostics()`, `status()`, `version()`, `capabilities()`).
9. **Storage Engine Integration**: Exclusive use of `StorageEngine` (`IDataStore`, `IFileStore`, `IObjectStore`, `ICacheStore`) for prompt templates, conversation history, and model metadata.

---

## 2. Out of Scope

1. **Direct System Execution**: AI models NEVER execute business operations directly. All operations are dispatched through Kernel capabilities (`kortex.<domain>.<resource>.<action>`).
2. **Direct Storage Access**: AI models NEVER access databases or files directly; all context retrieval flows through Knowledge Engine and Storage Engine.
3. **Hardcoded LLM Vendor Dependencies**: No component may depend on a specific LLM vendor. All providers implement `BaseAIProvider`.
4. **Bypassing Human Approval**: Critical business operations (salary payments, terminations, financial transfers) strictly require human approval regardless of AI planning.

---

## 3. Folder Structure

All source code strictly resides inside `backend/src/kortex/engines/ai/`:

```
backend/src/kortex/engines/ai/
├── __init__.py                # Package exports (AIOrchestrationEngine, models, interfaces)
├── engine.py                  # AIOrchestrationEngine core facade inheriting BaseEngine
├── interfaces.py              # Abstract interfaces (IAIOrchestrationEngine, IBaseAIProvider, etc.)
├── models.py                  # Pydantic v2 domain models, prompt schemas, and agent state
├── exceptions.py              # AI engine exception hierarchy
├── registry.py                # AIProviderRegistry for managing local and cloud providers
├── router.py                  # ModelRouter for task-based model selection
├── pipeline.py                # PromptPipeline for context assembly and system instructions
├── memory.py                  # AIMemoryManager for RAG integration and session history
├── tools.py                   # AIToolInvoker for translating LLM function calls to capabilities
├── agents.py                  # AgentOrchestrator for multi-agent loops and human approvals
├── base_provider.py           # BaseAIProvider abstract base class
├── diagnostics.py             # Common Diagnostics Interface (IEngineDiagnostics)
├── events.py                  # Immutable event payload definitions
└── providers/
    ├── __init__.py            # Provider package marker
    └── dummy_provider.py      # Reference AI provider implementation

backend/tests/unit/
├── test_ai_models.py                 # Unit tests for prompt schemas and agent state
├── test_ai_provider_registry.py      # Unit tests for provider registration
├── test_model_router.py              # Unit tests for model selection logic
├── test_prompt_pipeline.py           # Unit tests for prompt assembly
├── test_ai_tool_invoker.py           # Unit tests for capability tool translation
├── test_agent_orchestrator.py        # Unit tests for agent planning loops
├── test_dummy_provider.py            # Unit tests for DummyAIProvider execution
├── test_ai_diagnostics.py            # Unit tests for IEngineDiagnostics methods
└── test_ai_engine.py                 # Unit tests for core AIOrchestrationEngine facade

backend/tests/integration/
└── test_ai_engine_integration.py     # Integration tests with Kernel, Storage, Knowledge & Event Engine
```

---

## 4. Interfaces

- `IAIOrchestrationEngine`: Primary facade interface (`generate_response`, `orchestrate_agent`, `invoke_tool`, `register_provider`).
- `IBaseAIProvider`: Abstract base class for providers (`provider_id`, `is_local`, `supported_models`, `generate_text`, `generate_embeddings`).
- `IModelRouter`: Model selection protocol (`select_model`).
- `IAIMemoryManager`: Memory and RAG context retrieval protocol.
- `IAIToolInvoker`: Kernel capability tool translation protocol.

---

## 5. Models

- `AIProviderMetadata`: Model (`provider_id`, `display_name`, `vendor`, `is_local`, `supported_capabilities`).
- `LLMRequest`: Model (`request_id`, `prompt`, `system_instruction`, `context_documents`, `tools`, `temperature`, `max_tokens`).
- `LLMResponse`: Model (`request_id`, `text_content`, `tool_calls`, `token_usage`, `execution_time_ms`).
- `AgentTask`: Model (`task_id`, `goal`, `agent_role`, `status`, `history`, `human_approval_required`).
- `ToolDefinition`: Translates `UniversalCapabilityMetadata` into LLM function schema.

---

## 6. Provider Registry (`AIProviderRegistry`)

Thread-safe registry for registering and looking up local and cloud AI providers implementing `BaseAIProvider`.

---

## 7. Local Models

Offline-first local models operating strictly on local compute hardware without internet connectivity, declared with `is_local = True`.

---

## 8. Cloud Models

Optional cloud LLM providers configured via secret handles in Security Engine, invoked only when internet is available and task classification permits cloud processing (`export_restricted = False`).

---

## 9. Routing (`ModelRouter`)

Selects the optimal provider and model based on:
- Privacy level (`UniversalClassification`).
- Connectivity status (forces local models when offline).
- Task requirement (text generation, coding, embedding, planning).

---

## 10. Context Management (`AIMemoryManager`)

Manages token context windows, truncating long histories intelligently and assembling relevant context payloads.

---

## 11. Memory Integration

Interfaces with Knowledge Engine (`kortex.engines.knowledge`) to execute RAG (Retrieval-Augmented Generation) queries, injecting relevant knowledge graph nodes into the prompt pipeline.

---

## 12. Prompt Pipeline (`PromptPipeline`)

Assembles system instructions, user prompts, declarative templates, and context documents into secure, injection-resistant LLM prompt payloads.

---

## 13. Tool Invocation (`AIToolInvoker`)

Translates LLM function call requests into canonical `CapabilityRequest` objects, enforcing Kernel RBAC permissions before capability dispatch.

---

## 14. Agent Orchestration (`AgentOrchestrator`)

Coordinates agent planning loops, goal decomposition, step execution, and human approval pauses for critical actions.

---

## 15. Capability Registration

Canonical capabilities:
- `kortex.ai.response.generate`
- `kortex.ai.agent.orchestrate`
- `kortex.ai.provider.register`
- `kortex.ai.provider.list`

---

## 16. Event Integration

Emits immutable events to Event Engine:
- `AIGenerationStartedEvent` (`ai.generation.started`)
- `AIGenerationCompletedEvent` (`ai.generation.completed`)
- `AIToolInvokedEvent` (`ai.tool.invoked`)
- `AgentTaskCompletedEvent` (`ai.agent.completed`)

---

## 17. Performance

- Local model invocation non-blocking via `async`/`await`.
- Prompt assembly overhead $\le$ 20ms.
- Cached context embeddings in `ICacheStore`.

---

## 18. Security

- Privacy boundary enforcement: restricted data (`CONFIDENTIAL`, `RESTRICTED`) NEVER sent to cloud providers.
- Tool invocation strictly checked by Kernel authorization middleware.
- Secrets referenced via Security Engine handles only.

---

## 19. Acceptance Criteria

- ✓ **Architecture Compliant**: Inherits `BaseEngine`, implements `IEngineDiagnostics`, complies with Article 13 of Constitution.
- ✓ **Local-First & AI Interchangeable**: Operates offline with local providers; all providers implement `BaseAIProvider`.
- ✓ **Kernel Authority Preserved**: AI never bypasses Kernel or storage; tool calls execute via canonical capabilities.
- ✓ **Storage Engine Only**: Persistence flows exclusively through `StorageEngine`.
- ✓ **Tests $\ge$ 90%**: Coverage threshold met across all core files.
