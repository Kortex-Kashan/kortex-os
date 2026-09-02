"""M10 Adversarial Certification & Remediation Tests.

Covers the 4 certified production remediation fixes:
1. Cryptographic HMAC signing and tamper-resistance for ResumeToken.
2. Sliding-window context token budget enforcement (max_context_tokens).
3. Global request deadline and cancellation in AIOrchestrationEngine.generate_response.
4. Diagnostics metrics consistency and terminal status mutual exclusivity.
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

from kortex.engines.ai.agent import (
    AgentTask,
    ResumeToken,
    _hash_tool_calls,
    _issue_resume_token,
    _verify_resume_token,
)
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.diagnostics import AIDiagnostics
from kortex.engines.ai.engine import AIOrchestrationEngine
from kortex.engines.ai.exceptions import (
    AgentValidationError,
    AIProviderTimeoutError,
)
from kortex.engines.ai.memory import AIMemoryManager, InMemoryConversationStore
from kortex.engines.ai.models import (
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
)
from kortex.engines.ai.pipeline import ContextComposer, PromptPipeline, estimate_tokens
from kortex.engines.ai.registry import ProviderRegistry
from kortex.engines.ai.retrieval import RetrievedDocument
from kortex.engines.ai.router import ModelRouter
from kortex.engines.ai.tools import ToolCall

# ---------------------------------------------------------------------------
# Test Helpers & Stubs
# ---------------------------------------------------------------------------


class _SlowProvider(BaseAIProvider):
    """Provider that sleeps for delay_seconds before returning a response."""

    def __init__(self, provider_id: str = "slow-1", delay_seconds: float = 2.0) -> None:
        self._provider_id = provider_id
        self._delay_seconds = delay_seconds

    @property
    def metadata(self) -> AIProviderMetadata:
        return AIProviderMetadata(
            provider_id=self._provider_id,
            display_name=f"Slow Provider {self._provider_id}",
            vendor="test-vendor",
            endpoint_type="local_host",
            supported_models=["slow-model-1"],
        )

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(self._delay_seconds)
        return LLMResponse(
            request_id=request.request_id,
            text_content="Completed after delay",
            model_id="slow-model-1",
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    async def health_check(self) -> bool:
        return True


class _QuickProvider(BaseAIProvider):
    """Provider that immediately returns a response."""

    def __init__(self, provider_id: str = "quick-p") -> None:
        self._provider_id = provider_id

    @property
    def metadata(self) -> AIProviderMetadata:
        return AIProviderMetadata(
            provider_id=self._provider_id,
            display_name=f"Quick Provider {self._provider_id}",
            vendor="test-vendor",
            endpoint_type="local_host",
            supported_models=["quick-model-1"],
        )

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content="Quick response",
            model_id="quick-model-1",
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    async def health_check(self) -> bool:
        return True


def _make_task(task_id: str = "task-1", tenant_id: str = "tenant-a") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        tenant_id=tenant_id,
        conversation_id="conv-1",
        user_id="user-1",
        goal="Test goal",
    )


# ---------------------------------------------------------------------------
# M10.1: Cryptographic ResumeToken Security Tests
# ---------------------------------------------------------------------------


def test_resume_token_signed_on_issuance() -> None:
    """Issued ResumeToken must contain non-empty HMAC-SHA256 signature."""
    calls = [ToolCall(call_id="c1", tool_name="test_tool", arguments={"k": "v"})]
    secret = b"my-test-secret-key-32-bytes-long!"
    token = _issue_resume_token("task-1", step_count=2, pending_calls=calls, secret=secret)

    assert token.signature != ""
    assert len(token.signature) == 64  # SHA-256 hex string


def test_synthetic_forged_resume_token_rejected() -> None:
    """A synthetically crafted token without legitimate signature must be rejected."""
    calls = [ToolCall(call_id="c1", tool_name="delete_database", arguments={})]
    task = _make_task(task_id="victim-task")
    now = datetime.datetime.now(datetime.UTC)

    # Attacker knows task_id, pending_calls, and timestamps
    forged_token = ResumeToken(
        task_id="victim-task",
        step_count_at_pause=0,
        pending_call_hash=_hash_tool_calls(calls),
        issued_at=now.isoformat(),
        expires_at=(now + datetime.timedelta(minutes=15)).isoformat(),
        signature="forged_or_empty_signature",
    )

    with pytest.raises(AgentValidationError, match="signature is invalid, tampered, or forged"):
        _verify_resume_token(forged_token, task, step_count=0, approved_calls=calls)


def test_tampered_task_id_in_signature_rejected() -> None:
    """Changing task_id invalidates cryptographic signature check."""
    calls = [ToolCall(call_id="c1", tool_name="tool_a", arguments={})]
    secret = b"shared-secret"
    token = _issue_resume_token("task-original", step_count=1, pending_calls=calls, secret=secret)

    # Attempt to use the same token for a different task
    other_task = _make_task(task_id="task-tampered")
    with pytest.raises(AgentValidationError, match="task_id"):
        _verify_resume_token(token, other_task, step_count=1, approved_calls=calls, secret=secret)


def test_tampered_step_count_signature_rejected() -> None:
    """Modifying step count on a signed token fails verification."""
    calls = [ToolCall(call_id="c1", tool_name="tool_a", arguments={})]
    secret = b"shared-secret"
    token = _issue_resume_token("task-1", step_count=1, pending_calls=calls, secret=secret)

    # Tamper the step count
    tampered_token = token.model_copy(update={"step_count_at_pause": 2})
    task = _make_task(task_id="task-1")
    with pytest.raises(AgentValidationError):
        _verify_resume_token(tampered_token, task, step_count=2, approved_calls=calls, secret=secret)


def test_tampered_approved_calls_fails_verification() -> None:
    """Swapping approved calls fails hash and signature check."""
    original_calls = [ToolCall(call_id="c1", tool_name="read_data", arguments={})]
    forged_calls = [ToolCall(call_id="c1", tool_name="drop_table", arguments={})]
    secret = b"shared-secret"
    token = _issue_resume_token("task-1", step_count=0, pending_calls=original_calls, secret=secret)

    task = _make_task(task_id="task-1")
    with pytest.raises(AgentValidationError, match="pending_call_hash"):
        _verify_resume_token(token, task, step_count=0, approved_calls=forged_calls, secret=secret)


def test_orchestrator_secret_isolation() -> None:
    """Token issued by orchestrator A with secret A cannot be verified by orchestrator B with secret B."""
    secret_a = b"secret-a-key-padding-to-length-32!"
    secret_b = b"secret-b-key-padding-to-length-32!"
    calls = [ToolCall(call_id="c1", tool_name="tool_a", arguments={})]
    task = _make_task()

    token = _issue_resume_token(task.task_id, step_count=0, pending_calls=calls, secret=secret_a)

    with pytest.raises(AgentValidationError, match="signature is invalid, tampered, or forged"):
        _verify_resume_token(token, task, step_count=0, approved_calls=calls, secret=secret_b)


# ---------------------------------------------------------------------------
# M10.2: Context Token Budget Enforcement Tests
# ---------------------------------------------------------------------------


def test_estimate_tokens_calculation() -> None:
    """Test deterministic token estimation heuristic."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("1234") == 1
    assert estimate_tokens("12345678") == 2
    assert estimate_tokens("a" * 100) == 25


def test_context_budget_retains_small_context() -> None:
    """When context is below budget, all items remain intact."""
    pipeline = PromptPipeline(max_context_tokens=1000)
    req = LLMRequest(
        request_id="req-1",
        tenant_id="tenant-a",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="Hello world",
        system_instruction="Be concise",
        context_documents=["doc1", "doc2"],
    )
    history = ["[[user]]\nhi", "[[assistant]]\nhello"]
    docs = [RetrievedDocument(content="knowledge 1", classification="PUBLIC")]

    assembled = pipeline.assemble(req, history, docs)
    # Total context documents must have caller(2) + knowledge(1) + history(2) = 5
    assert len(assembled.context_documents) == 5


def test_context_budget_sliding_window_drops_oldest_history() -> None:
    """When history exceeds token budget, oldest history is dropped first."""
    # Base prompt = 20 chars (~5 tokens), system instruction = 0 chars
    # We set budget = 20 tokens -> remaining budget = 15 tokens.
    # Each history entry is ~32 chars (~8 tokens).
    # Two history entries = 16 tokens (exceeds 15).
    # Only the newest history entry should fit (8 tokens <= 15).
    pipeline = PromptPipeline(max_context_tokens=20)
    req = LLMRequest(
        request_id="req-1",
        tenant_id="tenant-a",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="12345678901234567890",
        system_instruction="",
        context_documents=[],
    )
    old_history = "[[user]]\nold history turn content 1234"
    new_history = "[[user]]\nnew history turn content 5678"
    history = [old_history, new_history]

    assembled = pipeline.assemble(req, history, [])
    # Oldest turn dropped, newest turn preserved
    assert len(assembled.context_documents) == 1
    assert "new history turn" in assembled.context_documents[0]


def test_context_composer_propagates_max_tokens() -> None:
    """ContextComposer enforces max_context_tokens during compose()."""
    memory = AIMemoryManager(store=InMemoryConversationStore())
    pipeline = PromptPipeline()
    composer = ContextComposer(
        memory=memory,
        pipeline=pipeline,
        max_context_tokens=15,
    )

    req = LLMRequest(
        request_id="req-1",
        tenant_id="tenant-a",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="a" * 40,  # ~10 tokens
        system_instruction="",
        context_documents=["b" * 100],  # ~25 tokens (exceeds remaining 5 tokens)
    )

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(composer.compose(req))
        # Large context doc exceeded budget and was dropped
        assert len(result.context_documents) == 0
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# M10.3: Global Generation Deadline Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_generation_timeout_trips_on_slow_provider() -> None:
    """generate_response raises AIProviderTimeoutError when global timeout expires."""
    reg = ProviderRegistry()
    slow_p = _SlowProvider(provider_id="slow-1", delay_seconds=2.0)
    reg.register(slow_p)

    router = ModelRouter(registry=reg)
    memory = AIMemoryManager(store=InMemoryConversationStore())
    composer = ContextComposer(memory=memory, pipeline=PromptPipeline())

    engine = AIOrchestrationEngine(
        provider_registry=reg,
        model_router=router,
        memory_manager=memory,
        context_composer=composer,
        default_generation_timeout_seconds=0.2,  # 200ms global timeout
    )

    req = LLMRequest(
        request_id="req-timeout",
        tenant_id="tenant-a",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="Slow request",
    )

    with pytest.raises(AIProviderTimeoutError, match="Global AI generation timeout exceeded"):
        await engine.generate_response(req)

    metrics = engine.metrics()
    assert metrics["generations"]["failed"] == 1


@pytest.mark.asyncio
async def test_global_generation_succeeds_before_timeout() -> None:
    """generate_response succeeds normally when execution completes before timeout."""
    reg = ProviderRegistry()
    quick_p = _QuickProvider(provider_id="quick-1")
    reg.register(quick_p)

    router = ModelRouter(registry=reg)
    memory = AIMemoryManager(store=InMemoryConversationStore())
    composer = ContextComposer(memory=memory, pipeline=PromptPipeline())

    engine = AIOrchestrationEngine(
        provider_registry=reg,
        model_router=router,
        memory_manager=memory,
        context_composer=composer,
        default_generation_timeout_seconds=5.0,
    )

    req = LLMRequest(
        request_id="req-quick",
        tenant_id="tenant-a",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="Quick request",
    )

    resp = await engine.generate_response(req)
    assert resp.text_content == "Quick response"
    metrics = engine.metrics()
    assert metrics["generations"]["successful"] == 1


# ---------------------------------------------------------------------------
# M10.4: Diagnostics Metrics Consistency & Mutual Exclusivity Tests
# ---------------------------------------------------------------------------


def test_diagnostics_agent_task_metrics_exact_sum() -> None:
    """Agent task sub-counters must sum exactly to total."""
    diag = AIDiagnostics()

    diag.record_agent_task(status="COMPLETED", latency_ms=100.0, total_steps=3)
    diag.record_agent_task(status="PAUSED_FOR_APPROVAL", latency_ms=50.0, total_steps=1)
    diag.record_agent_task(status="TIMED_OUT", latency_ms=500.0, total_steps=5)
    diag.record_agent_task(status="LOOP_DETECTED", latency_ms=200.0, total_steps=4)
    diag.record_agent_task(status="STEP_LIMIT_EXCEEDED", latency_ms=300.0, total_steps=10)
    diag.record_agent_task(status="FAILED", latency_ms=120.0, total_steps=2)

    m = diag.metrics()["agent_tasks"]
    assert m["total"] == 6
    assert m["completed"] == 1
    assert m["paused_for_approval"] == 1
    assert m["timed_out"] == 1
    assert m["loop_detected"] == 1
    assert m["step_limit_exceeded"] == 1
    assert m["failed"] == 1

    sub_sum = (
        m["completed"]
        + m["paused_for_approval"]
        + m["timed_out"]
        + m["loop_detected"]
        + m["step_limit_exceeded"]
        + m["failed"]
    )
    assert sub_sum == m["total"] == 6
