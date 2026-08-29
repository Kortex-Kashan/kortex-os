"""Unit & adversarial tests for KORTEX AI Orchestration Engine Facade (Milestone 8).

Tests adhere strictly to the ratified M8 specification:
- BaseEngine lifecycle state machine and transitions
- Kernel capability registration (6 canonical capabilities)
- Decoupled IKernelBridge interaction
- End-to-end generate_response, orchestrate_agent, resume_agent, invoke_tool
- Non-blocking event dispatch
- AST import quarantine (0 forbidden imports)
- Mutation probes (event error tolerance, no direct SQL, no duplicate context composition)
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kortex.core.base_engine import EngineState
from kortex.core.exceptions import EngineStateError
from kortex.engines.ai.agent import (
    AgentOrchestrator,
    AgentStatus,
    AgentTask,
    AlwaysApprovePolicy,
    AlwaysDenyPolicy,
    InMemoryAgentContextPort,
    InMemoryAgentTaskStore,
    InMemoryLLMExecutionPort,
    PersistedAgentTaskRecord,
)
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.diagnostics import CANONICAL_CAPABILITIES, AIDiagnostics
from kortex.engines.ai.engine import (
    AIOrchestrationEngine,
    KernelToolExecutionPort,
    RouterLLMExecutionPort,
)
from kortex.engines.ai.exceptions import (
    ConversationStoreError,
    MemoryValidationError,
    NoRoutableProviderError,
    ProviderFallbackExhaustedError,
    TransientProviderError,
)
from kortex.engines.ai.interfaces import IKernelBridge
from kortex.engines.ai.memory import AIMemoryManager, ConversationTurn, InMemoryConversationStore
from kortex.engines.ai.models import (
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
)
from kortex.engines.ai.pipeline import ContextComposer, PromptPipeline
from kortex.engines.ai.registry import MetadataOnlyAIProvider, ProviderRegistry
from kortex.engines.ai.retrieval import InMemoryKnowledgeQueryPort, RetrievedDocument
from kortex.engines.ai.router import ModelRouter
from kortex.engines.ai.tools import (
    AIToolInvoker,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
)

# ---------------------------------------------------------------------------
# Test Fakes & Helpers
# ---------------------------------------------------------------------------


class InMemoryKernelBridge(IKernelBridge):
    """In-memory reference fake for IKernelBridge to test engine lifecycle without live Kernel."""

    def __init__(self) -> None:
        self.capabilities: dict[str, dict[str, Any]] = {}
        self.events_published: list[tuple[str, dict[str, Any] | None, str]] = []
        self.invocations: list[tuple[str, dict[str, Any], str]] = []
        self.should_fail_events: bool = False

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Callable[..., Any] | None = None,
        parameters_schema: dict[str, Any] | None = None,
        returns_schema: dict[str, Any] | None = None,
        required_permissions: list[str] | None = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
    ) -> object:
        self.capabilities[name] = {
            "description": description,
            "provider": provider,
            "handler": handler,
            "required_permissions": required_permissions,
            "requires_authentication": requires_authentication,
            "security_classification": security_classification,
        }

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
        sender: str = "ai",
    ) -> object:
        if self.should_fail_events:
            raise RuntimeError("Event engine failure simulation.")
        self.events_published.append((topic, payload, sender))

    async def invoke_capability(
        self,
        name: str,
        arguments: dict[str, Any],
        tenant_id: str,
        user_id: str | None = None,
    ) -> object:
        self.invocations.append((name, arguments, tenant_id))
        return {"status": "success", "echo": arguments}


class DummyExecutingProvider(BaseAIProvider):
    """Reference executing AI provider for unit testing."""

    def __init__(self, provider_id: str = "dummy-provider", response_text: str = "Hello from AI") -> None:
        self._provider_id = provider_id
        self._response_text = response_text
        self._metadata = AIProviderMetadata(
            provider_id=provider_id,
            display_name="Dummy Provider",
            vendor="TestVendor",
            endpoint_type="local_host",
            supported_models=["dummy-model"],
        )

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    @property
    def supported_models(self) -> list[str]:
        return list(self._metadata.supported_models)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content=self._response_text,
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            execution_time_ms=12.5,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def health_check(self) -> bool:
        return True


def _make_engine(
    *,
    provider: BaseAIProvider | None = None,
    knowledge_docs: list[str] | None = None,
) -> tuple[AIOrchestrationEngine, InMemoryKernelBridge]:
    registry = ProviderRegistry()
    active_provider = provider or DummyExecutingProvider()
    registry.register(active_provider)

    router = ModelRouter(registry=registry)
    store = InMemoryConversationStore()
    memory = AIMemoryManager(store=store)

    knowledge_port = InMemoryKnowledgeQueryPort(
        documents=[RetrievedDocument(content=doc) for doc in (knowledge_docs or [])]
    )
    composer = ContextComposer(
        memory=memory,
        knowledge=knowledge_port,
        pipeline=PromptPipeline(),
    )

    tools = ToolRegistry()
    tools.register_tool(
        ToolDefinition(
            name="get_weather",
            description="Get weather for a location",
            canonical_capability="kortex.weather.get",
            parameters_schema={"type": "object", "properties": {"city": {"type": "string"}}},
            is_mutation=False,
        )
    )
    tools.register_tool(
        ToolDefinition(
            name="create_order",
            description="Create a purchase order",
            canonical_capability="kortex.order.create",
            parameters_schema={"type": "object", "properties": {"item": {"type": "string"}}},
            is_mutation=True,
        )
    )

    kernel_bridge = InMemoryKernelBridge()
    tool_port = KernelToolExecutionPort(kernel_bridge=kernel_bridge)
    tool_invoker = AIToolInvoker(registry=tools, execution_port=tool_port)

    diag = AIDiagnostics(
        provider_registry=registry,
        model_router=router,
        memory_manager=memory,
        tool_registry=tools,
    )

    engine = AIOrchestrationEngine(
        provider_registry=registry,
        model_router=router,
        memory_manager=memory,
        context_composer=composer,
        tool_invoker=tool_invoker,
        tool_registry=tools,
        diagnostics=diag,
    )

    return engine, kernel_bridge


# ---------------------------------------------------------------------------
# §1 — BaseEngine Lifecycle & State Machine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_lifecycle_happy_path() -> None:
    engine, kernel = _make_engine()
    assert engine.state == EngineState.UNINITIALIZED

    # 1. Initialize
    await engine.initialize(kernel)
    assert engine.state == EngineState.READY

    # 2. Start
    await engine.start()
    assert engine.state == EngineState.RUNNING

    # 3. Health check
    health = await engine.health_check()
    assert health["status"] == "HEALTHY"
    assert health["engine"] == "ai"

    # 4. Stop
    await engine.stop()
    assert engine.state == EngineState.STOPPED


@pytest.mark.asyncio
async def test_engine_cannot_initialize_twice() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)
    with pytest.raises(EngineStateError):
        await engine.initialize(kernel)


@pytest.mark.asyncio
async def test_engine_cannot_start_before_initialize() -> None:
    engine, _ = _make_engine()
    with pytest.raises(EngineStateError):
        await engine.start()


# ---------------------------------------------------------------------------
# §2 — Capability Registration with Kernel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kernel_capability_registration() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    assert len(kernel.capabilities) == len(CANONICAL_CAPABILITIES)
    for cap_name in CANONICAL_CAPABILITIES:

        assert cap_name in kernel.capabilities
        assert kernel.capabilities[cap_name]["provider"] == "ai"
        assert kernel.capabilities[cap_name]["handler"] is not None

    # Check permission declarations
    assert kernel.capabilities["kortex.ai.response.generate"]["required_permissions"] == ["ai:generate"]
    assert kernel.capabilities["kortex.ai.agent.orchestrate"]["required_permissions"] == ["ai:orchestrate"]
    assert kernel.capabilities["kortex.ai.agent.resume"]["required_permissions"] == ["ai:orchestrate"]
    assert kernel.capabilities["kortex.ai.tool.invoke"]["required_permissions"] == ["ai:execute"]
    assert kernel.capabilities["kortex.ai.provider.register"]["required_permissions"] == ["ai:manage"]
    assert kernel.capabilities["kortex.ai.provider.list"]["required_permissions"] == ["ai:read"]
    assert kernel.capabilities["kortex.ai.model.list"]["required_permissions"] == ["ai:read"]
    assert kernel.capabilities["kortex.ai.agent.cancel"]["required_permissions"] == ["ai:orchestrate"]
    assert kernel.capabilities["kortex.ai.agent.status"]["required_permissions"] == ["ai:read"]
    assert kernel.capabilities["kortex.ai.agent.list"]["required_permissions"] == ["ai:read"]


# ---------------------------------------------------------------------------
# §3 — generate_response Capability Flow & Events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_response_happy_path() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)
    await engine.start()

    request = LLMRequest(
        request_id="req-123",
        tenant_id="tenant-alpha",
        user_id="user-1",
        conversation_id="conv-456",
        prompt="Tell me a joke.",
    )

    response = await engine.generate_response(request)
    assert response.text_content == "Hello from AI"
    assert response.request_id == "req-123"

    # Verify history recorded in memory
    history = await engine.memory_manager.get_turns("tenant-alpha", "conv-456")
    assert len(history) == 1
    assert history[0].user_content == "Tell me a joke."
    assert history[0].assistant_content == "Hello from AI"

    # Verify events published
    topics = [evt[0] for evt in kernel.events_published]
    assert "ai.generation.started" in topics
    assert "ai.generation.completed" in topics

    # Verify diagnostics updated
    metrics = engine.metrics()
    assert metrics["generations"]["successful"] == 1


@pytest.mark.asyncio
async def test_generate_response_rejects_blank_tenant() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    with pytest.raises(MemoryValidationError):
        await engine.generate_response(
            LLMRequest(
                request_id="r1",
                tenant_id="   ",
                user_id="u1",
                conversation_id="c1",
                prompt="p",
            )
        )


@pytest.mark.asyncio
async def test_generate_response_routing_failure_recorded() -> None:
    # Build engine with no executable providers
    registry = ProviderRegistry()
    registry.register(
        MetadataOnlyAIProvider(
            AIProviderMetadata(
                provider_id="meta-only",
                display_name="Meta",
                vendor="V",
                endpoint_type="local_host",
            )
        )
    )
    router = ModelRouter(registry=registry)
    engine = AIOrchestrationEngine(provider_registry=registry, model_router=router)
    kernel = InMemoryKernelBridge()
    await engine.initialize(kernel)

    request = LLMRequest(
        request_id="r1",
        tenant_id="t1",
        user_id="u1",
        conversation_id="c1",
        prompt="p",
    )

    with pytest.raises(NoRoutableProviderError):
        await engine.generate_response(request)

    metrics = engine.metrics()
    assert metrics["generations"]["failed"] == 1
    assert metrics["error_breakdown"]["NoRoutableProviderError"] == 1


class _AlwaysFailingProvider(BaseAIProvider):
    """Reference provider whose generation calls always raise a transient failure."""

    def __init__(self, provider_id: str = "failing-provider") -> None:
        self._provider_id = provider_id
        self._metadata = AIProviderMetadata(
            provider_id=provider_id,
            display_name="Failing Provider",
            vendor="TestVendor",
            endpoint_type="local_host",
            supported_models=["dummy-model"],
        )

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    @property
    def supported_models(self) -> list[str]:
        return list(self._metadata.supported_models)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise TransientProviderError("Primary provider is unreachable.")

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise TransientProviderError("Primary provider is unreachable.")

    async def health_check(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# §3.5 — Provider Fallback Routing (M9 Recovery Matrix row 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_response_falls_back_to_secondary_provider_on_primary_failure() -> None:
    """M9 Attack 6: 'route to secondary local/cloud candidate' on primary failure."""
    registry = ProviderRegistry()
    registry.register(_AlwaysFailingProvider(provider_id="primary"))
    registry.register(DummyExecutingProvider(provider_id="secondary"))
    router = ModelRouter(registry=registry)
    engine = AIOrchestrationEngine(provider_registry=registry, model_router=router)
    kernel = InMemoryKernelBridge()
    await engine.initialize(kernel)

    request = LLMRequest(
        request_id="r1",
        tenant_id="t1",
        user_id="u1",
        conversation_id="c1",
        prompt="p",
    )

    response = await engine.generate_response(request)
    assert response.text_content == "Hello from AI"

    metrics = engine.metrics()
    assert metrics["generations"]["successful"] == 1


@pytest.mark.asyncio
async def test_generate_response_raises_when_every_fallback_candidate_fails() -> None:
    registry = ProviderRegistry()
    registry.register(_AlwaysFailingProvider(provider_id="primary"))
    registry.register(_AlwaysFailingProvider(provider_id="secondary"))
    router = ModelRouter(registry=registry)
    engine = AIOrchestrationEngine(provider_registry=registry, model_router=router)
    kernel = InMemoryKernelBridge()
    await engine.initialize(kernel)

    request = LLMRequest(
        request_id="r1",
        tenant_id="t1",
        user_id="u1",
        conversation_id="c1",
        prompt="p",
    )

    with pytest.raises(ProviderFallbackExhaustedError):
        await engine.generate_response(request)

    metrics = engine.metrics()
    assert metrics["generations"]["failed"] == 1
    assert metrics["error_breakdown"]["ProviderFallbackExhaustedError"] == 1


@pytest.mark.asyncio
async def test_router_llm_execution_port_falls_back_for_agent_steps() -> None:
    """The same failover must protect agent reasoning steps, not only direct generation."""
    registry = ProviderRegistry()
    registry.register(_AlwaysFailingProvider(provider_id="primary"))
    registry.register(DummyExecutingProvider(provider_id="secondary"))
    router = ModelRouter(registry=registry)
    port = RouterLLMExecutionPort(router=router, registry=registry)

    request = LLMRequest(
        request_id="r1",
        tenant_id="t1",
        user_id="u1",
        conversation_id="c1",
        prompt="p",
    )

    response = await port.generate_step(request)
    assert response.text_content == "Hello from AI"


class _WriteFailingConversationStore:
    """Reference `IConversationStore` whose `append` always fails durably."""

    async def append(
        self,
        tenant_id: str,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        request_id: str,
        user_id: str,
    ) -> ConversationTurn:
        raise ConversationStoreError("Database connection lost.")

    async def recent_turns(
        self, tenant_id: str, conversation_id: str, limit: int, offset: int = 0
    ) -> list[ConversationTurn]:
        return []


# ---------------------------------------------------------------------------
# §3.6 — Graceful Storage-Write Degradation (M9 Recovery Matrix row 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_response_degrades_gracefully_when_history_write_fails() -> None:
    """M9 Attack 6: storage failure after a successful generation must return the
    generation with a degraded flag rather than dropping the turn."""
    engine, kernel = _make_engine()
    engine._memory_manager = AIMemoryManager(store=_WriteFailingConversationStore())
    await engine.initialize(kernel)

    request = LLMRequest(
        request_id="req-degraded",
        tenant_id="tenant-alpha",
        user_id="user-1",
        conversation_id="conv-456",
        prompt="Tell me a joke.",
    )

    response = await engine.generate_response(request)
    assert response.degraded is True
    assert response.text_content == "Hello from AI"

    # The turn counts as a successful generation — the caller got an answer.
    metrics = engine.metrics()
    assert metrics["generations"]["successful"] == 1
    assert metrics["generations"]["failed"] == 0


@pytest.mark.asyncio
async def test_generate_response_emits_storage_write_failed_event_on_degradation() -> None:
    engine, kernel = _make_engine()
    engine._memory_manager = AIMemoryManager(store=_WriteFailingConversationStore())
    await engine.initialize(kernel)

    request = LLMRequest(
        request_id="req-degraded-2",
        tenant_id="tenant-alpha",
        user_id="user-1",
        conversation_id="conv-456",
        prompt="Tell me a joke.",
    )

    await engine.generate_response(request)

    topics = [evt[0] for evt in kernel.events_published]
    assert "ai.storage.write_failed" in topics
    assert "ai.generation.failed" not in topics
    assert "ai.generation.completed" in topics


@pytest.mark.asyncio
async def test_generate_response_happy_path_is_never_degraded() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    request = LLMRequest(
        request_id="req-normal",
        tenant_id="tenant-alpha",
        user_id="user-1",
        conversation_id="conv-456",
        prompt="Tell me a joke.",
    )

    response = await engine.generate_response(request)
    assert response.degraded is False


# ---------------------------------------------------------------------------
# §4 — orchestrate_agent & resume_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orchestrate_agent_happy_path() -> None:
    # Configure an agent orchestrator with in-memory test ports
    llm_port = InMemoryLLMExecutionPort(
        responses=[
            LLMResponse(
                request_id="r1",
                text_content="Calling weather",
                tool_calls=[{"name": "get_weather", "arguments": {"city": "Berlin"}}],
            ),
            LLMResponse(
                request_id="r2",
                text_content="It is sunny in Berlin.",
                tool_calls=[],
            ),
        ]
    )
    ctx_port = InMemoryAgentContextPort()
    tools = ToolRegistry()
    tools.register_tool(
        ToolDefinition(
            name="get_weather",
            description="desc",
            canonical_capability="kortex.weather.get",
            parameters_schema={"type": "object"},
        )
    )
    kernel = InMemoryKernelBridge()
    invoker = AIToolInvoker(registry=tools, execution_port=KernelToolExecutionPort(kernel))

    orchestrator = AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=llm_port,
        context_port=ctx_port,
        approval_policy=AlwaysApprovePolicy(),
    )

    engine = AIOrchestrationEngine(agent_orchestrator=orchestrator, tool_registry=tools)
    await engine.initialize(kernel)
    await engine.start()

    task = AgentTask(
        task_id="task-1",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Check weather in Berlin",
    )

    result = await engine.orchestrate_agent(task)
    assert result.status == AgentStatus.COMPLETED
    assert result.total_steps == 2
    assert result.final_response == "It is sunny in Berlin."

    # Verify event published
    topics = [evt[0] for evt in kernel.events_published]
    assert "ai.agent.completed" in topics


@pytest.mark.asyncio
async def test_orchestrate_agent_pause_and_resume() -> None:
    # Configure an agent orchestrator that pauses on mutation
    llm_port = InMemoryLLMExecutionPort(
        responses=[
            LLMResponse(
                request_id="r1",
                text_content="Creating order",
                tool_calls=[{"name": "create_order", "arguments": {"item": "Laptop"}}],
            ),
            LLMResponse(
                request_id="r2",
                text_content="Order created successfully.",
                tool_calls=[],
            ),
        ]
    )
    ctx_port = InMemoryAgentContextPort()
    tools = ToolRegistry()
    tools.register_tool(
        ToolDefinition(
            name="create_order",
            description="desc",
            canonical_capability="kortex.order.create",
            parameters_schema={"type": "object"},
            is_mutation=True,
        )
    )
    kernel = InMemoryKernelBridge()
    invoker = AIToolInvoker(registry=tools, execution_port=KernelToolExecutionPort(kernel))

    # Both orchestrators share one task store, as production does: the
    # resuming process reads the same durable store the pausing process
    # wrote to. A resume finds no record without this and is refused.
    shared_task_store = InMemoryAgentTaskStore()

    # Always deny triggers pause
    orchestrator = AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=llm_port,
        context_port=ctx_port,
        approval_policy=AlwaysDenyPolicy(),
        task_store=shared_task_store,
    )

    engine = AIOrchestrationEngine(agent_orchestrator=orchestrator, tool_registry=tools)
    await engine.initialize(kernel)

    task = AgentTask(
        task_id="task-2",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Order a laptop",
    )

    # 1. Run task -> Pauses
    paused_result = await engine.orchestrate_agent(task)
    assert paused_result.status == AgentStatus.PAUSED_FOR_APPROVAL
    assert paused_result.resume_token is not None

    # 2. Resume task with AlwaysApprove policy
    resuming_orchestrator = AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=llm_port,
        context_port=ctx_port,
        approval_policy=AlwaysApprovePolicy(),
        task_store=shared_task_store,
    )
    resuming_engine = AIOrchestrationEngine(
        agent_orchestrator=resuming_orchestrator, tool_registry=tools
    )
    await resuming_engine.initialize(kernel)

    resumed_result = await resuming_engine.resume_agent(
        task=task,
        resume_token=paused_result.resume_token,
        approved_tool_calls=paused_result.pending_tool_calls,
    )
    assert resumed_result.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# §4.5 — Agent Task Lifecycle Control & Observability (M13 Kernel Exposure)
# ---------------------------------------------------------------------------


def _make_task_record(
    task_id: str, tenant_id: str, status: AgentStatus
) -> PersistedAgentTaskRecord:
    task = AgentTask(
        task_id=task_id,
        tenant_id=tenant_id,
        user_id="user-1",
        conversation_id="conv-1",
        goal="M13 lifecycle test",
    )
    return PersistedAgentTaskRecord(task=task, status=status, current_step=1)


@pytest.mark.asyncio
async def test_agent_lifecycle_capabilities_are_registered_and_reachable() -> None:
    """Handlers registered for cancel/status/list must be the real facade methods,
    invocable exactly as the Kernel dispatcher invokes them: `handler(**parameters)`.
    """
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    record = _make_task_record("task-1", "tenant-1", AgentStatus.RUNNING)
    await engine._agent_orchestrator._task_store.save_task(record)

    status_handler = kernel.capabilities["kortex.ai.agent.status"]["handler"]
    fetched = await status_handler(task_id="task-1", tenant_id="tenant-1")
    assert fetched is not None
    assert fetched.task.task_id == "task-1"

    list_handler = kernel.capabilities["kortex.ai.agent.list"]["handler"]
    listed = await list_handler(tenant_id="tenant-1")
    assert [r.task.task_id for r in listed] == ["task-1"]

    cancel_handler = kernel.capabilities["kortex.ai.agent.cancel"]["handler"]
    cancelled = await cancel_handler(task_id="task-1", tenant_id="tenant-1")
    assert cancelled is True

    refetched = await status_handler(task_id="task-1", tenant_id="tenant-1")
    assert refetched is not None
    assert refetched.status == AgentStatus.CANCELLED


@pytest.mark.asyncio
async def test_agent_task_list_enforces_strict_tenant_isolation() -> None:
    """Adversarial: a tenant must never observe another tenant's tasks through
    the list capability, regardless of how many tasks the other tenant has."""
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-a1", "tenant-a", AgentStatus.RUNNING)
    )
    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-a2", "tenant-a", AgentStatus.COMPLETED)
    )
    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-b1", "tenant-b", AgentStatus.RUNNING)
    )

    tenant_a_tasks = await engine.list_agent_tasks("tenant-a")
    assert {r.task.task_id for r in tenant_a_tasks} == {"task-a1", "task-a2"}

    tenant_b_tasks = await engine.list_agent_tasks("tenant-b")
    assert {r.task.task_id for r in tenant_b_tasks} == {"task-b1"}


@pytest.mark.asyncio
async def test_agent_task_cancel_cannot_reach_another_tenants_task() -> None:
    """Adversarial: cancelling by task_id under the wrong tenant_id must fail
    rather than cancelling (or even revealing the existence of) the real task."""
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-victim", "tenant-owner", AgentStatus.RUNNING)
    )

    cancelled = await engine.cancel_agent_task("task-victim", "tenant-attacker")
    assert cancelled is False

    still_running = await engine.get_agent_task("task-victim", "tenant-owner")
    assert still_running is not None
    assert still_running.status == AgentStatus.RUNNING


@pytest.mark.asyncio
async def test_agent_task_list_filters_by_status() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-running", "tenant-1", AgentStatus.RUNNING)
    )
    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-cancelled", "tenant-1", AgentStatus.CANCELLED)
    )

    running_only = await engine.list_agent_tasks("tenant-1", status=AgentStatus.RUNNING)
    assert [r.task.task_id for r in running_only] == ["task-running"]


@pytest.mark.asyncio
async def test_agent_task_list_accepts_raw_string_status_from_capability_boundary() -> None:
    """A Kernel capability caller can only ever supply a JSON-shaped string, never
    an `AgentStatus` enum member — this must behave identically to passing the enum."""
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-running", "tenant-1", AgentStatus.RUNNING)
    )
    await engine._agent_orchestrator._task_store.save_task(
        _make_task_record("task-cancelled", "tenant-1", AgentStatus.CANCELLED)
    )

    list_handler = kernel.capabilities["kortex.ai.agent.list"]["handler"]
    result = await list_handler(tenant_id="tenant-1", status="CANCELLED")
    assert [r.task.task_id for r in result] == ["task-cancelled"]


@pytest.mark.asyncio
async def test_agent_task_list_rejects_invalid_status_string() -> None:
    """An unrecognized status string must fail loudly and early, never reach the
    store (where the two backends would otherwise fail differently — see
    `list_agent_tasks`'s docstring)."""
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    with pytest.raises(ValueError):
        await engine.list_agent_tasks("tenant-1", status="NOT_A_REAL_STATUS")


# ---------------------------------------------------------------------------
# §5 — invoke_tool Capability Flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_tool_flow() -> None:
    engine, kernel = _make_engine()
    await engine.initialize(kernel)

    call = ToolCall(
        call_id="call-abc",
        tool_name="get_weather",
        arguments={"city": "Paris"},
    )

    result = await engine.invoke_tool("tenant-1", call)
    assert result.status.value == "SUCCESS"
    assert len(kernel.invocations) == 1
    assert kernel.invocations[0][0] == "kortex.weather.get"

    # Event check
    topics = [evt[0] for evt in kernel.events_published]
    assert "ai.tool.invoked" in topics


# ---------------------------------------------------------------------------
# §6 — Provider Registry Delegation
# ---------------------------------------------------------------------------


def test_provider_registration_delegation() -> None:
    engine, _ = _make_engine()
    dummy = DummyExecutingProvider(provider_id="custom-provider")
    engine.register_provider(dummy)

    providers = engine.list_providers()
    provider_ids = [p.provider_id for p in providers]
    assert "custom-provider" in provider_ids


def test_list_models_flattens_every_provider_and_carries_no_secret_field() -> None:
    """`list_models()` is a pure flatten (Slice 4.6) -- no routing, no
    selection, no field beyond `AIModelSummary`'s own three (in particular,
    no `secret_handle`/`credential_requirement`, unlike `AIProviderMetadata`)."""
    engine, _ = _make_engine()  # default provider: "dummy-provider" / ["dummy-model"]
    second = DummyExecutingProvider(provider_id="second-provider")
    second._metadata = second._metadata.model_copy(update={"supported_models": ["model-a", "model-b"]})
    engine.register_provider(second)

    models = engine.list_models()

    assert {(m.model_id, m.provider_id) for m in models} == {
        ("dummy-model", "dummy-provider"),
        ("model-a", "second-provider"),
        ("model-b", "second-provider"),
    }
    for model in models:
        assert not hasattr(model, "secret_handle")
        assert not hasattr(model, "credential_requirement")


# ---------------------------------------------------------------------------
# §7 — Non-blocking Event Publication Mutation Probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_publishing_failure_does_not_crash_generation() -> None:
    """Mutation Probe: An event engine failure must never crash an AI generation request."""
    engine, kernel = _make_engine()
    await engine.initialize(kernel)
    kernel.should_fail_events = True  # Force publish_event to raise

    request = LLMRequest(
        request_id="req-999",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="Hello",
    )

    # Must succeed cleanly despite event failure
    response = await engine.generate_response(request)
    assert response.text_content == "Hello from AI"


# ---------------------------------------------------------------------------
# §8 — AST Import Quarantine & Anti-Bypass Probes
# ---------------------------------------------------------------------------


def _collect_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


FORBIDDEN_NAMESPACES = [
    "kortex.core.kernel",
    "kortex.core.container",
    "kortex.engines.security",
    "kortex.engines.knowledge.engine",
    "sqlalchemy",
]


@pytest.mark.parametrize("file_name", ["engine.py", "diagnostics.py", "interfaces.py"])
def test_m8_files_quarantine_forbidden_imports(file_name: str) -> None:
    target_path = Path(__file__).parent.parent.parent / "src" / "kortex" / "engines" / "ai" / file_name
    imports = _collect_imports(target_path)
    for forbidden in FORBIDDEN_NAMESPACES:
        violations = [imp for imp in imports if imp == forbidden or imp.startswith(forbidden + ".")]
        assert violations == [], (
            f"{file_name} illegally imports {forbidden!r}: {violations}"
        )


def test_engine_py_contains_no_raw_sql() -> None:
    """Mutation probe: engine.py must contain no direct SQL strings or operations."""
    target_path = Path(__file__).parent.parent.parent / "src" / "kortex" / "engines" / "ai" / "engine.py"
    content = target_path.read_text(encoding="utf-8").lower()
    assert "select " not in content
    assert "insert into" not in content
    assert "create table" not in content
