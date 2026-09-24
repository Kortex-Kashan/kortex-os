"""Unit tests for `OpenRouterProvider`.

All tests here use `httpx.MockTransport` -- no real network I/O, no real
OpenRouter API key required.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import AIProviderConfig, LLMRequest
from kortex.engines.ai.openrouter_provider import (
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    OPENROUTER_PROVIDER_ID,
    OpenRouterProvider,
)

_KEY_TENANT_A = "sk-or-tenant-a-live-key"


class _FakeConfigReader:
    def __init__(self, configs: dict[tuple[str, str], Any] | None = None) -> None:
        self._configs = configs or {}

    async def get(self, tenant_id: str, provider_id: str) -> Any:
        return self._configs.get((tenant_id, provider_id))


class _FakeSecretGetter:
    def __init__(self, secrets: dict[tuple[str, str], str]) -> None:
        self._secrets = secrets
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, secret_handle: str, tenant_id: str) -> str:
        self.calls.append((secret_handle, tenant_id))
        return self._secrets[(secret_handle, tenant_id)]


def _real_resolver(
    *,
    tenant_a_key: str | None = _KEY_TENANT_A,
    tenant_a_default_model: str | None = None,
) -> tuple[TenantCredentialResolver, _FakeSecretGetter]:
    handle = "kortex/ai/providers/openrouter"
    configs: dict[tuple[str, str], AIProviderConfig] = {}
    secrets: dict[tuple[str, str], str] = {}

    if tenant_a_key is not None:
        configs[("tenant-a", "openrouter")] = AIProviderConfig(
            tenant_id="tenant-a",
            provider_id="openrouter",
            secret_handle=handle,
            default_model=tenant_a_default_model,
        )
        secrets[(handle, "tenant-a")] = tenant_a_key

    getter = _FakeSecretGetter(secrets)
    return TenantCredentialResolver(_FakeConfigReader(configs), getter), getter


def _make_provider(
    handler: Any,
    resolver: TenantCredentialResolver | None = None,
    **overrides: Any,
) -> OpenRouterProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    if resolver is None:
        resolver, _ = _real_resolver()
    return OpenRouterProvider(credential_resolver=resolver, client=client, **overrides)


def _request(prompt: str = "hello", tenant_id: str = "tenant-a", **overrides: Any) -> LLMRequest:
    fields: dict[str, Any] = {
        "request_id": "req-1",
        "tenant_id": tenant_id,
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "prompt": prompt,
        "temperature": 0.5,
    }
    fields.update(overrides)
    return LLMRequest(**fields)


@pytest.mark.asyncio
async def test_metadata_conformance() -> None:
    provider = _make_provider(lambda r: httpx.Response(200))
    assert isinstance(provider, BaseAIProvider)
    assert provider.provider_id == OPENROUTER_PROVIDER_ID
    assert provider.metadata.endpoint_type == "cloud"
    assert provider.metadata.vendor == "openrouter"
    assert provider.metadata.display_name == "OpenRouter"
    assert provider.metadata.url == DEFAULT_OPENROUTER_BASE_URL
    assert provider.metadata.credential_requirement == "api_key"
    assert provider.metadata.secret_handle == "kortex/ai/providers/openrouter"
    assert DEFAULT_OPENROUTER_MODEL in provider.metadata.supported_models


@pytest.mark.asyncio
async def test_generate_text_success() -> None:
    recorded_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        body = {
            "id": "gen-123",
            "model": "google/gemini-3.1-pro-preview",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello from OpenRouter!",
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
            },
        }
        return httpx.Response(200, json=body)

    provider = _make_provider(handler)
    response = await provider.generate_text(_request())

    assert response.text_content == "Hello from OpenRouter!"
    assert response.token_usage["prompt_tokens"] == 10
    assert response.token_usage["completion_tokens"] == 20
    assert response.token_usage["total_tokens"] == 30
    assert response.model_name == "google/gemini-3.1-pro-preview"

    assert len(recorded_requests) == 1
    req = recorded_requests[0]
    assert req.headers["authorization"] == f"Bearer {_KEY_TENANT_A}"
    assert req.headers["http-referer"] == "https://github.com/kortex-os"
    assert req.headers["x-title"] == "KORTEX"

    payload = json.loads(req.content)
    assert payload["model"] == DEFAULT_OPENROUTER_MODEL
    assert payload["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_discover_models_with_credential() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-credential"
        body = {
            "data": [
                {"id": "openai/gpt-4o"},
                {"id": "google/gemini-3.1-pro-preview"},
                {"id": "anthropic/claude-3.5-sonnet"},
            ]
        }
        return httpx.Response(200, json=body)

    provider = _make_provider(handler)
    models = await provider.discover_models("test-credential")

    model_ids = [m.model_id for m in models]
    assert model_ids == [
        "openai/gpt-4o",
        "google/gemini-3.1-pro-preview",
        "anthropic/claude-3.5-sonnet",
    ]


@pytest.mark.asyncio
async def test_discover_models_fallback_without_credential() -> None:
    provider = _make_provider(lambda r: httpx.Response(200))
    models = await provider.discover_models(None)
    assert [m.model_id for m in models] == [DEFAULT_OPENROUTER_MODEL]


@pytest.mark.asyncio
async def test_health_check() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"data": []}))
    assert await provider.health_check() is True


@pytest.mark.asyncio
async def test_test_connection_requires_credential() -> None:
    provider = _make_provider(lambda r: httpx.Response(200))
    with pytest.raises(PermanentProviderError):
        await provider.test_connection(None)


@pytest.mark.asyncio
async def test_error_status_mapping() -> None:
    def handler_401(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    provider = _make_provider(handler_401)
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())

    def handler_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limit exceeded")

    provider_429 = _make_provider(handler_429)
    with pytest.raises(TransientProviderError):
        await provider_429.generate_text(_request())
