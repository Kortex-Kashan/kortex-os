"""Google Gemini cloud provider for the KORTEX OS AI Orchestration Engine (Phase B / B3).

Follows the canonical cloud-provider pattern `openai_provider.py` established in
B2, unchanged in every architectural respect: exactly ONE process-global
instance is registered in `ProviderRegistry`, holding a
`TenantCredentialResolver` reference rather than any tenant's key, and every
call resolves the CALLING tenant's credential fresh from `request.tenant_id`
(`generate_text`) or an explicit already-resolved `credential` argument
(`test_connection`/`discover_models`), uses it once, and lets it go out of
scope. See `credentials.py` for the full rationale behind that rule.

Raw `httpx`, no vendor SDK -- the same three reasons `openai_provider.py`
documents (existing repo convention, avoiding an SDK's own retry layer racing
`ResilientAIProvider`, and a small stable REST surface) apply identically
here, and no concrete blocker was found that would justify a `google-genai`
dependency.

Three things make Gemini's wire protocol genuinely different from OpenAI's,
all contained entirely inside this adapter:

1. **The model id is part of the URL path**, not the JSON body:
   `POST {base}/models/{model}:generateContent`. Every other provider in this
   package posts to one fixed URL with `model` in the body.
2. **Two distinct content-refusal shapes.** `promptFeedback.blockReason`
   means the prompt was rejected outright and no `candidates` were produced
   at all; `candidates[0].finishReason == "SAFETY"` (or `PROHIBITED_CONTENT`
   / `BLOCKLIST` / `RECITATION`) means generation started and was cut short.
   Neither OpenAI nor Anthropic splits refusal into two response shapes this
   way. Both are mapped to `PermanentProviderError` -- retrying identical
   content cannot succeed -- with distinct messages so the caller can tell
   which happened.
3. **The models endpoint lists the whole catalog**, including
   embedding-only, vision-only and other families this adapter does not
   implement, so `discover_models` filters on each entry's
   `supportedGenerationMethods` containing `"generateContent"`.

Credential transport is the `x-goog-api-key` HEADER, never the `?key=`
query-string form the Gemini docs also accept. Google expresses no preference
between the two, but KORTEX does: a credential in a URL is a credential that
can reach an access log, a proxy log, or an exception rendering a request
line. The header form keeps it in the same place `openai_provider.py` and
`anthropic_provider.py` keep theirs.

Embeddings are NOT implemented (`generate_embeddings` raises
`PermanentProviderError`), for exactly the reason `openai_provider.py`
documents: `BaseAIProvider.generate_embeddings(self, texts)` carries no
tenant identifier, so there is no channel through which this method could
resolve a tenant-scoped credential. That is a contract-level gap to be closed
deliberately in its own milestone, not worked around here.

Only the native Gemini Developer API (`generativelanguage.googleapis.com`) is
implemented. Vertex AI is a different product with different auth (GCP ADC,
project/region) and is explicitly out of scope for B3.
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

logger = logging.getLogger("kortex.engines.ai.gemini_provider")

GEMINI_PROVIDER_ID: str = "gemini"

DEFAULT_GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

DEFAULT_GEMINI_TIMEOUT_SECONDS: float = 55.0
"""Slightly under the AI Engine's own 60s default outer generation timeout
(`AIEngineRuntimeConfig.default_generation_timeout_seconds`) -- same precedent
and reasoning as `ollama_provider`/`openai_provider`: this provider's own HTTP
call should fail on its own terms rather than be cancelled mid-flight by the
outer `asyncio.timeout` wrapper `ResilientAIProvider` already applies."""

DEFAULT_GEMINI_MODEL: str = "gemini-2.5-flash"
"""Used only when neither `request.model_id` nor the tenant's configured
`AIProviderConfig.default_model` names one -- the last of three precedence
levels (see `generate_text`). Chosen as the price-performance tier, mirroring
`openai_provider`'s choice of its own cheap/fast default."""

SUPPORTED_GEMINI_MODELS: tuple[str, ...] = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
)
"""The static, small, explicitly-validated allow-list `ModelRouter`'s D1
`model_id` filter gates on (`AIProviderMetadata.supported_models`).

Routing metadata ONLY -- deliberately NOT a mirror of Gemini's full catalog,
which spans flash/pro/lite/preview generations plus embedding, vision and
other families this adapter does not implement. `discover_models()` is the
live, tenant-scoped source of truth for what a given key can actually reach;
this tuple decides only which *pinned* `model_id` values `ModelRouter` will
route here without needing a network call."""

_GENERATE_CONTENT_METHOD: str = "generateContent"
"""The `supportedGenerationMethods` entry a catalog model must advertise to be
usable by `generate_text`. Models lacking it (embedding-only, etc.) are
filtered out of `discover_models`."""

_BLOCKING_FINISH_REASONS: frozenset[str] = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"})
"""`finishReason` values that mean the response was withheld or truncated for
content reasons rather than completed. Distinct from `STOP` (normal) and
`MAX_TOKENS` (truncated but usable)."""

_MODEL_NAME_PREFIX: str = "models/"
"""Catalog entries name themselves `models/gemini-x`; `AIModelSummary.model_id`
carries the bare id so it lines up with what `LLMRequest.model_id` and
`supported_models` use."""

_MAX_DISCOVERY_PAGES: int = 50
"""Hard stop on `nextPageToken` following. Complete pagination is required
(B3 decision 3), but an unbounded `while` loop driven by a remote server's
own cursor is a hang risk if that server ever returns a self-referential or
never-emptying token. 50 pages at the API's 1000-per-page maximum is far
beyond any real catalog while still terminating."""


def _raise_for_status(response: httpx.Response, provider_id: str, operation: str) -> None:
    """Map a Gemini HTTP response's status code to the KORTEX error taxonomy.

    Classification is driven by the HTTP status code, which is authoritative
    and always present. The response body is consulted only to enrich the
    message: Google's error envelope is
    `{"error": {"code": <numeric http status>, "message": ..., "status":
    "<ENUM>"}}` (AIP-193 -- `code` is the numeric HTTP status, NOT a string),
    but nothing here *depends* on that shape. A body that is missing,
    non-JSON, or shaped differently degrades to a truncated raw excerpt
    rather than masking the real status with a parse error.

    Never includes request headers (the credential lives in `x-goog-api-key`)
    in any raised message.
    """
    if response.status_code == 200:
        return

    detail = _extract_error_message(response)

    if response.status_code in (401, 403):
        raise PermanentProviderError(
            f"Gemini provider '{provider_id}' was denied by the configured credential "
            f"during '{operation}' ({response.status_code}): {detail}"
        )
    if response.status_code == 404:
        raise PermanentProviderError(
            f"Gemini provider '{provider_id}' could not find the requested resource for "
            f"'{operation}' (model likely unavailable to this credential): {detail}"
        )
    if response.status_code == 429:
        raise TransientProviderError(f"Gemini provider '{provider_id}' was rate-limited during '{operation}': {detail}")
    if 500 <= response.status_code < 600:
        # Covers 500 INTERNAL, 503 UNAVAILABLE and 504 DEADLINE_EXCEEDED --
        # all three are documented as retryable.
        raise TransientProviderError(
            f"Gemini provider '{provider_id}' returned {response.status_code} during '{operation}': {detail}"
        )
    raise PermanentProviderError(
        f"Gemini provider '{provider_id}' rejected the request during '{operation}' ({response.status_code}): {detail}"
    )


def _extract_error_message(response: httpx.Response) -> str:
    """Best-effort human-readable detail from a Gemini error body.

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


class GeminiProvider(BaseAIProvider):
    """Real cloud provider backed by the native Gemini Developer REST API.

    Exactly one instance is ever registered, system-wide.
    `credential_resolver` is the ONLY channel through which this instance ever
    sees a tenant's key; it is resolved fresh on every call and never stored
    beyond that call's local scope.
    """

    def __init__(
        self,
        credential_resolver: TenantCredentialResolver,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        default_model: str = DEFAULT_GEMINI_MODEL,
        timeout_seconds: float = DEFAULT_GEMINI_TIMEOUT_SECONDS,
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
            provider_id=GEMINI_PROVIDER_ID,
            display_name="Google Gemini",
            vendor="google",
            endpoint_type="cloud",
            url=self._base_url,
            credential_requirement="api_key",
            secret_handle=provider_secret_handle(GEMINI_PROVIDER_ID),
            supported_models=(
                list(supported_models) if supported_models is not None else list(SUPPORTED_GEMINI_MODELS)
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
        """Credential goes in a header, never the URL -- see module docstring."""
        return {"x-goog-api-key": credential, "Content-Type": "application/json"}

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate content via `{base}/models/{model}:generateContent`.

        Model precedence (highest first): `request.model_id` (D1, an explicit
        caller pin -- `ModelRouter` has already verified it against
        `supported_models` before this is reached through routed execution) >
        the calling tenant's configured `AIProviderConfig.default_model` (via
        `ResolvedCredential`) > `DEFAULT_GEMINI_MODEL`.
        """
        start = time.perf_counter()
        resolved = await self._credential_resolver.resolve(request.tenant_id, self.provider_id)
        if resolved is None:
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' has no enabled, credentialed configuration for this tenant."
            )
        model = request.model_id or resolved.default_model or self._default_model

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {"temperature": request.temperature},
        }
        if request.system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": request.system_instruction}]}
        if request.max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens

        http_response = await self._post(
            f"{self._base_url}/models/{model}:{_GENERATE_CONTENT_METHOD}",
            payload=payload,
            credential=resolved.plaintext,
        )
        _raise_for_status(http_response, self.provider_id, "generate content")

        try:
            data = http_response.json()
        except ValueError as exc:
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' returned a malformed (non-JSON) response."
            ) from exc
        if not isinstance(data, dict):
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' returned a response that is not a JSON object."
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
            model_name=data.get("modelVersion") or model,
        )

    def _extract_text(self, data: dict[str, Any]) -> str:
        """Pull the generated text out of a Gemini response, or fail explicitly.

        Handles Gemini's two content-refusal shapes before anything else --
        see the module docstring. A refusal is never returned as empty text,
        because an empty string is indistinguishable from a legitimate short
        answer to the caller.
        """
        prompt_feedback = data.get("promptFeedback")
        if isinstance(prompt_feedback, dict):
            block_reason = prompt_feedback.get("blockReason")
            if block_reason:
                raise PermanentProviderError(
                    f"Gemini provider '{self.provider_id}' blocked the prompt before generation "
                    f"(blockReason={block_reason})."
                )

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' response is missing the expected 'candidates' list."
            )
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' response candidate is not a JSON object."
            )

        finish_reason = candidate.get("finishReason")
        if isinstance(finish_reason, str) and finish_reason in _BLOCKING_FINISH_REASONS:
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' withheld the response for content reasons "
                f"(finishReason={finish_reason})."
            )

        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise PermanentProviderError(
                f"Gemini provider '{self.provider_id}' response is missing the expected content parts."
            )
        texts = [part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)]
        if not texts:
            raise PermanentProviderError(f"Gemini provider '{self.provider_id}' response contains no text part.")
        # Gemini may split one answer across several text parts; joining is the
        # faithful reconstruction, not a heuristic.
        return "".join(texts)

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> tuple[int, int]:
        """Map `usageMetadata` onto KORTEX's prompt/completion token counts.

        Absent or partial usage metadata degrades to zeros rather than
        failing the whole generation -- the answer is still valid, and
        `LLMResponse.token_usage` is reporting, not correctness.
        """
        usage = data.get("usageMetadata")
        if not isinstance(usage, dict):
            return 0, 0
        prompt_tokens = int(usage.get("promptTokenCount", 0) or 0)
        completion_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
        return prompt_tokens, completion_tokens

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Not supported in Phase B / B3 -- fails explicitly. See module docstring."""
        raise PermanentProviderError(
            f"Gemini provider '{self.provider_id}' does not support embeddings generation "
            "in the current KORTEX configuration (no tenant-scoped credential channel reaches "
            "this method)."
        )

    async def health_check(self) -> bool:
        """Reachability only, per `BaseAIProvider.health_check`'s own contract.

        No credential is available here (the method takes none), so this
        cannot and does not assert anything about authentication state --
        `test_connection` is the credential-aware counterpart. Any HTTP
        response, including a 401, proves the network path to Gemini is up;
        only a transport-level failure means it is not.
        """
        try:
            await self._client.get(f"{self._base_url}/models")
        except httpx.HTTPError:
            return False
        return True

    async def test_connection(self, credential: str | None = None) -> bool:
        """Validate `credential` with the cheapest real authenticated Gemini call.

        Uses the first page of the models catalog rather than a generation
        request: it is authenticated, consumes no input or output tokens, and
        costs nothing -- satisfying "must not perform an LLM completion".
        """
        if credential is None:
            raise PermanentProviderError(f"Gemini provider '{self.provider_id}' requires a credential to test.")
        await self._fetch_models_page(credential, page_token=None)
        return True

    async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
        """Live, complete model discovery via `{base}/models`.

        Follows `nextPageToken` to exhaustion (B3 decision 3) and retains only
        models advertising `generateContent`, so the result is the full set of
        models this credential can actually generate with -- not the raw
        catalog, and not a page of it.

        Falls back to the static `supported_models` default
        (`BaseAIProvider.discover_models`) when no credential is available:
        Gemini's catalog endpoint requires authentication, so there is no
        unauthenticated live source to consult.
        """
        if credential is None:
            return await super().discover_models(credential)

        summaries: list[AIModelSummary] = []
        page_token: str | None = None
        for _ in range(_MAX_DISCOVERY_PAGES):
            payload = await self._fetch_models_page(credential, page_token=page_token)
            entries = payload.get("models")
            if not isinstance(entries, list):
                raise PermanentProviderError(
                    f"Gemini provider '{self.provider_id}' models response is missing the expected 'models' list."
                )
            summaries.extend(self._summaries_from_entries(entries))

            next_token = payload.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return summaries
            page_token = next_token

        raise TransientProviderError(
            f"Gemini provider '{self.provider_id}' model discovery exceeded "
            f"{_MAX_DISCOVERY_PAGES} pages without exhausting its cursor."
        )

    def _summaries_from_entries(self, entries: list[Any]) -> list[AIModelSummary]:
        """Normalize catalog entries, keeping only `generateContent` models.

        Malformed individual entries are skipped rather than failing the whole
        listing: discovery is a best-effort informational read, and one bad
        row should not hide every good one.
        """
        summaries: list[AIModelSummary] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                continue
            methods = entry.get("supportedGenerationMethods")
            if not isinstance(methods, list) or _GENERATE_CONTENT_METHOD not in methods:
                continue
            model_id = name[len(_MODEL_NAME_PREFIX) :] if name.startswith(_MODEL_NAME_PREFIX) else name
            if not model_id:
                continue
            summaries.append(
                AIModelSummary(
                    model_id=model_id,
                    provider_id=self.provider_id,
                    provider_display_name=self.metadata.display_name,
                )
            )
        return summaries

    async def _fetch_models_page(self, credential: str, page_token: str | None) -> dict[str, Any]:
        """One authenticated page of the models catalog, status-normalized."""
        params: dict[str, str] = {}
        if page_token:
            params["pageToken"] = page_token

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
                f"Gemini provider '{self.provider_id}' returned a malformed (non-JSON) models response."
            ) from exc
        if not isinstance(payload, dict):
            raise PermanentProviderError(f"Gemini provider '{self.provider_id}' models response is not a JSON object.")
        return payload

    async def _post(self, url: str, payload: dict[str, Any], credential: str) -> httpx.Response:
        """POST with Gemini's transport errors mapped to the KORTEX taxonomy."""
        try:
            return await self._client.post(url, json=payload, headers=self._headers(credential))
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"Gemini provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"Gemini provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"Gemini provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc

    async def _get(self, url: str, params: dict[str, str], credential: str) -> httpx.Response:
        """GET with Gemini's transport errors mapped to the KORTEX taxonomy."""
        try:
            return await self._client.get(url, params=params, headers=self._headers(credential))
        except httpx.TimeoutException as exc:
            raise TransientProviderError(
                f"Gemini provider '{self.provider_id}' timed out contacting '{self._base_url}'."
            ) from exc
        except httpx.ConnectError as exc:
            raise TransientProviderError(
                f"Gemini provider '{self.provider_id}' could not connect to '{self._base_url}': {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise TransientProviderError(
                f"Gemini provider '{self.provider_id}' encountered a transport error: {exc}"
            ) from exc


__all__ = [
    "DEFAULT_GEMINI_BASE_URL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_GEMINI_TIMEOUT_SECONDS",
    "GEMINI_PROVIDER_ID",
    "SUPPORTED_GEMINI_MODELS",
    "GeminiProvider",
]
