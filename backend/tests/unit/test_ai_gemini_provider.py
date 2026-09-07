"""Unit tests for `GeminiProvider` (Phase B / B3).

All tests use `httpx.MockTransport` -- no real network I/O, no real Gemini API
key required -- mirroring `test_ai_openai_provider.py`'s conventions exactly.
Every assertion checks real translated data (outbound headers, outbound JSON
payloads, parsed responses, raised exception types re-classified through the
live `RetryPolicy`); none assert merely that a mock was called.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.gemini_provider import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GEMINI_TIMEOUT_SECONDS,
    SUPPORTED_GEMINI_MODELS,
    GeminiProvider,
)
from kortex.engines.ai.models import AIProviderConfig, LLMRequest
from kortex.engines.ai.resilience import RetryPolicy

_KEY_TENANT_A = "gemini-key-tenant-a"  # nosec - test fixture
_KEY_TENANT_B = "gemini-key-tenant-b"  # nosec - test fixture
_HANDLE = "kortex/ai/providers/gemini"


class _FakeConfigReader:
    def __init__(self, configs: dict[tuple[str, str], AIProviderConfig]) -> None:
        self._configs = configs

    async def get(self, tenant_id: str, provider_id: str) -> AIProviderConfig | None:
        return self._configs.get((tenant_id, provider_id))


class _RecordingSecretGetter:
    """Records every resolution so tenant scoping is asserted, not assumed."""

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
) -> tuple[TenantCredentialResolver, _RecordingSecretGetter]:
    """A resolver over real `AIProviderConfig` rows, exercising the REAL
    `TenantCredentialResolver.resolve` logic rather than a stand-in."""
    configs: dict[tuple[str, str], AIProviderConfig] = {}
    secrets: dict[tuple[str, str], str] = {}
    if tenant_a_key is not None:
        configs[("tenant-a", "gemini")] = AIProviderConfig(
            tenant_id="tenant-a",
            provider_id="gemini",
            secret_handle=_HANDLE,
            default_model=tenant_a_default_model,
        )
        secrets[(_HANDLE, "tenant-a")] = tenant_a_key
    if tenant_b_key is not None:
        configs[("tenant-b", "gemini")] = AIProviderConfig(
            tenant_id="tenant-b", provider_id="gemini", secret_handle=_HANDLE
        )
        secrets[(_HANDLE, "tenant-b")] = tenant_b_key
    getter = _RecordingSecretGetter(secrets)
    return TenantCredentialResolver(_FakeConfigReader(configs), getter), getter


def _make_provider(handler, resolver: TenantCredentialResolver | None = None, **overrides) -> GeminiProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    if resolver is None:
        resolver, _getter = _real_resolver()
    return GeminiProvider(credential_resolver=resolver, client=client, **overrides)


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


def _generate_response(text: str, model: str = "gemini-2.5-flash", prompt_tokens: int = 11, out_tokens: int = 7):
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": out_tokens,
            "totalTokenCount": prompt_tokens + out_tokens,
        },
        "modelVersion": model,
    }


# ---------------------------------------------------------------------------
# Metadata & construction
# ---------------------------------------------------------------------------


def test_provider_satisfies_base_ai_provider_contract() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={}))
    assert isinstance(provider, BaseAIProvider)


def test_metadata_mapping() -> None:
    meta = _make_provider(lambda r: httpx.Response(200, json={})).metadata
    assert meta.provider_id == "gemini"
    assert meta.vendor == "google"
    assert meta.endpoint_type == "cloud"
    assert meta.credential_requirement == "api_key"
    assert meta.secret_handle == _HANDLE
    assert meta.supported_models == list(SUPPORTED_GEMINI_MODELS)


def test_constructor_rejects_invalid_arguments() -> None:
    resolver, _g = _real_resolver()
    with pytest.raises(ValueError):
        GeminiProvider(credential_resolver=resolver, base_url="")
    with pytest.raises(ValueError):
        GeminiProvider(credential_resolver=resolver, default_model="")
    with pytest.raises(ValueError):
        GeminiProvider(credential_resolver=resolver, timeout_seconds=0)


def test_default_timeout_is_under_the_engines_own_outer_timeout() -> None:
    assert DEFAULT_GEMINI_TIMEOUT_SECONDS < 60.0


# ---------------------------------------------------------------------------
# Credential usage & tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_is_sent_in_the_header_and_never_in_the_url() -> None:
    """The `?key=` query form is a credential in a URL that could reach an
    access log; this provider must always use the header form."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers.get("x-goog-api-key")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_generate_response("hi"))

    await _make_provider(handler).generate_text(_request())

    assert seen["header"] == _KEY_TENANT_A
    assert _KEY_TENANT_A not in str(seen["url"])
    assert "key=" not in str(seen["url"])


@pytest.mark.asyncio
async def test_each_tenant_gets_its_own_credential() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-goog-api-key"))
        return httpx.Response(200, json=_generate_response("hi"))

    resolver, _g = _real_resolver(tenant_a_key=_KEY_TENANT_A, tenant_b_key=_KEY_TENANT_B)
    provider = _make_provider(handler, resolver=resolver)

    await provider.generate_text(_request(tenant_id="tenant-a"))
    await provider.generate_text(_request(tenant_id="tenant-b"))

    assert seen == [_KEY_TENANT_A, _KEY_TENANT_B]


@pytest.mark.asyncio
async def test_unconfigured_tenant_never_borrows_another_tenants_credential() -> None:
    resolver, getter = _real_resolver(tenant_a_key=_KEY_TENANT_A, tenant_b_key=None)
    provider = _make_provider(lambda r: httpx.Response(200, json=_generate_response("hi")), resolver=resolver)

    with pytest.raises(PermanentProviderError, match="no enabled, credentialed configuration"):
        await provider.generate_text(_request(tenant_id="tenant-b"))

    assert all(tenant != "tenant-b" for _h, tenant in getter.calls)


@pytest.mark.asyncio
async def test_credential_never_appears_in_a_raised_error() -> None:
    provider = _make_provider(lambda r: httpx.Response(401, json={"error": {"code": 401, "message": "bad key"}}))
    with pytest.raises(PermanentProviderError) as excinfo:
        await provider.generate_text(_request())
    assert _KEY_TENANT_A not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Outbound request translation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_outbound_request_shape() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_generate_response("Paris.", model="gemini-2.5-pro"))

    provider = _make_provider(handler)
    await provider.generate_text(
        _request(prompt="Capital of France?", system_instruction="Be terse.", max_tokens=64, model_id="gemini-2.5-pro")
    )

    # Model id is carried in the URL path, not the body -- Gemini-specific.
    assert captured["path"] == "/v1beta/models/gemini-2.5-pro:generateContent"
    assert captured["body"]["contents"] == [{"role": "user", "parts": [{"text": "Capital of France?"}]}]
    assert captured["body"]["systemInstruction"] == {"parts": [{"text": "Be terse."}]}
    assert captured["body"]["generationConfig"]["maxOutputTokens"] == 64
    assert captured["body"]["generationConfig"]["temperature"] == 0.7


@pytest.mark.asyncio
async def test_system_instruction_omitted_when_absent() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_generate_response("ok"))

    await _make_provider(handler).generate_text(_request())
    assert "systemInstruction" not in captured


@pytest.mark.asyncio
async def test_max_output_tokens_omitted_when_request_leaves_it_unset() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_generate_response("ok"))

    await _make_provider(handler).generate_text(_request())
    assert "maxOutputTokens" not in captured["generationConfig"]


# ---------------------------------------------------------------------------
# Model precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_precedence_request_pin_wins() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_generate_response("ok"))

    resolver, _g = _real_resolver(tenant_a_default_model="gemini-2.5-flash")
    provider = _make_provider(handler, resolver=resolver, default_model="gemini-3.8-flash")
    await provider.generate_text(_request(model_id="gemini-2.5-pro"))
    assert "gemini-2.5-pro:generateContent" in seen["path"]


@pytest.mark.asyncio
async def test_model_precedence_tenant_default_beats_provider_default() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_generate_response("ok"))

    resolver, _g = _real_resolver(tenant_a_default_model="gemini-3.7-flash")
    provider = _make_provider(handler, resolver=resolver, default_model="gemini-3.8-flash")
    await provider.generate_text(_request())
    assert "gemini-3.7-flash:generateContent" in seen["path"]


@pytest.mark.asyncio
async def test_model_precedence_falls_back_to_provider_default() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_generate_response("ok"))

    resolver, _g = _real_resolver(tenant_a_default_model=None)
    provider = _make_provider(handler, resolver=resolver, default_model="gemini-3.8-flash")
    await provider.generate_text(_request())
    assert "gemini-3.8-flash:generateContent" in seen["path"]
    assert DEFAULT_GEMINI_MODEL != "gemini-3.8-flash"  # proves the override, not the module default


# ---------------------------------------------------------------------------
# Inbound response translation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_response_maps_correctly() -> None:
    provider = _make_provider(
        lambda r: httpx.Response(
            200, json=_generate_response("Paris.", model="gemini-2.5-pro", prompt_tokens=11, out_tokens=7)
        )
    )
    response = await provider.generate_text(_request())

    assert response.request_id == "req-1"
    assert response.text_content == "Paris."
    assert response.token_usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert response.provider_id == "gemini"
    assert response.model_name == "gemini-2.5-pro"
    assert response.degraded is False


@pytest.mark.asyncio
async def test_multiple_text_parts_are_joined_faithfully() -> None:
    payload = {"candidates": [{"content": {"parts": [{"text": "Hello "}, {"text": "world"}]}, "finishReason": "STOP"}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    assert (await provider.generate_text(_request())).text_content == "Hello world"


@pytest.mark.asyncio
async def test_missing_usage_metadata_degrades_to_zeros() -> None:
    payload = {"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    usage = (await provider.generate_text(_request())).token_usage
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


# ---------------------------------------------------------------------------
# Malformed responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_response_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, content=b"not json {{"))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_missing_candidates_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"usageMetadata": {}}))
    with pytest.raises(PermanentProviderError, match="candidates"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_candidate_without_text_part_raises_permanent() -> None:
    payload = {"candidates": [{"content": {"parts": [{"inlineData": {}}]}, "finishReason": "STOP"}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(PermanentProviderError, match="no text part"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_response_that_is_not_an_object_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json=["unexpected"]))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


# ---------------------------------------------------------------------------
# Gemini-specific: the two content-refusal shapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_blocked_before_generation_raises_permanent() -> None:
    """`promptFeedback.blockReason` with no candidates at all -- Gemini's
    first refusal shape. Must never surface as empty text."""
    payload = {"promptFeedback": {"blockReason": "SAFETY", "safetyRatings": []}}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(PermanentProviderError, match="blocked the prompt"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"])
async def test_blocking_finish_reason_raises_permanent(reason: str) -> None:
    """Gemini's second refusal shape: generation started, then was withheld."""
    payload = {"candidates": [{"content": {"parts": []}, "finishReason": reason}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(PermanentProviderError, match="withheld the response"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_max_tokens_finish_reason_is_not_treated_as_a_refusal() -> None:
    """A truncated-but-usable answer must still be returned -- `MAX_TOKENS` is
    not in the blocking set."""
    payload = {"candidates": [{"content": {"parts": [{"text": "partial"}]}, "finishReason": "MAX_TOKENS"}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    assert (await provider.generate_text(_request())).text_content == "partial"


# ---------------------------------------------------------------------------
# HTTP error normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_client_errors_are_permanent(status: int) -> None:
    provider = _make_provider(
        lambda r: httpx.Response(status, json={"error": {"code": status, "message": "nope", "status": "X"}})
    )
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 503, 504])
async def test_rate_limit_and_server_errors_are_transient(status: int) -> None:
    provider = _make_provider(
        lambda r: httpx.Response(status, json={"error": {"code": status, "message": "later", "status": "X"}})
    )
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_error_message_is_extracted_from_googles_envelope() -> None:
    """AIP-193 shape: `{"error": {"code": <numeric>, "message": ..., "status": ...}}`."""
    provider = _make_provider(
        lambda r: httpx.Response(
            400, json={"error": {"code": 400, "message": "quota unit missing", "status": "INVALID_ARGUMENT"}}
        )
    )
    with pytest.raises(PermanentProviderError, match="quota unit missing"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_unparseable_error_body_still_classifies_by_status() -> None:
    """A changed or absent envelope must never turn a clean status
    classification into a parse failure."""
    provider = _make_provider(lambda r: httpx.Response(503, content=b"<html>gateway</html>"))
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_connect_error_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(TransientProviderError):
        await _make_provider(handler).generate_text(_request())


@pytest.mark.asyncio
async def test_timeout_is_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TransientProviderError):
        await _make_provider(handler).generate_text(_request())


@pytest.mark.asyncio
async def test_error_classes_line_up_with_the_live_retry_policy() -> None:
    """Ties the mapping to the actual consumer: `RetryPolicy.is_transient` is
    what decides whether `ResilientAIProvider` retries."""
    policy = RetryPolicy()

    perm = _make_provider(lambda r: httpx.Response(401, json={"error": {"message": "bad"}}))
    with pytest.raises(PermanentProviderError) as p_exc:
        await perm.generate_text(_request())
    assert policy.is_transient(p_exc.value) is False

    trans = _make_provider(lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}))
    with pytest.raises(TransientProviderError) as t_exc:
        await trans.generate_text(_request())
    assert policy.is_transient(t_exc.value) is True


# ---------------------------------------------------------------------------
# Embeddings (deferred)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_embeddings_fails_explicitly() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={}))
    with pytest.raises(PermanentProviderError, match="does not support embeddings"):
        await provider.generate_embeddings(["text"])


# ---------------------------------------------------------------------------
# health_check vs. test_connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_is_unauthenticated_reachability_only() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(401)

    assert await _make_provider(handler).health_check() is True
    assert "x-goog-api-key" not in captured


@pytest.mark.asyncio
async def test_health_check_false_on_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await _make_provider(handler).health_check() is False


@pytest.mark.asyncio
async def test_test_connection_requires_a_credential() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"models": []}))
    with pytest.raises(PermanentProviderError, match="requires a credential"):
        await provider.test_connection(None)


@pytest.mark.asyncio
async def test_test_connection_succeeds_and_never_generates() -> None:
    """The probe must hit the catalog endpoint, never `:generateContent`."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"models": []})

    assert await _make_provider(handler).test_connection(_KEY_TENANT_A) is True
    assert paths == ["/v1beta/models"]
    assert all("generateContent" not in p for p in paths)


@pytest.mark.asyncio
async def test_test_connection_authentication_failure_is_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(401, json={"error": {"message": "invalid"}}))
    with pytest.raises(PermanentProviderError):
        await provider.test_connection("wrong-key")  # nosec - test fixture


@pytest.mark.asyncio
async def test_test_connection_rate_limit_is_transient() -> None:
    provider = _make_provider(lambda r: httpx.Response(429, json={"error": {"message": "slow"}}))
    with pytest.raises(TransientProviderError):
        await provider.test_connection("any-key")  # nosec - test fixture


# ---------------------------------------------------------------------------
# Model discovery: filtering + complete pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_filters_to_generate_content_models_only() -> None:
    """`supportedGenerationMethods` filtering -- the catalog also lists
    embedding-only families this adapter cannot generate with."""
    payload = {
        "models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
        ]
    }
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    models = await provider.discover_models(_KEY_TENANT_A)

    assert {m.model_id for m in models} == {"gemini-2.5-flash", "gemini-2.5-pro"}
    assert all(m.provider_id == "gemini" for m in models)


@pytest.mark.asyncio
async def test_discovery_follows_pagination_to_exhaustion() -> None:
    """Complete pagination (B3 decision 3): every page is fetched and the
    cursor is actually propagated as `pageToken`."""
    seen_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        seen_tokens.append(token)
        if token is None:
            return httpx.Response(
                200,
                json={
                    "models": [{"name": "models/m1", "supportedGenerationMethods": ["generateContent"]}],
                    "nextPageToken": "page-2",
                },
            )
        if token == "page-2":
            return httpx.Response(
                200,
                json={
                    "models": [{"name": "models/m2", "supportedGenerationMethods": ["generateContent"]}],
                    "nextPageToken": "page-3",
                },
            )
        return httpx.Response(
            200, json={"models": [{"name": "models/m3", "supportedGenerationMethods": ["generateContent"]}]}
        )

    models = await _make_provider(handler).discover_models(_KEY_TENANT_A)

    assert [m.model_id for m in models] == ["m1", "m2", "m3"]
    assert seen_tokens == [None, "page-2", "page-3"]


@pytest.mark.asyncio
async def test_discovery_stops_on_empty_next_page_token() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "models": [{"name": "models/only", "supportedGenerationMethods": ["generateContent"]}],
                "nextPageToken": "",
            },
        )

    models = await _make_provider(handler).discover_models(_KEY_TENANT_A)
    assert [m.model_id for m in models] == ["only"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_discovery_without_credential_falls_back_to_static_list() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"models": []}))
    models = await provider.discover_models(None)
    assert {m.model_id for m in models} == set(SUPPORTED_GEMINI_MODELS)


@pytest.mark.asyncio
async def test_discovery_malformed_payload_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(PermanentProviderError, match="'models' list"):
        await provider.discover_models(_KEY_TENANT_A)


@pytest.mark.asyncio
async def test_discovery_skips_malformed_entries_without_losing_good_ones() -> None:
    payload = {
        "models": [
            "not-a-dict",
            {"no_name": True, "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/good", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/no-methods"},
        ]
    }
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    models = await provider.discover_models(_KEY_TENANT_A)
    assert [m.model_id for m in models] == ["good"]


@pytest.mark.asyncio
async def test_discovery_bounded_against_a_non_terminating_cursor() -> None:
    """A server that always returns a fresh `nextPageToken` must not hang the
    caller forever."""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200,
            json={
                "models": [{"name": f"models/m{counter['n']}", "supportedGenerationMethods": ["generateContent"]}],
                "nextPageToken": f"tok-{counter['n']}",
            },
        )

    with pytest.raises(TransientProviderError, match="without exhausting its cursor"):
        await _make_provider(handler).discover_models(_KEY_TENANT_A)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_closes_only_an_owned_client() -> None:
    resolver, _g = _real_resolver()
    owned = GeminiProvider(credential_resolver=resolver)
    assert owned._owns_client is True
    await owned.aclose()
    assert owned._client.is_closed

    injected_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    borrowed = GeminiProvider(credential_resolver=resolver, client=injected_client)
    await borrowed.aclose()
    assert injected_client.is_closed is False
    await injected_client.aclose()
