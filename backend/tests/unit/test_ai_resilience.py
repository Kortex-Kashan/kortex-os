"""Unit & adversarial tests for KORTEX AI Provider Resilience Layer (Milestone 9.2).

Tests adhere strictly to the ratified M9.2 specification:
- Timeout protection against hung providers and GPU deadlocks
- Exponential backoff retry policies and transient vs permanent error classification
- Circuit breaker state machine (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- Multi-provider fallback chaining
- AST import quarantine and protocol compatibility
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import (
    AIProviderError,
    AIProviderTimeoutError,
    CircuitBreakerOpenError,
    PermanentProviderError,
    ProviderFallbackExhaustedError,
    TransientProviderError,
)
from kortex.engines.ai.interfaces import IBaseAIProvider
from kortex.engines.ai.models import (
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
)
from kortex.engines.ai.resilience import (
    CircuitBreaker,
    CircuitState,
    ProviderFallbackChain,
    ResilientAIProvider,
    RetryPolicy,
)

# ---------------------------------------------------------------------------
# Test Providers & Fakes
# ---------------------------------------------------------------------------


class MockProvider(BaseAIProvider):
    """Configurable mock AI provider for testing resilience, retries, and fallbacks."""

    def __init__(
        self,
        provider_id: str = "mock-primary",
        supported_models: list[str] | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._supported_models = supported_models or ["model-a", "model-b"]
        self._metadata = AIProviderMetadata(
            provider_id=provider_id,
            display_name=f"Mock Provider ({provider_id})",
            vendor="mock-vendor",
            endpoint_type="local_host",
            supported_models=self._supported_models,
            credential_requirement="none",
        )
        self.call_count = 0
        self.embedding_call_count = 0
        self.delay: float = 0.0
        self.failure_sequence: list[Exception | None] = []
        self.is_healthy: bool = True

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def supported_models(self) -> list[str]:
        return self._supported_models

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if self.failure_sequence:
            err = self.failure_sequence.pop(0)
            if err is not None:
                raise err

        return LLMResponse(
            request_id=request.request_id,
            text_content=f"Response from {self._provider_id} (turn {self.call_count})",
            model_id=self._supported_models[0],
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.embedding_call_count += 1
        if self.delay > 0:
            await asyncio.sleep(self.delay)

        if self.failure_sequence:
            err = self.failure_sequence.pop(0)
            if err is not None:
                raise err

        return [[0.1, 0.2, 0.3] for _ in texts]

    async def health_check(self) -> bool:
        return self.is_healthy


def _sample_request() -> LLMRequest:
    return LLMRequest(
        request_id="req-test-1",
        tenant_id="tenant-alpha",
        user_id="user-1",
        conversation_id="conv-1",
        prompt="Explain quantum entanglement",
    )


# ---------------------------------------------------------------------------
# §1 — Timeout Protection Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_timeout_cancels_hung_call_and_raises_timeout_error() -> None:
    """Verify that a provider hanging longer than timeout_seconds raises AIProviderTimeoutError."""
    inner = MockProvider("ollama-slow")
    inner.delay = 1.0  # 1 second delay
    resilient = ResilientAIProvider(
        provider=inner,
        timeout_seconds=0.05,  # 50ms timeout
        retry_policy=RetryPolicy(max_attempts=1),
    )

    req = _sample_request()
    with pytest.raises(AIProviderTimeoutError, match=r"timed out after 0\.05s"):
        await resilient.generate_text(req)

    assert inner.call_count == 1
    assert resilient.circuit_breaker.consecutive_failures == 1


@pytest.mark.asyncio
async def test_embedding_timeout_enforcement() -> None:
    """Verify embeddings timeout enforcement."""
    inner = MockProvider("vllm-embed")
    inner.delay = 1.0
    resilient = ResilientAIProvider(
        provider=inner,
        embedding_timeout_seconds=0.05,
        retry_policy=RetryPolicy(max_attempts=1),
    )

    with pytest.raises(AIProviderTimeoutError, match=r"embeddings timed out after 0\.05s"):
        await resilient.generate_embeddings(["text-1", "text-2"])

    assert inner.embedding_call_count == 1


# ---------------------------------------------------------------------------
# §2 — Retry Policy & Backoff Tests
# ---------------------------------------------------------------------------


def test_retry_policy_delay_computation_deterministic() -> None:
    """Verify exponential backoff calculation without jitter."""
    policy = RetryPolicy(
        max_attempts=4,
        initial_delay=1.0,
        backoff_factor=2.0,
        max_delay=10.0,
        jitter=False,
    )

    assert policy.compute_delay(1) == 1.0  # 1 * 2^0
    assert policy.compute_delay(2) == 2.0  # 1 * 2^1
    assert policy.compute_delay(3) == 4.0  # 1 * 2^2
    assert policy.compute_delay(4) == 8.0  # 1 * 2^3
    assert policy.compute_delay(5) == 10.0  # Clamped to max_delay 10.0


def test_retry_policy_transient_error_classification() -> None:
    """Verify transient vs permanent error classification."""
    policy = RetryPolicy()

    # Transient errors
    assert policy.is_transient(TransientProviderError("HTTP 503 Service Unavailable")) is True
    assert policy.is_transient(AIProviderTimeoutError("Connection timed out")) is True
    assert policy.is_transient(ConnectionResetError("Connection reset by peer")) is True
    assert policy.is_transient(AIProviderError("Rate limit exceeded [429]")) is True
    assert policy.is_transient(AIProviderError("Gateway timeout [504]")) is True

    # Permanent errors
    assert policy.is_transient(PermanentProviderError("Unauthorized [401]")) is False
    assert policy.is_transient(ValueError("Invalid prompt parameter")) is False
    assert policy.is_transient(PermissionError("Forbidden [403]")) is False


@pytest.mark.asyncio
async def test_resilient_provider_retries_transient_error_until_success() -> None:
    """Verify provider retries transient errors and succeeds when transient error resolves."""
    inner = MockProvider("ollama-flaky")
    inner.failure_sequence = [
        TransientProviderError("HTTP 503 Backend overloaded"),
        TransientProviderError("HTTP 429 Rate limit"),
        None,  # Success on attempt 3
    ]

    policy = RetryPolicy(max_attempts=3, initial_delay=0.01, jitter=False)
    resilient = ResilientAIProvider(provider=inner, retry_policy=policy)

    req = _sample_request()
    response = await resilient.generate_text(req)

    assert inner.call_count == 3
    assert "Response from ollama-flaky" in response.text_content
    assert resilient.circuit_breaker.consecutive_failures == 0
    assert resilient.circuit_breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_resilient_provider_fails_fast_on_permanent_error() -> None:
    """Verify provider does NOT retry permanent errors."""
    inner = MockProvider("anthropic-auth")
    inner.failure_sequence = [
        PermanentProviderError("Invalid API key [401]"),
    ]

    policy = RetryPolicy(max_attempts=3, initial_delay=0.01)
    resilient = ResilientAIProvider(provider=inner, retry_policy=policy)

    req = _sample_request()
    with pytest.raises(PermanentProviderError, match="Invalid API key"):
        await resilient.generate_text(req)

    assert inner.call_count == 1  # No retries for permanent errors


# ---------------------------------------------------------------------------
# §3 — Circuit Breaker State Machine Tests
# ---------------------------------------------------------------------------


def test_circuit_breaker_trips_to_open_after_threshold_failures() -> None:
    """Verify circuit trips to OPEN after 3 consecutive failures."""
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED

    cb.record_failure(AIProviderError("Fail 1"))
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 1

    cb.record_failure(AIProviderError("Fail 2"))
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 2

    cb.record_failure(AIProviderError("Fail 3"))
    assert cb.state == CircuitState.OPEN
    assert cb.consecutive_failures == 3

    # Fast-fail when OPEN
    with pytest.raises(CircuitBreakerOpenError, match="circuit breaker is OPEN"):
        cb.can_execute()


@pytest.mark.asyncio
async def test_circuit_breaker_recovery_to_half_open_and_closed() -> None:
    """Verify circuit transitions to HALF_OPEN after timeout and closes upon successful probe."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)  # 50ms recovery
    cb.record_failure(AIProviderError("Fail 1"))
    cb.record_failure(AIProviderError("Fail 2"))
    assert cb.state == CircuitState.OPEN

    # Immediately: still open
    with pytest.raises(CircuitBreakerOpenError):
        cb.can_execute()

    # Wait for recovery timeout
    await asyncio.sleep(0.06)

    # Should transition to HALF_OPEN and allow trial call
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.can_execute() is True

    # Record success -> CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure_returns_to_open() -> None:
    """Verify failed probe in HALF_OPEN trips immediately back to OPEN."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.05)
    cb.record_failure(AIProviderError("Fail 1"))
    cb.record_failure(AIProviderError("Fail 2"))
    assert cb.state == CircuitState.OPEN

    await asyncio.sleep(0.06)
    assert cb.state == CircuitState.HALF_OPEN

    # Probe fails
    cb.record_failure(AIProviderError("Probe failed"))
    assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# §4 — Provider Fallback Chain Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_chain_switches_to_secondary_on_primary_failure() -> None:
    """Verify FallbackChain transparently fails over from primary to secondary provider."""
    primary = MockProvider("local-vllm")
    primary.failure_sequence = [
        AIProviderError("GPU Out of Memory"),
        AIProviderError("GPU Out of Memory"),
    ]

    secondary = MockProvider("local-ollama")

    chain = ProviderFallbackChain(
        providers=[primary, secondary],
        retry_policy=RetryPolicy(max_attempts=1),
    )

    req = _sample_request()
    response = await chain.generate_text(req)

    assert "Response from local-ollama" in response.text_content
    assert primary.call_count == 1
    assert secondary.call_count == 1


@pytest.mark.asyncio
async def test_fallback_chain_exhaustion_raises_clean_exception() -> None:
    """Verify ProviderFallbackExhaustedError when all providers fail."""
    p1 = MockProvider("p1")
    p1.failure_sequence = [AIProviderError("P1 down")]

    p2 = MockProvider("p2")
    p2.failure_sequence = [AIProviderError("P2 down")]

    chain = ProviderFallbackChain(
        providers=[p1, p2],
        retry_policy=RetryPolicy(max_attempts=1),
    )

    req = _sample_request()
    with pytest.raises(ProviderFallbackExhaustedError, match="All providers in fallback chain exhausted"):
        await chain.generate_text(req)

    assert p1.call_count == 1
    assert p2.call_count == 1


@pytest.mark.asyncio
async def test_fallback_chain_health_check() -> None:
    """Verify health check returns status map of all providers in chain."""
    p1 = MockProvider("p1")
    p1.is_healthy = True
    p2 = MockProvider("p2")
    p2.is_healthy = False

    chain = ProviderFallbackChain([p1, p2])
    health = await chain.health_check()

    assert health == {"p1": True, "p2": False}


# ---------------------------------------------------------------------------
# §5 — Protocol Compliance & AST Import Quarantine
# ---------------------------------------------------------------------------


def test_resilient_provider_satisfies_base_provider_protocol() -> None:
    """Verify ResilientAIProvider satisfies BaseAIProvider and IBaseAIProvider."""
    inner = MockProvider("base-mock")
    resilient = ResilientAIProvider(inner)

    assert isinstance(resilient, BaseAIProvider)
    assert isinstance(resilient, IBaseAIProvider)
    assert resilient.provider_id == "base-mock"
    assert resilient.supported_models == ["model-a", "model-b"]
    assert resilient.metadata.display_name == "Mock Provider (base-mock)"


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
    "kortex.engines.security",
    "kortex.core.container",
    "kortex.core.kernel",
    "sqlalchemy",
    "openai",
    "anthropic",
    "google.generativeai",
    "ollama",
]


def test_resilience_py_quarantine_forbidden_imports() -> None:
    """Verify resilience.py does not import forbidden namespaces."""
    target_path = (
        Path(__file__).parent.parent.parent
        / "src"
        / "kortex"
        / "engines"
        / "ai"
        / "resilience.py"
    )
    imports = _collect_imports(target_path)
    for forbidden in FORBIDDEN_NAMESPACES:
        violations = [
            imp for imp in imports if imp == forbidden or imp.startswith(forbidden + ".")
        ]
        assert violations == [], (
            f"resilience.py illegally imports {forbidden!r}: {violations}"
        )
