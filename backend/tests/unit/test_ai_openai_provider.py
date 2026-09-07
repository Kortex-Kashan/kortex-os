"""Unit tests for `OpenAIProvider` (Phase B / B2).

All tests here use `httpx.MockTransport` -- no real network I/O, no real
OpenAI API key required, mirroring `test_ai_ollama_provider.py`'s exact
conventions (the primary behavioral precedent for this provider). Covers:
credential resolution (including the tenant-isolation property that a
Ollama-precedent test file has no reason to cover), successful response,
`model_id` propagation and precedence, malformed response, timeout,
connection failure, error-status mapping, embeddings deferral,
health-check vs. test_connection distinction, and model discovery.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import LLMRequest
from kortex.engines.ai.openai_provider import (
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENAI_TIMEOUT_SECONDS,
    SUPPORTED_OPENAI_MODELS,
    OpenAIProvider,
)

_KEY_TENANT_A = "sk-tenant-a-live-key"  # nosec - test fixture
_KEY_TENANT_B = "sk-tenant-b-live-key"  # nosec - test fixture


class _FakeConfigReader:
    def __init__(self, configs=None) -> None:
        self._configs = configs or {}

    async def get(self, tenant_id: str, provider_id: str):
        return self._configs.get((tenant_id, provider_id))


class _FakeSecretGetter:
    """Records every call so tenant-scoping can be asserted, not merely trusted."""

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
    tenant_b_key: str | None = None,
) -> tuple[TenantCredentialResolver, _FakeSecretGetter]:
    """A resolver wired against real `AIProviderConfig` rows and a recording
    secret getter -- exercises the REAL `TenantCredentialResolver.resolve`
    logic (tenant lookup, enabled gate, default_model passthrough), not a
    hand-rolled stand-in that only pretends to."""
    from kortex.engines.ai.models import AIProviderConfig

    handle = "kortex/ai/providers/openai"
    configs: dict[tuple[str, str], AIProviderConfig] = {}
    secrets: dict[tuple[str, str], str] = {}

    if tenant_a_key is not None:
        configs[("tenant-a", "openai")] = AIProviderConfig(
            tenant_id="tenant-a",
            provider_id="openai",
            secret_handle=handle,
            default_model=tenant_a_default_model,
        )
        secrets[(handle, "tenant-a")] = tenant_a_key
    if tenant_b_key is not None:
        configs[("tenant-b", "openai")] = AIProviderConfig(
            tenant_id="tenant-b", provider_id="openai", secret_handle=handle
        )
        secrets[(handle, "tenant-b")] = tenant_b_key

    getter = _FakeSecretGetter(secrets)
    return TenantCredentialResolver(_FakeConfigReader(configs), getter), getter


def _make_provider(handler, resolver: TenantCredentialResolver | None = None, **overrides) -> OpenAIProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    if resolver is None:
        resolver, _getter = _real_resolver()
    return OpenAIProvider(credential_resolver=resolver, client=client, **overrides)


def _request(prompt: str = "hello", tenant_id: str = "tenant-a", **overrides) -> LLMRequest:
    fields = {
        "request_id": "req-1",
        "tenant_id": tenant_id,
        "user_id": "user-1",
        "conversation_id": "conv-1",
        "prompt": prompt,
    }
    fields.update(overrides)
    return LLMRequest(**fields)


# ---------------------------------------------------------------------------
# Construction & metadata
# ---------------------------------------------------------------------------


def test_provider_satisfies_base_ai_provider_contract() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    assert isinstance(provider, BaseAIProvider)


def test_metadata_mapping() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    meta = provider.metadata
    assert meta.provider_id == "openai"
    assert meta.vendor == "openai"
    assert meta.endpoint_type == "cloud"
    assert meta.credential_requirement == "api_key"
    assert meta.secret_handle == "kortex/ai/providers/openai"
    assert meta.supported_models == list(SUPPORTED_OPENAI_MODELS)
    assert provider.provider_id == "openai"


def test_constructor_rejects_empty_base_url() -> None:
    resolver, _getter = _real_resolver()
    with pytest.raises(ValueError):
        OpenAIProvider(credential_resolver=resolver, base_url="")


def test_constructor_rejects_empty_default_model() -> None:
    resolver, _getter = _real_resolver()
    with pytest.raises(ValueError):
        OpenAIProvider(credential_resolver=resolver, default_model="")


def test_constructor_rejects_non_positive_timeout() -> None:
    resolver, _getter = _real_resolver()
    with pytest.raises(ValueError):
        OpenAIProvider(credential_resolver=resolver, timeout_seconds=0)


def test_default_timeout_is_under_the_engines_own_outer_timeout() -> None:
    assert DEFAULT_OPENAI_TIMEOUT_SECONDS < 60.0


# ---------------------------------------------------------------------------
# Credential resolution & tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_text_resolves_the_requesting_tenants_own_credential() -> None:
    """The credential reaching the outgoing HTTP request is the one
    belonging to `request.tenant_id` -- verified via the actual
    Authorization header sent, not by trusting a mock was invoked."""
    captured_auth: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_auth["Authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_chat_response("hi"))

    resolver, _getter = _real_resolver(tenant_a_key=_KEY_TENANT_A, tenant_b_key=_KEY_TENANT_B)
    provider = _make_provider(handler, resolver=resolver)

    await provider.generate_text(_request(tenant_id="tenant-a"))
    assert captured_auth["Authorization"] == f"Bearer {_KEY_TENANT_A}"

    await provider.generate_text(_request(tenant_id="tenant-b"))
    assert captured_auth["Authorization"] == f"Bearer {_KEY_TENANT_B}"


@pytest.mark.asyncio
async def test_generate_text_never_uses_another_tenants_credential() -> None:
    """Tenant B has no configuration at all; tenant A's real, valid
    credential must never be substituted for it."""
    resolver, getter = _real_resolver(tenant_a_key=_KEY_TENANT_A, tenant_b_key=None)
    provider = _make_provider(lambda request: httpx.Response(200, json=_chat_response("hi")), resolver=resolver)

    with pytest.raises(PermanentProviderError, match="no enabled, credentialed configuration"):
        await provider.generate_text(_request(tenant_id="tenant-b"))

    # The secret getter was never even asked to resolve tenant A's handle
    # under tenant B's identity -- the failure happens at the config-lookup
    # stage, before any secret read is attempted.
    assert all(tenant != "tenant-b" for _handle, tenant in getter.calls)


@pytest.mark.asyncio
async def test_generate_text_fails_closed_with_no_configuration_at_all() -> None:
    resolver, _getter = _real_resolver(tenant_a_key=None)
    provider = _make_provider(lambda request: httpx.Response(200, json=_chat_response("hi")), resolver=resolver)
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request(tenant_id="tenant-a"))


@pytest.mark.asyncio
async def test_the_credential_never_appears_in_a_raised_error_message() -> None:
    resolver, _getter = _real_resolver(tenant_a_key=_KEY_TENANT_A)
    provider = _make_provider(
        lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}), resolver=resolver
    )
    with pytest.raises(PermanentProviderError) as excinfo:
        await provider.generate_text(_request())
    assert _KEY_TENANT_A not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Request/response translation
# ---------------------------------------------------------------------------


def _chat_response(text: str, model: str = "gpt-4o-mini", prompt_tokens: int = 10, completion_tokens: int = 5) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@pytest.mark.asyncio
async def test_generate_text_successful_response_maps_correctly() -> None:
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json=_chat_response("Paris is the capital of France.", model="gpt-4o"))

    provider = _make_provider(handler)
    request = _request(prompt="What is the capital of France?", system_instruction="Be concise.", max_tokens=64)
    response = await provider.generate_text(request)

    assert response.request_id == "req-1"
    assert response.text_content == "Paris is the capital of France."
    assert response.token_usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert response.provider_id == "openai"
    assert response.model_name == "gpt-4o"
    assert response.degraded is False

    assert captured_payload["messages"] == [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "What is the capital of France?"},
    ]
    assert captured_payload["max_tokens"] == 64
    assert captured_payload["temperature"] == 0.7


@pytest.mark.asyncio
async def test_generate_text_omits_system_message_when_no_system_instruction() -> None:
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json=_chat_response("ok"))

    provider = _make_provider(handler)
    await provider.generate_text(_request())
    assert captured_payload["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_model_id_reaches_the_outgoing_request_when_pinned() -> None:
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json=_chat_response("ok", model="gpt-4.1"))

    provider = _make_provider(handler)
    await provider.generate_text(_request(model_id="gpt-4.1"))
    assert captured_payload["model"] == "gpt-4.1"


@pytest.mark.asyncio
async def test_tenants_configured_default_model_used_when_request_omits_model_id() -> None:
    """Precedence level 2: the tenant's own `AIProviderConfig.default_model`,
    read via `ResolvedCredential`, NOT the provider's global fallback."""
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json=_chat_response("ok"))

    resolver, _getter = _real_resolver(tenant_a_default_model="gpt-4.1-mini")
    provider = _make_provider(handler, resolver=resolver, default_model="gpt-4o-mini")
    await provider.generate_text(_request())
    assert captured_payload["model"] == "gpt-4.1-mini"


@pytest.mark.asyncio
async def test_provider_global_default_used_when_neither_request_nor_tenant_names_a_model() -> None:
    """Precedence level 3: the provider's own constructor default."""
    captured_payload: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.content)
        return httpx.Response(200, json=_chat_response("ok"))

    resolver, _getter = _real_resolver(tenant_a_default_model=None)
    provider = _make_provider(handler, resolver=resolver, default_model="gpt-4o")
    await provider.generate_text(_request())
    assert captured_payload["model"] == "gpt-4o"
    assert DEFAULT_OPENAI_MODEL != "gpt-4o"  # sanity: this really is the constructor override, not the module default


@pytest.mark.asyncio
async def test_generate_text_malformed_json_response_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, content=b"not json at all {{{"))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_missing_choices_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={"id": "x", "choices": []}))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_non_string_content_raises_permanent_error() -> None:
    """A refusal/tool-call-only response has no plain string content -- must
    fail loudly rather than crash on a raw TypeError or fabricate text."""
    provider = _make_provider(
        lambda request: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None}}]})
    )
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_handles_missing_usage_gracefully() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]}))
    response = await provider.generate_text(_request())
    assert response.token_usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# Error normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_text_401_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(401, json={"error": {"message": "invalid_api_key"}}))
    with pytest.raises(PermanentProviderError, match="invalid"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_403_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(403, json={"error": {"message": "forbidden"}}))
    with pytest.raises(PermanentProviderError, match="forbidden"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_404_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(404, json={"error": {"message": "model not found"}}))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request(model_id="gpt-4o"))


@pytest.mark.asyncio
async def test_generate_text_429_raises_transient_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(429, json={"error": {"message": "rate limited"}}))
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_500_raises_transient_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(500, text="internal error"))
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_generate_text_unexpected_4xx_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(400, json={"error": {"message": "invalid_request"}}))
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
async def test_permanent_credential_failure_is_not_classified_as_transient() -> None:
    """Ties directly to `ResilientAIProvider.retry_policy.is_transient`: a
    401 must never be retried."""
    from kortex.engines.ai.resilience import RetryPolicy

    provider = _make_provider(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(PermanentProviderError) as excinfo:
        await provider.generate_text(_request())
    assert RetryPolicy().is_transient(excinfo.value) is False


@pytest.mark.asyncio
async def test_rate_limit_failure_is_classified_as_transient() -> None:
    from kortex.engines.ai.resilience import RetryPolicy

    provider = _make_provider(lambda request: httpx.Response(429, json={"error": {"message": "rate limited"}}))
    with pytest.raises(TransientProviderError) as excinfo:
        await provider.generate_text(_request())
    assert RetryPolicy().is_transient(excinfo.value) is True


# ---------------------------------------------------------------------------
# Embeddings (deferred -- see module docstring)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_embeddings_raises_clear_not_supported_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={}))
    with pytest.raises(PermanentProviderError, match="does not support embeddings"):
        await provider.generate_embeddings(["some text"])


# ---------------------------------------------------------------------------
# health_check vs. test_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_is_unauthenticated_reachability_only() -> None:
    captured_headers: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(401)  # even an auth-rejected response proves reachability

    provider = _make_provider(handler)
    assert await provider.health_check() is True
    assert "authorization" not in {k.lower() for k in captured_headers}


@pytest.mark.asyncio
async def test_health_check_false_on_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    provider = _make_provider(handler)
    assert await provider.health_check() is False


@pytest.mark.asyncio
async def test_test_connection_requires_a_credential() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={"data": []}))
    with pytest.raises(PermanentProviderError, match="requires a credential"):
        await provider.test_connection(None)


@pytest.mark.asyncio
async def test_test_connection_true_with_valid_credential() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == f"Bearer {_KEY_TENANT_A}"
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})

    provider = _make_provider(handler)
    assert await provider.test_connection(_KEY_TENANT_A) is True


@pytest.mark.asyncio
async def test_test_connection_raises_permanent_error_for_invalid_credential() -> None:
    """An invalid credential is a diagnosable failure, not a bare `False` --
    the caller (the `kortex.ai.provider.test` capability handler) needs the
    reason to report something actionable."""
    provider = _make_provider(lambda request: httpx.Response(401, json={"error": {"message": "invalid"}}))
    with pytest.raises(PermanentProviderError):
        await provider.test_connection("sk-invalid")  # nosec - test fixture


@pytest.mark.asyncio
async def test_test_connection_raises_transient_error_on_rate_limit() -> None:
    provider = _make_provider(lambda request: httpx.Response(429, json={"error": {"message": "rate limited"}}))
    with pytest.raises(TransientProviderError):
        await provider.test_connection("sk-whatever")  # nosec - test fixture


# ---------------------------------------------------------------------------
# Model discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_models_live_with_credential() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o", "object": "model"}, {"id": "gpt-4o-mini", "object": "model"}]},
        )

    provider = _make_provider(handler)
    models = await provider.discover_models(_KEY_TENANT_A)
    assert {m.model_id for m in models} == {"gpt-4o", "gpt-4o-mini"}
    assert all(m.provider_id == "openai" for m in models)


@pytest.mark.asyncio
async def test_discover_models_falls_back_to_static_list_without_a_credential() -> None:
    """No live source is reachable unauthenticated, so this must return the
    static allow-list, not an empty list or a fabricated one."""
    provider = _make_provider(lambda request: httpx.Response(200, json={"data": []}))
    models = await provider.discover_models(None)
    assert {m.model_id for m in models} == set(SUPPORTED_OPENAI_MODELS)


@pytest.mark.asyncio
async def test_discover_models_malformed_response_raises_permanent_error() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json={"unexpected": "shape"}))
    with pytest.raises(PermanentProviderError):
        await provider.discover_models(_KEY_TENANT_A)


@pytest.mark.asyncio
async def test_discover_models_ignores_malformed_entries_defensively() -> None:
    provider = _make_provider(
        lambda request: httpx.Response(
            200, json={"data": [{"id": "gpt-4o"}, {"no_id": "oops"}, "not-a-dict", {"id": 123}]}
        )
    )
    models = await provider.discover_models(_KEY_TENANT_A)
    assert [m.model_id for m in models] == ["gpt-4o"]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_closes_an_owned_client() -> None:
    resolver, _getter = _real_resolver()
    provider = OpenAIProvider(credential_resolver=resolver)
    assert provider._owns_client is True
    await provider.aclose()
    assert provider._client.is_closed


@pytest.mark.asyncio
async def test_aclose_does_not_close_an_injected_client() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client = httpx.AsyncClient(transport=transport)
    resolver, _getter = _real_resolver()
    provider = OpenAIProvider(credential_resolver=resolver, client=client)
    await provider.aclose()
    assert client.is_closed is False
    await client.aclose()
