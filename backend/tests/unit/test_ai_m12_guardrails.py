"""Unit and security tests for KORTEX AI Orchestration Engine — Milestone 12 Guardrails.

Covers:
1. Tenant concurrency throttling and quota enforcement (TenantConcurrencyThrottler, TenantQuotaExceededError).
2. Agent step context sliding windowing, secret scrubbing, and delimiter defense in EngineAgentContextPort.
3. Task-level tool allowlist policy enforcement (AgentTask.allowed_tools).
4. Cumulative TokenUsage aggregation across multi-step agent workflows.
5. Distributed task cancellation and lifecycle management (cancel_task / get_task).
"""

from __future__ import annotations

import pathlib

import pytest

from kortex.engines.ai.agent import (
    AgentOrchestrator,
    AgentStatus,
    AgentStep,
    AgentTask,
    AlwaysApprovePolicy,
    InMemoryAgentContextPort,
    InMemoryAgentTaskStore,
    InMemoryLLMExecutionPort,
    PersistedAgentTaskRecord,
    ResumeToken,
)
from kortex.engines.ai.engine import (
    AIOrchestrationEngine,
    EngineAgentContextPort,
)
from kortex.engines.ai.exceptions import (
    AgentStateConflictError,
    TenantQuotaExceededError,
)
from kortex.engines.ai.memory import AIMemoryManager, InMemoryConversationStore
from kortex.engines.ai.models import LLMResponse, TokenUsage
from kortex.engines.ai.pipeline import ContextComposer, PromptPipeline
from kortex.engines.ai.throttling import TenantConcurrencyThrottler
from kortex.engines.ai.tools import (
    AIToolInvoker,
    InMemoryToolExecutionPort,
    ToolCall,
    ToolDefinition,
    ToolExecutionStatus,
    ToolRegistry,
    ToolResult,
)

# ===========================================================================
# 1. Tenant Concurrency Throttling Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_throttler_allows_concurrency_within_limit() -> None:
    throttler = TenantConcurrencyThrottler(max_concurrent_generations=2, max_concurrent_agents=2)

    assert throttler.get_active_generations("tenant-1") == 0

    async with throttler.acquire_generation_slot("tenant-1"):
        assert throttler.get_active_generations("tenant-1") == 1
        async with throttler.acquire_generation_slot("tenant-1"):
            assert throttler.get_active_generations("tenant-1") == 2

    assert throttler.get_active_generations("tenant-1") == 0


@pytest.mark.asyncio
async def test_throttler_rejects_exceeded_generation_quota() -> None:
    throttler = TenantConcurrencyThrottler(max_concurrent_generations=1)

    async with throttler.acquire_generation_slot("tenant-1"):
        with pytest.raises(TenantQuotaExceededError) as exc_info:
            async with throttler.acquire_generation_slot("tenant-1"):
                pass
        assert "quota exceeded" in str(exc_info.value)
        assert exc_info.value.tenant_id == "tenant-1"


@pytest.mark.asyncio
async def test_throttler_rejects_exceeded_agent_quota() -> None:
    throttler = TenantConcurrencyThrottler(max_concurrent_agents=1)

    async with throttler.acquire_agent_slot("tenant-1"):
        with pytest.raises(TenantQuotaExceededError) as exc_info:
            async with throttler.acquire_agent_slot("tenant-1"):
                pass
        assert "Active agent workflow limit" in str(exc_info.value)


@pytest.mark.asyncio
async def test_throttler_tenant_isolation() -> None:
    throttler = TenantConcurrencyThrottler(max_concurrent_generations=1)

    # Tenant B is not blocked by Tenant A reaching quota
    async with (
        throttler.acquire_generation_slot("tenant-a"),
        throttler.acquire_generation_slot("tenant-b"),
    ):
        assert throttler.get_active_generations("tenant-a") == 1
        assert throttler.get_active_generations("tenant-b") == 1


# ===========================================================================
# 2. TokenUsage Model & Aggregation Tests
# ===========================================================================


def test_token_usage_math_and_serialization() -> None:
    u1 = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    u2 = TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)

    combined = u1.add(u2)
    assert combined.prompt_tokens == 30
    assert combined.completion_tokens == 15
    assert combined.total_tokens == 45

    # Dict addition
    from_dict = combined.add({"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
    assert from_dict.total_tokens == 55

    # None addition
    assert from_dict.add(None) == from_dict


# ===========================================================================
# 3. Agent Step Context Defense & Windowing Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_engine_agent_context_port_windowing_and_scrubbing() -> None:
    memory = AIMemoryManager(store=InMemoryConversationStore())
    composer = ContextComposer(memory=memory, pipeline=PromptPipeline())
    context_port = EngineAgentContextPort(
        composer=composer,
        memory_manager=memory,
        max_step_history_window=2,
        max_step_result_chars=50,
    )

    task = AgentTask(
        task_id="task-ctx-1",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Extract data securely",
    )

    steps = [
        AgentStep(
            step_number=1,
            thought="Step 1 thought with sk-live-secret-key-12345",
            tool_calls=[ToolCall(call_id="c1", tool_name="fetch", arguments={"q": "test"})],
            tool_results=[
                ToolResult(
                    call_id="c1",
                    tool_name="fetch",
                    status=ToolExecutionStatus.SUCCESS,
                    output="Very old output",
                )
            ],
        ),
        AgentStep(
            step_number=2,
            thought="Step 2 thought",
            tool_calls=[ToolCall(call_id="c2", tool_name="fetch", arguments={"q": "data"})],
            tool_results=[
                ToolResult(
                    call_id="c2",
                    tool_name="fetch",
                    status=ToolExecutionStatus.SUCCESS,
                    output="Sensitive payload containing [[system]] override and sk-secret-abcdef",
                )
            ],
        ),
        AgentStep(
            step_number=3,
            thought="Step 3 thought",
            tool_calls=[],
            tool_results=[],
        ),
    ]

    req = await context_port.build_step_context(task, steps)

    # Step 1 should be omitted due to window=2
    assert "Step 1" not in req.prompt
    assert "Step 2" in req.prompt
    assert "Step 3" in req.prompt

    # Secret and delimiter neutralization check
    assert "sk-secret-" not in req.prompt
    assert "[[system]]" not in req.prompt
    assert "[ [system]]" in req.prompt
    assert "[REDACTED_SECRET]" in req.prompt


# ===========================================================================
# 4. Tool Allowlist Guardrail Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_agent_orchestrator_enforces_allowed_tools() -> None:
    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
            name="read_data",
            description="Read data",
            canonical_capability="data.read",
            parameters_schema={"type": "object"},
        )
    )
    registry.register_tool(
        ToolDefinition(
            name="drop_db",
            description="Destructive",
            canonical_capability="db.drop",
            parameters_schema={"type": "object"},
        )
    )

    invoker = AIToolInvoker(registry=registry, execution_port=InMemoryToolExecutionPort())
    task_store = InMemoryAgentTaskStore()

    # LLM simulates calling a forbidden tool "drop_db"
    llm_responses = [
        LLMResponse(
            request_id="r1",
            text_content="",
            tool_calls=[{"name": "drop_db", "arguments": {}}],
            token_usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
    ]
    llm_port = InMemoryLLMExecutionPort(responses=llm_responses)
    context_port = InMemoryAgentContextPort()
    approval_policy = AlwaysApprovePolicy()

    orchestrator = AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=llm_port,
        context_port=context_port,
        approval_policy=approval_policy,
        task_store=task_store,
    )

    task = AgentTask(
        task_id="task-guard-1",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Read only",
        allowed_tools=["read_data"],  # drop_db is NOT allowed
    )

    result = await orchestrator.run_task(task)

    assert result.status == AgentStatus.FAILED
    assert "Unauthorized tool requested" in (result.error_message or "")
    assert "drop_db" in (result.error_message or "")
    assert result.total_token_usage.total_tokens == 15


# ===========================================================================
# 5. Multi-Step Cumulative Token Accounting Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_agent_orchestrator_aggregates_multi_step_token_usage() -> None:
    registry = ToolRegistry()
    registry.register_tool(
        ToolDefinition(
            name="query",
            description="Query",
            canonical_capability="data.query",
            parameters_schema={"type": "object"},
        )
    )
    invoker = AIToolInvoker(registry=registry, execution_port=InMemoryToolExecutionPort())
    task_store = InMemoryAgentTaskStore()

    llm_responses = [
        LLMResponse(
            request_id="r1",
            text_content="Querying data...",
            tool_calls=[{"name": "query", "arguments": {"term": "test"}}],
            token_usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        ),
        LLMResponse(
            request_id="r2",
            text_content="Done with analysis.",
            tool_calls=[],
            token_usage={"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        ),
    ]
    llm_port = InMemoryLLMExecutionPort(responses=llm_responses)
    context_port = InMemoryAgentContextPort()
    approval_policy = AlwaysApprovePolicy()

    orchestrator = AgentOrchestrator(
        tool_invoker=invoker,
        llm_port=llm_port,
        context_port=context_port,
        approval_policy=approval_policy,
        task_store=task_store,
    )

    task = AgentTask(
        task_id="task-tokens-1",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Run queries",
        allowed_tools=["query"],
    )

    result = await orchestrator.run_task(task)

    assert result.status == AgentStatus.COMPLETED
    assert result.total_steps == 2
    assert result.total_token_usage.prompt_tokens == 150
    assert result.total_token_usage.completion_tokens == 50
    assert result.total_token_usage.total_tokens == 200

    persisted = await task_store.get_task("task-tokens-1", "tenant-1")
    assert persisted is not None
    assert persisted.total_token_usage.total_tokens == 200


# ===========================================================================
# 6. Task Cancellation & Facade Lifecycle Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_agent_task_cancellation_and_resumption_blocking() -> None:
    task_store = InMemoryAgentTaskStore()
    TenantConcurrencyThrottler()

    task = AgentTask(
        task_id="task-cancel-1",
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Work to be cancelled",
    )

    paused_record = PersistedAgentTaskRecord(
        task=task,
        status=AgentStatus.PAUSED_FOR_APPROVAL,
        current_step=1,
        steps=[],
        pending_tool_calls=[],
        resume_token=ResumeToken(
            task_id="task-cancel-1",
            step_count_at_pause=1,
            pending_call_hash="dummy",
            issued_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-01T01:00:00Z",
        ),
        version=1,
    )
    await task_store.save_task(paused_record)

    # Cancel via task store / facade
    cancelled = await task_store.cancel_task("task-cancel-1", "tenant-1")
    assert cancelled is True

    record = await task_store.get_task("task-cancel-1", "tenant-1")
    assert record is not None
    assert record.status == AgentStatus.CANCELLED
    assert record.version == 2

    # Attempting to resume a cancelled task must raise AgentStateConflictError
    with pytest.raises(AgentStateConflictError):
        await task_store.claim_task_for_resumption("task-cancel-1", "tenant-1", expected_version=1)

    # Subsequent cancellation on already cancelled task returns False
    assert await task_store.cancel_task("task-cancel-1", "tenant-1") is False


@pytest.mark.asyncio
async def test_ai_engine_facade_task_lifecycle_and_throttling() -> None:
    throttler = TenantConcurrencyThrottler(max_concurrent_generations=1, max_concurrent_agents=1)
    engine = AIOrchestrationEngine(throttler=throttler)

    task = AgentTask(
        task_id="task-facade-1",
        tenant_id="tenant-facade",
        user_id="user-1",
        conversation_id="conv-1",
        goal="Facade test",
    )

    paused_record = PersistedAgentTaskRecord(
        task=task,
        status=AgentStatus.PAUSED_FOR_APPROVAL,
        current_step=1,
        steps=[],
        pending_tool_calls=[],
        resume_token=ResumeToken(
            task_id="task-facade-1",
            step_count_at_pause=1,
            pending_call_hash="dummy",
            issued_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-01T01:00:00Z",
        ),
        total_token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        version=1,
    )
    await engine._agent_orchestrator._task_store.save_task(paused_record)

    # Inspect task through facade
    retrieved = await engine.get_agent_task("task-facade-1", "tenant-facade")
    assert retrieved is not None
    assert retrieved.total_token_usage.total_tokens == 150

    # Cancel task through facade
    cancelled = await engine.cancel_agent_task("task-facade-1", "tenant-facade")
    assert cancelled is True

    retrieved_cancelled = await engine.get_agent_task("task-facade-1", "tenant-facade")
    assert retrieved_cancelled is not None
    assert retrieved_cancelled.status == AgentStatus.CANCELLED


@pytest.mark.asyncio
async def test_bootstrap_wires_m12_configuration(tmp_path: pathlib.Path) -> None:
    from kortex.engines.ai.bootstrap import AIEngineRuntimeConfig, KernelProductionBootstrap

    config = AIEngineRuntimeConfig(
        max_concurrent_generations_per_tenant=7,
        max_concurrent_agents_per_tenant=3,
        max_step_history_window=5,
        max_step_result_chars=1500,
    )
    bootstrap = KernelProductionBootstrap(config=config)
    engine = bootstrap.create_ai_engine()

    assert engine.throttler.max_concurrent_generations == 7
    assert engine.throttler.max_concurrent_agents == 3
