"""OpenRouter cloud provider for the KORTEX OS AI Orchestration Engine.

Mirrors the OpenAIProvider pattern. Uses httpx to interact with OpenRouter's
OpenAI-compatible REST API.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import httpx

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.credentials import TenantCredentialResolver, provider_secret_handle
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import AIModelSummary, AIProviderMetadata, LLMRequest, LLMResponse

logger = logging.getLogger("kortex.engines.ai.openrouter_provider")

OPENROUTER_PROVIDER_ID: str = "openrouter"

DEFAULT_OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

DEFAULT_OPENROUTER_TIMEOUT_SECONDS: float = 55.0

DEFAULT_OPENROUTER_MODEL: str = "google/gemini-3.1-pro-preview"

SUPPORTED_OPENROUTER_MODELS: tuple[str, ...] = ("google/gemini-3.1-pro-preview",)


def _raise_for_status(response: httpx.Response, provider_id: str, operation: str) -> None:
    if response.status_code == 200:
        return
    body_excerpt = response.text[:500]
    if response.status_code == 401:
        raise PermanentProviderError(
            f"OpenRouter provider '{provider_id}' rejected the configured credential as invalid ({operation})."
        )
    if response.status_code == 403:
        raise PermanentProviderError(
            f"OpenRouter provider '{provider_id}' credential is valid but forbidden from this operation ({operation})."
        )
    if response.status_code == 404:
        raise PermanentProviderError(
            f"OpenRouter provider '{provider_id}' could not find the requested resource for '{operation}' "
            "(model likely unavailable to this credential)."
        )
    if response.status_code == 429:
        raise TransientProviderError(f"OpenRouter provider '{provider_id}' was rate-limited during '{operation}'.")
    if 500 <= response.status_code < 600:
        raise TransientProviderError(
            f"OpenRouter provider '{provider_id}' returned {response.status_code} during '{operation}': {body_excerpt}"
        )
    raise PermanentProviderError(
        f"OpenRouter provider '{provider_id}' rejected the request during '{operation}' "
        f"({response.status_code}): {body_excerpt}"
    )


class OpenRouterProvider(BaseAIProvider):
    def __init__(
        self,
        credential_resolver: TenantCredentialResolver,
        base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        default_model: str = DEFAULT_OPENROUTER_MODEL,
        timeout_seconds: float = DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
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
            provider_id=OPENROUTER_PROVIDER_ID,
            display_name="OpenRouter",
            vendor="openrouter",
            endpoint_type="cloud",
            url=self._base_url,
            credential_requirement="api_key",
            secret_handle=provider_secret_handle(OPENROUTER_PROVIDER_ID),
            supported_models=list(supported_models)
            if supported_models is not None
            else list(SUPPORTED_OPENROUTER_MODELS),
        )
        self._discovered_models: set[str] = set()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    def register_discovered_models(self, model_ids: Sequence[str]) -> None:
        """Dynamically expand the provider's supported models with discovered catalog models."""
        self._discovered_models.update(m for m in model_ids if m)

    @property
    def metadata(self) -> AIProviderMetadata:
        if not self._discovered_models:
            return self._metadata
        combined = list(dict.fromkeys(self._metadata.supported_models + sorted(self._discovered_models)))
        return self._metadata.model_copy(update={"supported_models": combined})

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self, credential: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/kortex-os",
            "X-Title": "KORTEX",
        }

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        resolved = await self._credential_resolver.resolve(request.tenant_id, self.provider_id)
        if resolved is None:
            raise PermanentProviderError(
                f"OpenRouter provider '{self.provider_id}' has no enabled, credentialed configuration for this tenant."
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
                f"OpenRouter provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"OpenRouter provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"OpenRouter provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

        _raise_for_status(http_response, self.provider_id, "chat completion")

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"OpenRouter provider '{self.provider_id}' returned a malformed (non-JSON) response."
            ) from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise PermanentProviderError(
                f"OpenRouter provider '{self.provider_id}' response is missing the expected 'choices' list."
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text_content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text_content, str):
            raise PermanentProviderError(
                f"OpenRouter provider '{self.provider_id}' response is missing the expected message text content."
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
        raise PermanentProviderError(
            f"OpenRouter provider '{self.provider_id}' does not support embeddings generation."
        )

    async def health_check(self) -> bool:
        try:
            await self._client.get(f"{self._base_url}/models")
        except httpx.HTTPError:
            return False
        return True

    async def test_connection(self, credential: str | None = None) -> bool:
        if credential is None:
            raise PermanentProviderError(f"OpenRouter provider '{self.provider_id}' requires a credential to test.")
        await self._fetch_models(credential)
        return True

    async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
        if credential is None:
            return await super().discover_models(credential)
        return await self._fetch_models(credential)

    async def _fetch_models(self, credential: str) -> list[AIModelSummary]:
        try:
            http_response = await self._client.get(
                f"{self._base_url}/models",
                headers=self._headers(credential),
            )
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"OpenRouter provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"OpenRouter provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"OpenRouter provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

        _raise_for_status(http_response, self.provider_id, "model discovery")

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"OpenRouter provider '{self.provider_id}' returned a malformed (non-JSON) models response."
            ) from exc

        entries = data.get("data") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise PermanentProviderError(
                f"OpenRouter provider '{self.provider_id}' models response is missing the expected 'data' list."
            )

        result = [
            AIModelSummary(
                model_id=entry["id"],
                provider_id=self.provider_id,
                provider_display_name=self.metadata.display_name,
            )
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        ]
        self.register_discovered_models([s.model_id for s in result])
        return result


__all__ = [
    "DEFAULT_OPENROUTER_BASE_URL",
    "DEFAULT_OPENROUTER_MODEL",
    "DEFAULT_OPENROUTER_TIMEOUT_SECONDS",
    "OPENROUTER_PROVIDER_ID",
    "SUPPORTED_OPENROUTER_MODELS",
    "OpenRouterProvider",
]
