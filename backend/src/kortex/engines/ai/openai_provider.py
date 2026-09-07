"""OpenAI cloud provider for the KORTEX OS AI Orchestration Engine (Phase B / B2).

Establishes the canonical pattern every credentialed cloud provider
(Gemini/Anthropic, Phase B / B3) reuses: exactly ONE process-global instance
is registered in `ProviderRegistry`, holding a `TenantCredentialResolver`
reference rather than any tenant's key. Every call resolves the CALLING
tenant's credential fresh, from `request.tenant_id`
(`generate_text`/`generate_embeddings`) or an explicit `credential` argument
already resolved by the caller (`test_connection`/`discover_models`), uses
it once, and lets it go out of scope. This is a direct consequence of the
architectural rule `credentials.py` states and B1 already enforces:
`ProviderRegistry` must never become a tenant credential store, and there
must be no one-provider-instance-per-tenant holding a secret. See that
module's docstring for the full rationale.

Uses a plain `httpx.AsyncClient` against OpenAI's REST API directly --
deliberately NOT the `openai` PyPI SDK. Reasons, all pre-existing repo
conventions rather than a new decision made in isolation:

* `OllamaProvider` (the primary behavioral precedent for this provider) is
  built the same way, for exactly this class of reason: `httpx>=0.28.0` is
  already a core dependency, and no AI provider adapter in this package
  depends on a vendor SDK.
* The `openai` SDK bundles its own automatic retry logic by default. This
  package's resilience model (`ResilientAIProvider`: timeout, exponential
  backoff, circuit breaker) is deliberately the ONLY retry/timeout layer an
  AI provider sits behind -- a second, independent retry loop inside the
  SDK would race the outer one, double the effective retry count, and hide
  the real attempt/backoff timing from `ResilientAIProvider`'s own
  telemetry and circuit-breaker bookkeeping.
* OpenAI's REST surface actually used here (chat completions, embeddings,
  models) is small, stable, and does not warrant the dependency-management
  and version-pinning surface a full client SDK adds for the very partial
  slice of it this provider needs.

Adding zero new dependencies is therefore the correct choice, not merely
the cheapest one.

Embeddings are explicitly NOT implemented against the real OpenAI API in
Phase B / B2 (`generate_embeddings` always raises `PermanentProviderError`)
-- not a corner cut, but the same "explicit failure over fake success"
precedent `OllamaProvider`'s own module docstring established. Reason:
`BaseAIProvider.generate_embeddings(self, texts: list[str])` carries NO
tenant identifier at all, so there is no channel through which this method
could resolve a TENANT-SCOPED credential the way `generate_text` does via
`request.tenant_id`. Nothing today calls `generate_embeddings` in
production (verified: only `ResilientAIProvider`/`ProviderFallbackChain`
delegate it, and nothing calls those for embeddings either), so extending
the abstract contract with a tenant channel to support this is a
deliberate, separately-scoped architectural decision for a future
milestone, not something to slip in as a side effect of adding a chat
provider.
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

logger = logging.getLogger("kortex.engines.ai.openai_provider")

DEFAULT_OPENAI_BASE_URL: str = "https://api.openai.com/v1"

DEFAULT_OPENAI_TIMEOUT_SECONDS: float = 55.0
"""Slightly under the AI Engine's own 60s default outer generation timeout
(`AIEngineRuntimeConfig.default_generation_timeout_seconds`) -- the same
precedent and reasoning as `ollama_provider.DEFAULT_OLLAMA_TIMEOUT_SECONDS`:
this provider's own HTTP call should fail on its own terms rather than be
cancelled mid-flight by the outer `asyncio.timeout` wrapper
`ResilientAIProvider` already applies."""

DEFAULT_OPENAI_MODEL: str = "gpt-4o-mini"
"""Used only when neither `request.model_id` nor the tenant's configured
`AIProviderConfig.default_model` names one -- the last of three
precedence levels; see `OpenAIProvider.generate_text`."""

SUPPORTED_OPENAI_MODELS: tuple[str, ...] = ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini")
"""The static, small, explicitly-validated allow-list `ModelRouter`'s D1
`model_id` filter gates on (`AIProviderMetadata.supported_models`) -- NOT a
mirror of OpenAI's full model catalog, which numbers in the hundreds
across chat, embedding, moderation, audio, and image families this
provider does not implement. This is the set of standard chat-completions
models KORTEX validates its request/response mapping against (all share
the same `max_tokens` request parameter; OpenAI's newer reasoning-model
family uses a different one this provider does not attempt to support).
`discover_models()` -- a live, tenant-scoped call to OpenAI's own
`/v1/models` -- is the real, current source of truth for what a given
tenant's key can actually reach; this list only decides which *pinned*
`model_id` values `ModelRouter` will route to this provider without
requiring a live call. A tenant's own configured `default_model`
(`AIProviderConfig.default_model`, reached via `ResolvedCredential`) is
NOT constrained by this list -- see `generate_text`'s docstring."""


def _raise_for_status(response: httpx.Response, provider_id: str, operation: str) -> None:
    """Map an OpenAI HTTP response's status code to the KORTEX error taxonomy.

    Shared by every OpenAI HTTP call this provider makes so the
    status-code-to-exception mapping exists in exactly one place. Never
    includes request headers (the credential lives in `Authorization`) in
    any raised message -- only the response body, truncated, which is
    OpenAI's own error JSON and does not echo the caller's credential back.
    """
    if response.status_code == 200:
        return
    body_excerpt = response.text[:500]
    if response.status_code == 401:
        raise PermanentProviderError(
            f"OpenAI provider '{provider_id}' rejected the configured credential as invalid ({operation})."
        )
    if response.status_code == 403:
        raise PermanentProviderError(
            f"OpenAI provider '{provider_id}' credential is valid but forbidden from this operation ({operation})."
        )
    if response.status_code == 404:
        raise PermanentProviderError(
            f"OpenAI provider '{provider_id}' could not find the requested resource for '{operation}' "
            "(model likely unavailable to this credential)."
        )
    if response.status_code == 429:
        raise TransientProviderError(f"OpenAI provider '{provider_id}' was rate-limited during '{operation}'.")
    if 500 <= response.status_code < 600:
        raise TransientProviderError(
            f"OpenAI provider '{provider_id}' returned {response.status_code} during '{operation}': {body_excerpt}"
        )
    raise PermanentProviderError(
        f"OpenAI provider '{provider_id}' rejected the request during '{operation}' "
        f"({response.status_code}): {body_excerpt}"
    )


class OpenAIProvider(BaseAIProvider):
    """Real cloud provider backed by the OpenAI REST API.

    Exactly one instance is ever registered, system-wide -- see the module
    docstring. `credential_resolver` is the ONLY channel through which this
    instance ever sees a tenant's key; it is resolved fresh on every call
    and never stored beyond that call's local scope.
    """

    def __init__(
        self,
        credential_resolver: TenantCredentialResolver,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        default_model: str = DEFAULT_OPENAI_MODEL,
        timeout_seconds: float = DEFAULT_OPENAI_TIMEOUT_SECONDS,
        supported_models: list[str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty.")
        if not default_model or not default_model.strip():
            raise ValueError("default_model must not be empty.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")

        self._credential_resolver = credential_resolver
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout_seconds = timeout_seconds
        self._metadata = AIProviderMetadata(
            provider_id="openai",
            display_name="OpenAI",
            vendor="openai",
            endpoint_type="cloud",
            url=self._base_url,
            credential_requirement="api_key",
            secret_handle=provider_secret_handle("openai"),
            supported_models=list(supported_models) if supported_models is not None else list(SUPPORTED_OPENAI_MODELS),
        )
        # Own the client only if the caller didn't inject one -- tests
        # inject a mock transport; production constructs its own and is
        # responsible for closing it via `aclose()` (see module docstring
        # on the pre-existing, unaddressed-in-B2 shutdown-wiring gap this
        # provider shares with `OllamaProvider`).
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
        return {"Authorization": f"Bearer {credential}", "Content-Type": "application/json"}

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate a chat completion via `/chat/completions`.

        Model precedence (highest first): `request.model_id` (D1, an
        explicit caller pin -- `ModelRouter` has already verified it is in
        `supported_models` before this is ever reached through routed
        execution) > the calling tenant's configured
        `AIProviderConfig.default_model` (via `ResolvedCredential`, which
        may name ANY model string the tenant chose, not just one from
        `SUPPORTED_OPENAI_MODELS` -- that static list only gates *pinned*
        `model_id` routing, not a tenant's own configured default) >
        `DEFAULT_OPENAI_MODEL`.
        """
        start = time.perf_counter()
        resolved = await self._credential_resolver.resolve(request.tenant_id, self.provider_id)
        if resolved is None:
            raise PermanentProviderError(
                f"OpenAI provider '{self.provider_id}' has no enabled, credentialed configuration for this tenant."
            )
        model = request.model_id or resolved.default_model or self._default_model

        messages: list[dict[str, str]] = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        messages.append({"role": "user", "content": request.prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        try:
            http_response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(resolved.plaintext),
            )
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"OpenAI provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"OpenAI provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"OpenAI provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

        _raise_for_status(http_response, self.provider_id, "chat completion")

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"OpenAI provider '{self.provider_id}' returned a malformed (non-JSON) response."
            ) from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise PermanentProviderError(
                f"OpenAI provider '{self.provider_id}' response is missing the expected 'choices' list."
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text_content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text_content, str):
            raise PermanentProviderError(
                f"OpenAI provider '{self.provider_id}' response is missing the expected message text content."
            )

        usage = data.get("usage") if isinstance(data, dict) else None
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0) if isinstance(usage, dict) else 0
        completion_tokens = int(usage.get("completion_tokens", 0) or 0) if isinstance(usage, dict) else 0

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
            model_name=data.get("model", model) if isinstance(data, dict) else model,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Not supported in Phase B / B2 -- fails explicitly. See module docstring."""
        raise PermanentProviderError(
            f"OpenAI provider '{self.provider_id}' does not support embeddings generation "
            "in the current KORTEX configuration (no tenant-scoped credential channel reaches this method)."
        )

    async def health_check(self) -> bool:
        """Reachability only, per `BaseAIProvider.health_check`'s own contract.

        No credential is available here (the method takes none), so this
        cannot and does not assert anything about authentication state --
        `test_connection` is the credential-aware counterpart. A bare,
        unauthenticated request to the API root reliably distinguishes
        "network/DNS/TLS path to OpenAI is reachable" (any HTTP response,
        even a 401) from "it is not" (a transport-level exception).
        """
        try:
            await self._client.get(self._base_url)
        except httpx.HTTPError:
            return False
        return True

    async def test_connection(self, credential: str | None = None) -> bool:
        """Validate `credential` with the cheapest real authenticated OpenAI call.

        Reuses `/models` (the same endpoint `discover_models` calls) rather
        than issuing a chat completion: it is authenticated, requires no
        input tokens, and costs nothing to call, satisfying "perform a
        minimal safe provider operation... not an expensive arbitrary
        completion."
        """
        if credential is None:
            raise PermanentProviderError(f"OpenAI provider '{self.provider_id}' requires a credential to test.")
        await self._fetch_models(credential)
        return True

    async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
        """Live model discovery via `/models`, scoped to whichever credential is supplied.

        Falls back to the static default (`BaseAIProvider.discover_models`,
        this provider's own `supported_models` allow-list) when no
        credential is available -- OpenAI's `/models` endpoint requires
        authentication, so there is no unauthenticated live source to fall
        back to.
        """
        if credential is None:
            return await super().discover_models(credential)
        return await self._fetch_models(credential)

    async def _fetch_models(self, credential: str) -> list[AIModelSummary]:
        """The one real HTTP call backing both `test_connection` and `discover_models`."""
        try:
            http_response = await self._client.get(
                f"{self._base_url}/models",
                headers=self._headers(credential),
            )
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"OpenAI provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"OpenAI provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"OpenAI provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

        _raise_for_status(http_response, self.provider_id, "model discovery")

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"OpenAI provider '{self.provider_id}' returned a malformed (non-JSON) models response."
            ) from exc

        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise PermanentProviderError(
                f"OpenAI provider '{self.provider_id}' models response is missing the expected 'data' list."
            )

        return [
            AIModelSummary(
                model_id=entry["id"],
                provider_id=self.provider_id,
                provider_display_name=self.metadata.display_name,
            )
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        ]


__all__ = [
    "DEFAULT_OPENAI_BASE_URL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_OPENAI_TIMEOUT_SECONDS",
    "SUPPORTED_OPENAI_MODELS",
    "OpenAIProvider",
]
