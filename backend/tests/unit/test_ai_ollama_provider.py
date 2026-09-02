"""Unit tests for `OllamaProvider` (M6.1-2).

All tests here use `httpx.MockTransport` -- no real network I/O, no real
Ollama instance required. Covers: successful response, malformed response,
timeout, connection failure, unavailable model, provider error mapping,
metadata mapping, and health-check variants. The real-endpoint integration
test lives separately in `tests/integration/test_ai_ollama_integration.py`
and is skip-safe when no real Ollama instance is reachable.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import LLMRequest
from kortex.engines.ai.ollama_provider import DEFAULT_OLLAMA_TIMEOUT_SECONDS, OllamaProvider


def _make_provider(handler) -> OllamaProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return OllamaProvider(base_url="http://localhost:11434", model_name="llama3", client=client)


def _request(prompt: str = "hello", **overrides) -> LLMRequest:
    fields = {
        "request_id": "req-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "prompt": prompt,
    }
    fields.update(overrides)
    return LLMRequest(**fields)


def test_provider_satisfies_base_ai_provider_contract() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    assert isinstance(provider, BaseAIProvider)


def test_metadata_mapping() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    meta = provider.metadata
    assert meta.provider_id == "ollama-llama3"
    assert meta.vendor == "ollama"
    assert meta.endpoint_type == "local_host"
    assert meta.url == "http://localhost:11434"
    assert meta.credential_requirement == "none"
    assert meta.supported_models == ["llama3"]
    assert provider.provider_id == "ollama-llama3"
    assert provider.supported_models == ["llama3"]


def test_constructor_rejects_empty_base_url() -> None:
    with pytest.raises(ValueError):
        OllamaProvider(base_url="", model_name="llama3")


def test_constructor_rejects_empty_model_name() -> None:
    with pytest.raises(ValueError):
        OllamaProvider(base_url="http://localhost:11434", model_name="")


def test_constructor_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError):
        OllamaProvider(base_url="http://localhost:11434", model_name="llama3", timeout_seconds=0)


def test_default_timeout_is_under_the_engines_own_outer_timeout() -> None:
    """Documented invariant: this provider's own timeout must stay under the
    AI Engine's 60s default outer generation timeout so it fails on its own
    terms rather than being cancelled mid-flight."""
    assert DEFAULT_OLLAMA_TIMEOUT_SECONDS < 60.0


@pytest.mark.asyncio
async def test_generate_text_successful_response_maps_correctly() -> None:
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        assert request.url.path == "/api/generate"
        return httpx.Response(
            200,
            json={
                "model": "llama3",
                "response": "Paris is the capital of France.",
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 7,
                "total_duration": 250_000_000,  # 250ms in nanoseconds
            },
        )

    provider = _make_provider(handler)
    request = _request(prompt="What is the capital of France?", system_instruction="Be concise.", max_tokens=64)
    response = await provider.generate_text(request)

    assert response.request_id == "req-1"
    assert response.text_content == "Paris is the capital of France."
    assert response.token_usage == {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}
    assert response.execution_time_ms == pytest.approx(250.0, rel=0.01)
    assert response.provider_id == "ollama-llama3"
    assert response.model_name == "llama3"
    assert response.degraded is False

    assert captured_payload["model"] == "llama3"
    assert captured_payload["prompt"] == "What is the capital of France?"
    assert captured_payload["stream"] is False
    assert captured_payload["system"] == "Be concise."
    assert captured_payload["options"]["num_predict"] == 64


@pytest.mark.asyncio
async def test_generate_text_omits_system_field_when_no_system_instruction() -> None:
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok"})

    provider = _make_provider(handler)
    await provider.generate_text(_request())
    assert "system" not in captured_payload


@pytest.mark.asyncio
async def test_generate_text_handles_missing_token_counts_gracefully() -> None:
    """Ollama's response schema doesn't guarantee count fields are present."""
    provider = _make_provider(lambda request: httpx.Response(200, json={"response": "hi"}))
    response = await provider.generate_text(_request())
    assert response.token_usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert response.execution_time_ms >= 0.0


@pytest.mark.asyncio
async def test_generate_text_malformed_json_response_raises_permanent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all {{{")

    provider = _make_provider(handler)
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_missing_response_field_raises_permanent_error() -> None:
    """A 200 response with valid JSON but no 'response' text field is malformed."""
    provider = _make_provider(lambda request: httpx.Response(200, json={"done": True}))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_404_model_not_found_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(PermanentProviderError, match="not available"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_500_raises_transient_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(500, text="internal server error"))
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_429_rate_limited_raises_transient_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(429, text="rate limited"))
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_unexpected_4xx_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(400, text="bad request"))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_connection_failure_raises_transient_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    provider = _make_provider(handler)
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_timeout_raises_transient_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Timed out", request=request)

    provider = _make_provider(handler)
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_transient_and_permanent_errors_are_distinct_types() -> None:
    """RetryPolicy.is_transient classifies by exception type -- these two
    error categories must never collapse into the same class."""
    assert not issubclass(TransientProviderError, PermanentProviderError)
    assert not issubclass(PermanentProviderError, TransientProviderError)


@pytest.mark.asyncio
async def test_generate_embeddings_raises_clear_not_supported_error() -> None:
    """M6.1 scope explicitly excludes embeddings -- must fail loudly, never
    fabricate vectors or silently succeed."""
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    with pytest.raises(PermanentProviderError, match="does not support embeddings"):
        await provider.generate_embeddings(["some text"])


@pytest.mark.asyncio
async def test_health_check_true_when_model_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3:latest"}, {"name": "mistral:latest"}]})

    provider = _make_provider(handler)
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_health_check_false_when_model_absent() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={"models": [{"name": "mistral:latest"}]}))
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    provider = _make_provider(handler)
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_on_non_200_status() -> None:
    provider = _make_provider(lambda request: httpx.Response(503, text="unavailable"))
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_health_check_false_on_malformed_json() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, content=b"not json"))
    assert await provider.health_check() is False
