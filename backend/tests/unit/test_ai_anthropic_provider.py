"""Unit tests for `AnthropicProvider` (Phase B / B3).

All tests use `httpx.MockTransport` -- no real network I/O, no real Anthropic
API key required -- mirroring `test_ai_openai_provider.py`'s conventions
exactly. Every assertion checks real translated data (outbound headers,
outbound JSON payloads, parsed responses, raised exception types
re-classified through the live `RetryPolicy`); none assert merely that a mock
was called.

The two B3 capability decisions get dedicated, mutation-sensitive coverage:
`temperature` is never forwarded, and `max_tokens` always appears (defaulting
to 4096) because Anthropic's API requires it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kortex.engines.ai.anthropic_provider import (
    ANTHROPIC_API_VERSION,
    DEFAULT_ANTHROPIC_MAX_TOKENS,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
    SUPPORTED_ANTHROPIC_MODELS,
    AnthropicProvider,
)
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import AIProviderConfig, LLMRequest
from kortex.engines.ai.resilience import RetryPolicy

_KEY_TENANT_A = "anthropic-key-tenant-a"  # nosec - test fixture
_KEY_TENANT_B = "anthropic-key-tenant-b"  # nosec - test fixture
_HANDLE = "kortex/ai/providers/anthropic"


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
        configs[("tenant-a", "anthropic")] = AIProviderConfig(
            tenant_id="tenant-a",
            provider_id="anthropic",
            secret_handle=_HANDLE,
            default_model=tenant_a_default_model,
        )
        secrets[(_HANDLE, "tenant-a")] = tenant_a_key
    if tenant_b_key is not None:
        configs[("tenant-b", "anthropic")] = AIProviderConfig(
            tenant_id="tenant-b", provider_id="anthropic", secret_handle=_HANDLE
        )
        secrets[(_HANDLE, "tenant-b")] = tenant_b_key
    getter = _RecordingSecretGetter(secrets)
    return TenantCredentialResolver(_FakeConfigReader(configs), getter), getter


def _make_provider(handler, resolver: TenantCredentialResolver | None = None, **overrides) -> AnthropicProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    if resolver is None:
        resolver, _getter = _real_resolver()
    return AnthropicProvider(credential_resolver=resolver, client=client, **overrides)


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


def _message_response(text: str, model: str = "claude-opus-5", in_tokens: int = 13, out_tokens: int = 9):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": in_tokens, "output_tokens": out_tokens},
    }


# ---------------------------------------------------------------------------
# Metadata & construction
# ---------------------------------------------------------------------------


def test_provider_satisfies_base_ai_provider_contract() -> None:
    assert isinstance(_make_provider(lambda r: httpx.Response(200, json={})), BaseAIProvider)


def test_metadata_mapping() -> None:
    meta = _make_provider(lambda r: httpx.Response(200, json={})).metadata
    assert meta.provider_id == "anthropic"
    assert meta.vendor == "anthropic"
    assert meta.endpoint_type == "cloud"
    assert meta.credential_requirement == "api_key"
    assert meta.secret_handle == _HANDLE
    assert meta.supported_models == list(SUPPORTED_ANTHROPIC_MODELS)


def test_constructor_rejects_invalid_arguments() -> None:
    resolver, _g = _real_resolver()
    with pytest.raises(ValueError):
        AnthropicProvider(credential_resolver=resolver, base_url="")
    with pytest.raises(ValueError):
        AnthropicProvider(credential_resolver=resolver, default_model="")
    with pytest.raises(ValueError):
        AnthropicProvider(credential_resolver=resolver, default_max_tokens=0)
    with pytest.raises(ValueError):
        AnthropicProvider(credential_resolver=resolver, timeout_seconds=0)


def test_default_timeout_is_under_the_engines_own_outer_timeout() -> None:
    assert DEFAULT_ANTHROPIC_TIMEOUT_SECONDS < 60.0


def test_default_max_tokens_is_the_architect_specified_value() -> None:
    """Binding B3 decision 2: fixed at 4096, not tenant-configurable."""
    assert DEFAULT_ANTHROPIC_MAX_TOKENS == 4096


# ---------------------------------------------------------------------------
# Credential usage, required headers & tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_headers_are_sent() -> None:
    """`x-api-key` plus the mandatory `anthropic-version`; the credential must
    never land in the URL."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        seen["version"] = request.headers.get("anthropic-version")
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_message_response("hi"))

    await _make_provider(handler).generate_text(_request())

    assert seen["key"] == _KEY_TENANT_A
    assert seen["version"] == ANTHROPIC_API_VERSION == "2023-06-01"
    assert _KEY_TENANT_A not in str(seen["url"])


@pytest.mark.asyncio
async def test_each_tenant_gets_its_own_credential() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-api-key"))
        return httpx.Response(200, json=_message_response("hi"))

    resolver, _g = _real_resolver(tenant_a_key=_KEY_TENANT_A, tenant_b_key=_KEY_TENANT_B)
    provider = _make_provider(handler, resolver=resolver)

    await provider.generate_text(_request(tenant_id="tenant-a"))
    await provider.generate_text(_request(tenant_id="tenant-b"))

    assert seen == [_KEY_TENANT_A, _KEY_TENANT_B]


@pytest.mark.asyncio
async def test_unconfigured_tenant_never_borrows_another_tenants_credential() -> None:
    resolver, getter = _real_resolver(tenant_a_key=_KEY_TENANT_A, tenant_b_key=None)
    provider = _make_provider(lambda r: httpx.Response(200, json=_message_response("hi")), resolver=resolver)

    with pytest.raises(PermanentProviderError, match="no enabled, credentialed configuration"):
        await provider.generate_text(_request(tenant_id="tenant-b"))

    assert all(tenant != "tenant-b" for _h, tenant in getter.calls)


@pytest.mark.asyncio
async def test_credential_never_appears_in_a_raised_error() -> None:
    provider = _make_provider(
        lambda r: httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
        )
    )
    with pytest.raises(PermanentProviderError) as excinfo:
        await provider.generate_text(_request())
    assert _KEY_TENANT_A not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Outbound request translation, incl. the two B3 capability decisions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exact_outbound_request_shape() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_message_response("Paris."))

    await _make_provider(handler).generate_text(
        _request(prompt="Capital of France?", system_instruction="Be terse.", max_tokens=64, model_id="claude-sonnet-5")
    )

    assert captured["path"] == "/v1/messages"
    assert captured["body"]["model"] == "claude-sonnet-5"
    assert captured["body"]["messages"] == [{"role": "user", "content": "Capital of France?"}]
    # `system` is a TOP-LEVEL field, never a message with role "system".
    assert captured["body"]["system"] == "Be terse."
    assert all(m["role"] != "system" for m in captured["body"]["messages"])


@pytest.mark.asyncio
async def test_temperature_is_never_forwarded() -> None:
    """Binding B3 decision 1. `LLMRequest.temperature` always has a value
    (default 0.7), and Anthropic's newest tier rejects the field with a 400 --
    so this adapter must omit it entirely. Mutation-sensitive: adding
    `temperature` back to the payload fails this immediately."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    request = _request(temperature=0.2)
    assert request.temperature == 0.2  # the request really does carry one

    await _make_provider(handler).generate_text(request)

    assert "temperature" not in captured
    assert "top_p" not in captured
    assert "top_k" not in captured


@pytest.mark.asyncio
async def test_max_tokens_defaults_to_4096_when_request_leaves_it_unset() -> None:
    """Binding B3 decision 2: Anthropic REQUIRES `max_tokens`, so it must
    always be present in the payload."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    request = _request()
    assert request.max_tokens is None  # nothing supplied by the caller

    await _make_provider(handler).generate_text(request)

    assert captured["max_tokens"] == DEFAULT_ANTHROPIC_MAX_TOKENS == 4096


@pytest.mark.asyncio
async def test_explicit_max_tokens_is_forwarded_unchanged() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    await _make_provider(handler).generate_text(_request(max_tokens=256))
    assert captured["max_tokens"] == 256


@pytest.mark.asyncio
async def test_provider_level_max_tokens_default_is_overridable_at_construction() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    await _make_provider(handler, default_max_tokens=1024).generate_text(_request())
    assert captured["max_tokens"] == 1024


@pytest.mark.asyncio
async def test_system_field_omitted_when_absent() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    await _make_provider(handler).generate_text(_request())
    assert "system" not in captured


# ---------------------------------------------------------------------------
# Model precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_precedence_request_pin_wins() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    resolver, _g = _real_resolver(tenant_a_default_model="claude-haiku-4-5")
    provider = _make_provider(handler, resolver=resolver, default_model="claude-opus-4-8")
    await provider.generate_text(_request(model_id="claude-sonnet-5"))
    assert captured["model"] == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_model_precedence_tenant_default_beats_provider_default() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    resolver, _g = _real_resolver(tenant_a_default_model="claude-haiku-4-5")
    provider = _make_provider(handler, resolver=resolver, default_model="claude-opus-4-8")
    await provider.generate_text(_request())
    assert captured["model"] == "claude-haiku-4-5"


@pytest.mark.asyncio
async def test_model_precedence_falls_back_to_provider_default() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=_message_response("ok"))

    resolver, _g = _real_resolver(tenant_a_default_model=None)
    provider = _make_provider(handler, resolver=resolver, default_model="claude-opus-4-8")
    await provider.generate_text(_request())
    assert captured["model"] == "claude-opus-4-8"
    assert DEFAULT_ANTHROPIC_MODEL != "claude-opus-4-8"  # proves the override, not the module default


# ---------------------------------------------------------------------------
# Inbound response translation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_response_maps_correctly() -> None:
    provider = _make_provider(
        lambda r: httpx.Response(
            200, json=_message_response("Paris.", model="claude-sonnet-5", in_tokens=13, out_tokens=9)
        )
    )
    response = await provider.generate_text(_request())

    assert response.request_id == "req-1"
    assert response.text_content == "Paris."
    # Anthropic reports input_tokens/output_tokens; KORTEX reports prompt/completion.
    assert response.token_usage == {"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22}
    assert response.provider_id == "anthropic"
    assert response.model_name == "claude-sonnet-5"
    assert response.degraded is False


@pytest.mark.asyncio
async def test_only_text_blocks_are_extracted_and_joined() -> None:
    """`content` is a list of typed blocks; non-text blocks must be skipped,
    and a multi-block answer reconstructed in order."""
    payload = {
        "content": [
            {"type": "thinking", "thinking": "internal"},
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    assert (await provider.generate_text(_request())).text_content == "Hello world"


@pytest.mark.asyncio
async def test_missing_usage_degrades_to_zeros() -> None:
    payload = {"content": [{"type": "text", "text": "hi"}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    usage = (await provider.generate_text(_request())).token_usage
    assert usage == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


@pytest.mark.asyncio
async def test_refusal_stop_reason_raises_permanent() -> None:
    """A refusal must never surface as empty text -- empty is
    indistinguishable from a legitimately short answer."""
    payload = {"content": [], "stop_reason": "refusal"}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(PermanentProviderError, match="declined to answer"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_max_tokens_stop_reason_still_returns_the_partial_answer() -> None:
    payload = {"content": [{"type": "text", "text": "partial"}], "stop_reason": "max_tokens"}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    assert (await provider.generate_text(_request())).text_content == "partial"


# ---------------------------------------------------------------------------
# Malformed responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_json_response_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, content=b"not json {{"))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_missing_content_list_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"usage": {}}))
    with pytest.raises(PermanentProviderError, match="'content' list"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_content_without_a_text_block_raises_permanent() -> None:
    payload = {"content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    with pytest.raises(PermanentProviderError, match="no text block"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_response_that_is_not_an_object_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json=["unexpected"]))
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


# ---------------------------------------------------------------------------
# HTTP error normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
async def test_client_errors_are_permanent(status: int) -> None:
    provider = _make_provider(
        lambda r: httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "nope"}})
    )
    with pytest.raises(PermanentProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_rate_limit_and_server_errors_are_transient(status: int) -> None:
    provider = _make_provider(
        lambda r: httpx.Response(status, json={"type": "error", "error": {"type": "x", "message": "later"}})
    )
    with pytest.raises(TransientProviderError):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_529_overloaded_is_classified_transient() -> None:
    """Anthropic-specific `overloaded_error` outside the standard 5xx block.
    Mutation-sensitive: removing the explicit 529 branch drops it into the
    permanent fall-through and fails this test."""
    provider = _make_provider(
        lambda r: httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    )
    with pytest.raises(TransientProviderError, match="temporarily overloaded") as excinfo:
        await provider.generate_text(_request())
    # And the live retry policy must agree it is retryable.
    assert RetryPolicy().is_transient(excinfo.value) is True


@pytest.mark.asyncio
async def test_error_message_is_extracted_from_anthropics_envelope() -> None:
    provider = _make_provider(
        lambda r: httpx.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "max_tokens is required"}}
        )
    )
    with pytest.raises(PermanentProviderError, match="max_tokens is required"):
        await provider.generate_text(_request())


@pytest.mark.asyncio
async def test_unparseable_error_body_still_classifies_by_status() -> None:
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
    assert "x-api-key" not in captured


@pytest.mark.asyncio
async def test_health_check_false_on_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert await _make_provider(handler).health_check() is False


@pytest.mark.asyncio
async def test_test_connection_requires_a_credential() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"data": []}))
    with pytest.raises(PermanentProviderError, match="requires a credential"):
        await provider.test_connection(None)


@pytest.mark.asyncio
async def test_test_connection_succeeds_and_never_generates() -> None:
    """The probe must hit the catalog endpoint, never `/v1/messages`."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": [], "has_more": False})

    assert await _make_provider(handler).test_connection(_KEY_TENANT_A) is True
    assert paths == ["/v1/models"]
    assert "/v1/messages" not in paths


@pytest.mark.asyncio
async def test_test_connection_authentication_failure_is_permanent() -> None:
    provider = _make_provider(
        lambda r: httpx.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid"}}
        )
    )
    with pytest.raises(PermanentProviderError):
        await provider.test_connection("wrong-key")  # nosec - test fixture


@pytest.mark.asyncio
async def test_test_connection_rate_limit_is_transient() -> None:
    provider = _make_provider(lambda r: httpx.Response(429, json={"error": {"message": "slow"}}))
    with pytest.raises(TransientProviderError):
        await provider.test_connection("any-key")  # nosec - test fixture


# ---------------------------------------------------------------------------
# Model discovery: complete cursor pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_maps_a_single_page() -> None:
    payload = {
        "data": [{"id": "claude-opus-5", "display_name": "Claude Opus 5"}, {"id": "claude-sonnet-5"}],
        "has_more": False,
    }
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    models = await provider.discover_models(_KEY_TENANT_A)

    assert [m.model_id for m in models] == ["claude-opus-5", "claude-sonnet-5"]
    assert all(m.provider_id == "anthropic" for m in models)


@pytest.mark.asyncio
async def test_discovery_follows_the_cursor_to_exhaustion() -> None:
    """Complete pagination (B3 decision 3): `has_more` + `last_id` are
    followed as `after_id` until the provider says there is no more."""
    seen_after: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("after_id")
        seen_after.append(after)
        if after is None:
            return httpx.Response(200, json={"data": [{"id": "m1"}], "has_more": True, "last_id": "m1"})
        if after == "m1":
            return httpx.Response(200, json={"data": [{"id": "m2"}], "has_more": True, "last_id": "m2"})
        return httpx.Response(200, json={"data": [{"id": "m3"}], "has_more": False, "last_id": "m3"})

    models = await _make_provider(handler).discover_models(_KEY_TENANT_A)

    assert [m.model_id for m in models] == ["m1", "m2", "m3"]
    assert seen_after == [None, "m1", "m2"]


@pytest.mark.asyncio
async def test_discovery_stops_when_has_more_claims_another_page_but_gives_no_cursor() -> None:
    """Defensive: returning what we have beats looping forever on the same
    page when the server's own cursor is missing or non-advancing."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"data": [{"id": "only"}], "has_more": True})

    models = await _make_provider(handler).discover_models(_KEY_TENANT_A)
    assert [m.model_id for m in models] == ["only"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_discovery_without_credential_falls_back_to_static_list() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"data": []}))
    models = await provider.discover_models(None)
    assert {m.model_id for m in models} == set(SUPPORTED_ANTHROPIC_MODELS)


@pytest.mark.asyncio
async def test_discovery_malformed_payload_raises_permanent() -> None:
    provider = _make_provider(lambda r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(PermanentProviderError, match="'data' list"):
        await provider.discover_models(_KEY_TENANT_A)


@pytest.mark.asyncio
async def test_discovery_skips_malformed_entries_without_losing_good_ones() -> None:
    payload = {"data": ["not-a-dict", {"no_id": True}, {"id": "good"}], "has_more": False}
    provider = _make_provider(lambda r: httpx.Response(200, json=payload))
    models = await provider.discover_models(_KEY_TENANT_A)
    assert [m.model_id for m in models] == ["good"]


@pytest.mark.asyncio
async def test_discovery_bounded_against_a_non_terminating_cursor() -> None:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return httpx.Response(
            200, json={"data": [{"id": f"m{counter['n']}"}], "has_more": True, "last_id": f"m{counter['n']}"}
        )

    with pytest.raises(TransientProviderError, match="without exhausting its cursor"):
        await _make_provider(handler).discover_models(_KEY_TENANT_A)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_closes_only_an_owned_client() -> None:
    resolver, _g = _real_resolver()
    owned = AnthropicProvider(credential_resolver=resolver)
    assert owned._owns_client is True
    await owned.aclose()
    assert owned._client.is_closed

    injected_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    borrowed = AnthropicProvider(credential_resolver=resolver, client=injected_client)
    await borrowed.aclose()
    assert injected_client.is_closed is False
    await injected_client.aclose()
