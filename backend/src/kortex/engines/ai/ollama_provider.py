"""Ollama local model provider for the KORTEX OS AI Orchestration Engine (M6.1-2).

One `OllamaProvider` instance per configured model. `LLMRequest` deliberately
carries no `model_id` field (see `router.py`'s `_MODEL_ID_REJECTION`) --
model selection happens by registering one provider instance per model and
letting `ModelRouter`'s existing candidate-selection-by-`supported_models`
choose between them, rather than adding a field to a shared DTO every
provider and the router depend on.

Uses a plain `httpx.AsyncClient`, not the Connector Engine's SSRF-hardened
HTTP driver: that driver's threat model is arbitrary, user-supplied
connector-profile URLs, and it unconditionally blocks loopback/private-
network targets -- exactly where Ollama's own default endpoint lives. An
operator-configured Ollama `base_url` is trusted infrastructure
configuration, the same trust class as a database connection string, not
user input.

Implements no retry/circuit-breaker/timeout logic of its own:
`KernelProductionBootstrap.create_ai_engine` already wraps every provider
passed via `custom_providers` in `ResilientAIProvider` (see
`bootstrap.py`), which applies exactly that generically to any
`BaseAIProvider`. Raising `TransientProviderError`/`PermanentProviderError`
-- this module's only error-handling responsibility -- lets
`RetryPolicy.is_transient` classify failures reliably by exception type,
rather than relying on its message-based fallback heuristic for httpx's own
exception types, which do not subclass Python's builtin
`ConnectionError`/`OSError`.

Streaming is explicitly out of scope for M6.1 (ships non-streaming
generation only, per the milestone's own ADR-flagged deferral) -- every
request sets `"stream": false`.

Embeddings are out of scope for M6.1's generation-focused objective;
`generate_embeddings` raises a clear, typed error rather than fabricating
vectors or silently succeeding -- the same "explicit failure over fake
success" precedent established for the Connector Engine's governed
external execution fix (M6.0-4).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import PermanentProviderError, TransientProviderError
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse

logger = logging.getLogger("kortex.engines.ai.ollama_provider")

DEFAULT_OLLAMA_TIMEOUT_SECONDS: float = 55.0
"""Slightly under the AI Engine's own 60s default outer generation timeout
(`AIEngineRuntimeConfig.default_generation_timeout_seconds`), so this
provider's own HTTP call fails on its own terms rather than being cancelled
mid-flight by the outer `asyncio.wait_for`/`asyncio.timeout` wrappers that
already exist around it (`engine.py`, `resilience.py`)."""


class OllamaProvider(BaseAIProvider):
    """Real local-model provider backed by a running Ollama instance's HTTP API.

    Exactly one model per instance -- see module docstring for why.
    """

    def __init__(
        self,
        base_url: str,
        model_name: str,
        timeout_seconds: float = DEFAULT_OLLAMA_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url must not be empty.")
        if not model_name or not model_name.strip():
            raise ValueError("model_name must not be empty.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0.")

        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._metadata = AIProviderMetadata(
            provider_id=f"ollama-{model_name}",
            display_name=f"Ollama ({model_name})",
            vendor="ollama",
            endpoint_type="local_host",
            url=self._base_url,
            credential_requirement="none",
            supported_models=[model_name],
        )
        # Own the client only if the caller didn't inject one -- tests
        # inject a mock transport; production constructs its own and is
        # responsible for closing it via `aclose()`.
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def aclose(self) -> None:
        """Release the underlying HTTP client, if this instance owns one."""
        if self._owns_client:
            await self._client.aclose()

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate a non-streaming completion via Ollama's `/api/generate` endpoint."""
        start = time.perf_counter()
        options: dict[str, Any] = {"temperature": request.temperature}
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        payload: dict[str, Any] = {
            "model": self._model_name,
            "prompt": request.prompt,
            "stream": False,
            "options": options,
        }
        if request.system_instruction:
            payload["system"] = request.system_instruction

        try:
            http_response = await self._client.post(f"{self._base_url}/api/generate", json=payload)
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"Ollama provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"Ollama provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"Ollama provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

        if http_response.status_code == 404:
            raise PermanentProviderError(
                f"Ollama model '{self._model_name}' is not available at '{self._base_url}' "
                "(404 from /api/generate -- model likely not pulled)."
            )
        if http_response.status_code == 429 or 500 <= http_response.status_code < 600:
            raise TransientProviderError(
                f"Ollama provider '{self.provider_id}' returned {http_response.status_code}: "
                f"{http_response.text[:500]}"
            )
        if http_response.status_code != 200:
            raise PermanentProviderError(
                f"Ollama provider '{self.provider_id}' returned unexpected status "
                f"{http_response.status_code}: {http_response.text[:500]}"
            )

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"Ollama provider '{self.provider_id}' returned a malformed (non-JSON) response."
            ) from exc

        text_content = data.get("response") if isinstance(data, dict) else None
        if not isinstance(text_content, str):
            keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise PermanentProviderError(
                f"Ollama provider '{self.provider_id}' response is missing the expected "
                f"'response' text field: {keys!r}"
            )

        prompt_tokens = int(data.get("prompt_eval_count", 0) or 0)
        completion_tokens = int(data.get("eval_count", 0) or 0)
        total_duration_ns = data.get("total_duration")
        execution_time_ms = (
            total_duration_ns / 1_000_000.0
            if isinstance(total_duration_ns, (int, float))
            else (time.perf_counter() - start) * 1000.0
        )

        return LLMResponse(
            request_id=request.request_id,
            text_content=text_content,
            tool_calls=[],
            token_usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
            execution_time_ms=execution_time_ms,
            provider_id=self.provider_id,
            model_name=self._model_name,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Not supported in M6.1's generation-focused scope -- fails explicitly."""
        raise PermanentProviderError(
            f"Ollama provider '{self.provider_id}' does not support embeddings generation "
            "in the current KORTEX configuration."
        )

    async def health_check(self) -> bool:
        """Reachable and serving the configured model, per Ollama's `/api/tags` listing."""
        try:
            http_response = await self._client.get(f"{self._base_url}/api/tags")
        except httpx.HTTPError:
            return False
        if http_response.status_code != 200:
            return False
        try:
            data = http_response.json()
        except ValueError:
            return False
        models = data.get("models", []) if isinstance(data, dict) else []
        if not isinstance(models, list):
            return False
        configured_family = self._model_name.split(":")[0]
        return any(
            isinstance(m, dict) and str(m.get("name", "")).split(":")[0] == configured_family
            for m in models
        )


__all__ = ["DEFAULT_OLLAMA_TIMEOUT_SECONDS", "OllamaProvider"]
