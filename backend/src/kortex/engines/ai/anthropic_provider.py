"""Anthropic Claude cloud provider for the KORTEX OS AI Orchestration Engine (Phase B / B3).

Follows the canonical cloud-provider pattern `openai_provider.py` established
in B2, unchanged in every architectural respect: exactly ONE process-global
instance is registered in `ProviderRegistry`, holding a
`TenantCredentialResolver` reference rather than any tenant's key, and every
call resolves the CALLING tenant's credential fresh from `request.tenant_id`
(`generate_text`) or an explicit already-resolved `credential` argument
(`test_connection`/`discover_models`), uses it once, and lets it go out of
scope. See `credentials.py` for the full rationale behind that rule.

Raw `httpx`, no vendor SDK -- the same three reasons `openai_provider.py`
documents apply identically here, and no concrete blocker was found that
would justify an `anthropic` package dependency.

## Two deliberate provider capability limitations

**1. `LLMRequest.temperature` is NOT sent to Anthropic.** This adapter does
not map KORTEX's temperature control onto the native API at all. This is an
intentional, documented capability limitation, NOT silent equivalence: a
caller that sets `temperature=0.2` and routes to Anthropic gets Anthropic's
own default sampling behavior, not a 0.2-equivalent. The reason is that
`temperature`/`top_p`/`top_k` were REMOVED from Anthropic's newest model tier
(Claude Opus 5, Fable 5/5.1, Opus 4.8/4.7) and are rejected with a 400 -- so
forwarding the field would hard-fail every request pinned to those models,
while a per-model compatibility matrix would need maintenance on every
Anthropic release and a retry-after-400 would add a round trip to every call
on the affected tier. Omitting it is the only option that works uniformly
across every Claude model without ongoing maintenance. Chief Architect
decision, Phase B / B3.

**2. `max_tokens` is REQUIRED by the Messages API.** Unlike OpenAI and Gemini,
where it is optional and defaults server-side, Anthropic rejects a request
that omits it. `LLMRequest.max_tokens` is `int | None`, so when it is `None`
this adapter supplies `DEFAULT_ANTHROPIC_MAX_TOKENS`. An explicitly supplied
value is always forwarded unchanged.

## Wire-protocol differences from OpenAI, all contained in this adapter

* Auth is `x-api-key`, plus a mandatory `anthropic-version` header. The
  version is a fixed API-contract date string, decoupled from model release
  dates -- a provider-wide constant, never tenant configuration.
* `system` is a TOP-LEVEL request field, not a message with `role: "system"`
  inside `messages` (which is how `openai_provider.py` sends it).
* Responses carry `content` as a LIST OF BLOCKS; the text lives in the blocks
  whose `type == "text"`, so extraction filters rather than indexing one
  field.
* Usage fields are `input_tokens`/`output_tokens`, not OpenAI's
  `prompt_tokens`/`completion_tokens`.
* HTTP 529 `overloaded_error` is an Anthropic-specific status outside the
  standard 5xx block and is classified TRANSIENT explicitly, so it can never
  fall through to a generic "unexpected status" branch and be misread as
  permanent.

Embeddings are NOT implemented (`generate_embeddings` raises
`PermanentProviderError`) -- same contract-level reason `openai_provider.py`
documents: `BaseAIProvider.generate_embeddings(self, texts)` carries no
tenant identifier, so no tenant-scoped credential can be resolved inside it.
Anthropic additionally offers no first-party embeddings endpoint at all.

Only the native Anthropic Messages API (`api.anthropic.com`) is implemented.
Amazon Bedrock is a different product with different auth and is explicitly
out of scope for B3.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.credentials import TenantCredentialResolver, provider_secret_handle
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import AIModelSummary, AIProviderMetadata, LLMRequest, LLMResponse

logger = logging.getLogger("kortex.engines.ai.anthropic_provider")

ANTHROPIC_PROVIDER_ID: str = "anthropic"

DEFAULT_ANTHROPIC_BASE_URL: str = "https://api.anthropic.com/v1"

ANTHROPIC_API_VERSION: str = "2023-06-01"
"""Value of the mandatory `anthropic-version` header.

A fixed API-contract version string, NOT a model version and NOT a release
date -- it pins the request/response schema this adapter was written against.
Provider-wide by nature, so it lives here as a constant rather than in
`AIProviderConfig`."""

DEFAULT_ANTHROPIC_TIMEOUT_SECONDS: float = 55.0
"""Slightly under the AI Engine's own 60s default outer generation timeout
(`AIEngineRuntimeConfig.default_generation_timeout_seconds`) -- same precedent
and reasoning as `ollama_provider`/`openai_provider`/`gemini_provider`."""

DEFAULT_ANTHROPIC_MODEL: str = "claude-opus-5"
"""Used only when neither `request.model_id` nor the tenant's configured
`AIProviderConfig.default_model` names one -- the last of three precedence
levels (see `generate_text`)."""

DEFAULT_ANTHROPIC_MAX_TOKENS: int = 4096
"""Supplied when `LLMRequest.max_tokens is None`, because Anthropic's Messages
API REQUIRES `max_tokens` and rejects a request without it.

Chief Architect decision, Phase B / B3: fixed at 4096 and deliberately NOT
tenant-configurable in this milestone. An explicitly supplied
`LLMRequest.max_tokens` always wins over this value."""

SUPPORTED_ANTHROPIC_MODELS: tuple[str, ...] = (
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-opus-4-8",
    "claude-haiku-4-5",
)
"""The static, small, explicitly-validated allow-list `ModelRouter`'s D1
`model_id` filter gates on (`AIProviderMetadata.supported_models`).

Routing metadata ONLY -- deliberately NOT a mirror of Anthropic's full
catalog, which spans several generations plus restricted-access models this
adapter is not validated against. `discover_models()` is the live,
tenant-scoped source of truth for what a given key can actually reach; this
tuple decides only which *pinned* `model_id` values `ModelRouter` will route
here without needing a network call."""

_MAX_DISCOVERY_PAGES: int = 50
"""Hard stop on cursor following. Complete pagination is required (B3 decision
3), but an unbounded `while` loop driven by a remote server's own cursor is a
hang risk if that server ever returns a non-advancing cursor."""


def _raise_for_status(response: httpx.Response, provider_id: str, operation: str) -> None:
    """Map an Anthropic HTTP response's status code to the KORTEX error taxonomy.

    Classification is driven by the HTTP status code, which is authoritative
    and always present. The body is consulted only to enrich the message:
    Anthropic's envelope is `{"type": "error", "error": {"type": ...,
    "message": ...}}`, but nothing here *depends* on that shape -- a missing,
    non-JSON, or differently-shaped body degrades to a truncated raw excerpt.

    Never includes request headers (the credential lives in `x-api-key`) in
    any raised message.
    """
    if response.status_code == 200:
        return

    detail = _extract_error_message(response)

    if response.status_code == 401:
        raise PermanentProviderError(
            f"Anthropic provider '{provider_id}' rejected the configured credential as invalid "
            f"during '{operation}': {detail}"
        )
    if response.status_code == 403:
        raise PermanentProviderError(
            f"Anthropic provider '{provider_id}' credential is valid but forbidden from '{operation}': {detail}"
        )
    if response.status_code == 404:
        raise PermanentProviderError(
            f"Anthropic provider '{provider_id}' could not find the requested resource for "
            f"'{operation}' (model likely unavailable to this credential): {detail}"
        )
    if response.status_code == 413:
        raise PermanentProviderError(
            f"Anthropic provider '{provider_id}' rejected the request as too large during '{operation}': {detail}"
        )
    if response.status_code == 429:
        raise TransientProviderError(
            f"Anthropic provider '{provider_id}' was rate-limited during '{operation}': {detail}"
        )
    if response.status_code == 529:
        # Anthropic-specific `overloaded_error`, outside the standard 5xx
        # block. Checked explicitly and BEFORE the generic fall-through so it
        # can never be misclassified as a permanent 4xx/5xx-adjacent failure.
        raise TransientProviderError(
            f"Anthropic provider '{provider_id}' is temporarily overloaded during '{operation}': {detail}"
        )
    if 500 <= response.status_code < 600:
        raise TransientProviderError(
            f"Anthropic provider '{provider_id}' returned {response.status_code} during '{operation}': {detail}"
        )
    raise PermanentProviderError(
        f"Anthropic provider '{provider_id}' rejected the request during '{operation}' "
        f"({response.status_code}): {detail}"
    )


def _extract_error_message(response: httpx.Response) -> str:
    """Best-effort human-readable detail from an Anthropic error body.

    Defensive by construction: any shape other than the documented
    `{"error": {"message": ...}}` falls back to a truncated raw excerpt, so a
    changed or absent envelope can never turn a clean status-code
    classification into an unhandled parse error.
    """
    try:
        body = response.json()
    except ValueError:
        return response.text[:500]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message[:500]
    return response.text[:500]


class AnthropicProvider(BaseAIProvider):
    """Real cloud provider backed by the native Anthropic Messages REST API.

    Exactly one instance is ever registered, system-wide.
    `credential_resolver` is the ONLY channel through which this instance ever
    sees a tenant's key; it is resolved fresh on every call and never stored
    beyond that call's local scope.
    """

    def __init__(
        self,
        credential_resolver: TenantCredentialResolver,
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        default_model: str = DEFAULT_ANTHROPIC_MODEL,
        default_max_tokens: int = DEFAULT_ANTHROPIC_MAX_TOKENS,
        timeout_seconds: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
        supported_models: list[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty.")
        if not default_model or not default_model.strip():
            raise ValueError("default_model must not be empty.")
        if default_max_tokens <= 0:
            raise ValueError("default_max_tokens must be > 0.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")

        self._credential_resolver = credential_resolver
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._default_max_tokens = default_max_tokens
        self._timeout_seconds = timeout_seconds
        self._metadata = AIProviderMetadata(
            provider_id=ANTHROPIC_PROVIDER_ID,
            display_name="Anthropic Claude",
            vendor="anthropic",
            endpoint_type="cloud",
            url=self._base_url,
            credential_requirement="api_key",
            secret_handle=provider_secret_handle(ANTHROPIC_PROVIDER_ID),
            supported_models=(
                list(supported_models) if supported_models is not None else list(SUPPORTED_ANTHROPIC_MODELS)
            ),
        )
        # Own the client only if the caller didn't inject one -- tests inject a
        # mock transport; production constructs its own and is responsible for
        # closing it via `aclose()`.
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def aclose(self) -> None:
        """Release the underlying HTTP client, if this instance owns one."""
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, credential: str) -> dict[str, str]:
        """`x-api-key` plus the mandatory `anthropic-version` -- see module docstring."""
        return {
            "x-api-key": credential,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "Content-Type": "application/json",
        }

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate a message via `{base}/messages`.

        Model precedence (highest first): `request.model_id` (D1, an explicit
        caller pin -- `ModelRouter` has already verified it against
        `supported_models` before this is reached through routed execution) >
        the calling tenant's configured `AIProviderConfig.default_model` (via
        `ResolvedCredential`) > `DEFAULT_ANTHROPIC_MODEL`.

        `request.temperature` is deliberately not forwarded, and
        `max_tokens` falls back to `DEFAULT_ANTHROPIC_MAX_TOKENS` when the
        request leaves it unset -- both are documented in the module
        docstring's capability-limitation section.
        """
        start = time.perf_counter()
        resolved = await self._credential_resolver.resolve(request.tenant_id, self.provider_id)
        if resolved is None:
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' has no enabled, credentialed configuration for this tenant."
            )
        model = request.model_id or resolved.default_model or self._default_model

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self._default_max_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.system_instruction:
            # Top-level field, NOT a message with role "system".
            payload["system"] = request.system_instruction

        http_response = await self._post(
            f"{self._base_url}/messages",
            payload=payload,
            credential=resolved.plaintext,
        )
        _raise_for_status(http_response, self.provider_id, "create message")

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' returned a malformed (non-JSON) response."
            ) from exc
        if not isinstance(data, dict):
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' returned a response that is not a JSON object."
            )

        text_content = self._extract_text(data)
        prompt_tokens, completion_tokens = self._extract_usage(data)

        return LLMResponse(
            request_id=request.request_id,
            text_content=text_content,
            tool_calls=[],
            token_usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            execution_time_ms=(time.perf_counter() - start) * 1000.0,
            provider_id=self.provider_id,
            model_name=data.get("model") or model,
        )

    def _extract_text(self, data: dict[str, Any]) -> str:
        """Concatenate the `text` blocks out of an Anthropic response.

        `content` is a list of typed blocks (`text`, `thinking`, `tool_use`,
        ...); only `text` blocks carry the answer. A `stop_reason` of
        `"refusal"` is surfaced as an explicit error rather than as empty
        text, because an empty string is indistinguishable from a legitimate
        short answer to the caller.
        """
        stop_reason = data.get("stop_reason")
        if stop_reason == "refusal":
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' declined to answer (stop_reason=refusal)."
            )

        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' response is missing the expected 'content' list."
            )
        texts = [
            block["text"]
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        if not texts:
            raise PermanentProviderError(f"Anthropic provider '{self.provider_id}' response contains no text block.")
        # One answer may arrive as several text blocks; joining is the faithful
        # reconstruction, not a heuristic.
        return "".join(texts)

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[int, int]:
        """Map Anthropic's `input_tokens`/`output_tokens` onto KORTEX's counts.

        Absent or partial usage metadata degrades to zeros rather than failing
        the whole generation -- the answer is still valid, and
        `LLMResponse.token_usage` is reporting, not correctness.
        """
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return 0, 0
        prompt_tokens = int(usage.get("input_tokens", 0) or 0)
        completion_tokens = int(usage.get("output_tokens", 0) or 0)
        return prompt_tokens, completion_tokens

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Not supported in Phase B / B3 -- fails explicitly. See module docstring."""
        raise PermanentProviderError(
            f"Anthropic provider '{self.provider_id}' does not support embeddings generation "
            "in the current KORTEX configuration (no tenant-scoped credential channel reaches "
            "this method, and Anthropic exposes no first-party embeddings endpoint)."
        )

    async def health_check(self) -> bool:
        """Reachability only, per `BaseAIProvider.health_check`'s own contract.

        No credential is available here (the method takes none), so this
        cannot and does not assert anything about authentication state --
        `test_connection` is the credential-aware counterpart. Any HTTP
        response, including a 401, proves the network path is up; only a
        transport-level failure means it is not.
        """
        try:
            await self._client.get(f"{self._base_url}/models")
        except httpx.HTTPError:
            return False
        return True

    async def test_connection(self, credential: str | None = None) -> bool:
        """Validate `credential` with the cheapest real authenticated call.

        Uses the first page of the models catalog rather than a message
        request: it is authenticated, consumes no input or output tokens, and
        costs nothing -- satisfying "must not perform an LLM completion".
        """
        if credential is None:
            raise PermanentProviderError(f"Anthropic provider '{self.provider_id}' requires a credential to test.")
        await self._fetch_models_page(credential, after_id=None)
        return True

    async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
        """Live, complete model discovery via `{base}/models`.

        Follows Anthropic's cursor (`has_more` + `last_id` -> `after_id`) to
        exhaustion (B3 decision 3), so the result is the full set of models
        this credential can reach rather than one page of it.

        Falls back to the static `supported_models` default
        (`BaseAIProvider.discover_models`) when no credential is available:
        the catalog endpoint requires authentication, so there is no
        unauthenticated live source to consult.
        """
        if credential is None:
            return await super().discover_models(credential)

        summaries: list[AIModelSummary] = []
        after_id: str | None = None
        for _ in range(_MAX_DISCOVERY_PAGES):
            payload = await self._fetch_models_page(credential, after_id=after_id)
            entries = payload.get("data")
            if not isinstance(entries, list):
                raise PermanentProviderError(
                    f"Anthropic provider '{self.provider_id}' models response is missing the expected 'data' list."
                )
            summaries.extend(self._summaries_from_entries(entries))

            if payload.get("has_more") is not True:
                return summaries
            last_id = payload.get("last_id")
            if not isinstance(last_id, str) or not last_id or last_id == after_id:
                # `has_more` claimed another page but gave no usable, advancing
                # cursor. Returning what we have beats looping forever on the
                # same page.
                return summaries
            after_id = last_id

        raise TransientProviderError(
            f"Anthropic provider '{self.provider_id}' model discovery exceeded "
            f"{_MAX_DISCOVERY_PAGES} pages without exhausting its cursor."
        )

    def _summaries_from_entries(self, entries: list[Any]) -> list[AIModelSummary]:
        """Normalize catalog entries into `AIModelSummary`.

        Malformed individual entries are skipped rather than failing the whole
        listing: discovery is a best-effort informational read, and one bad
        row should not hide every good one. Anthropic's catalog lists only
        generation-capable models, so no method filter is needed here (unlike
        Gemini's, which also carries embedding-only families).
        """
        summaries: list[AIModelSummary] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id:
                continue
            summaries.append(
                AIModelSummary(
                    model_id=model_id,
                    provider_id=self.provider_id,
                    provider_display_name=self.metadata.display_name,
                )
            )
        return summaries

    async def _fetch_models_page(self, credential: str, after_id: str | None) -> dict[str, Any]:
        """One authenticated page of the models catalog, status-normalized."""
        params: dict[str, str] = {}
        if after_id:
            params["after_id"] = after_id

        http_response = await self._get(
            f"{self._base_url}/models",
            params=params,
            credential=credential,
        )
        _raise_for_status(http_response, self.provider_id, "model discovery")

        try:
            payload = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' returned a malformed (non-JSON) models response."
            ) from exc
        if not isinstance(payload, dict):
            raise PermanentProviderError(
                f"Anthropic provider '{self.provider_id}' models response is not a JSON object."
            )
        return payload

    async def _post(self, url: str, payload: dict[str, Any], credential: str) -> httpx.Response:
        """POST with Anthropic's transport errors mapped to the KORTEX taxonomy."""
        try:
            return await self._client.post(url, json=payload, headers=self._headers(credential))
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"Anthropic provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"Anthropic provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"Anthropic provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

    async def _get(self, url: str, params: dict[str, str], credential: str) -> httpx.Response:
        """GET with Anthropic's transport errors mapped to the KORTEX taxonomy."""
        try:
            return await self._client.get(url, params=params, headers=self._headers(credential))
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"Anthropic provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"Anthropic provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"Anthropic provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc


__all__ = [
    "ANTHROPIC_API_VERSION",
    "ANTHROPIC_PROVIDER_ID",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_ANTHROPIC_MAX_TOKENS",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_ANTHROPIC_TIMEOUT_SECONDS",
    "SUPPORTED_ANTHROPIC_MODELS",
    "AnthropicProvider",
]
