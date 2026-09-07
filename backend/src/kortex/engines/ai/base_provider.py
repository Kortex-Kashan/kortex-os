"""Abstract Base Class for AI providers in the KORTEX OS AI Orchestration Engine.

Mirrors `kortex.engines.connector.base_driver.BaseConnectorDriver`: one
abstract class covers every provider category (local-host, network/LAN,
and cloud), differentiated only by `AIProviderMetadata.endpoint_type` —
there is deliberately no separate subclass hierarchy per category, so that
local and network providers are never structurally second-class relative
to cloud providers.

No concrete provider (dummy or real) is implemented here — that is
Milestone 2 scope, once the provider registry exists to register one.

`test_connection`/`discover_models` (Phase B / B2) are additive, concrete
(non-abstract) members added when the OpenAI provider needed a
tenant-credentialed validation/discovery hook that `health_check()`
structurally cannot provide (`health_check()` takes no credential and is
documented, below, as reachability-only). Every provider that predates
this — `OllamaProvider`, every test double across the suite — inherits the
default implementations unchanged; nothing was made abstract, so nothing
that already satisfied this contract stops satisfying it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kortex.engines.ai.models import AIModelSummary, AIProviderMetadata, LLMRequest, LLMResponse


class BaseAIProvider(ABC):
    """Abstract base class for all AI provider adapters.

    All provider implementations (local runtimes such as Ollama/llama.cpp/
    vLLM, network/LAN endpoints, cloud APIs, and the Milestone 2 reference
    dummy provider) implement this contract.
    """

    @property
    @abstractmethod
    def metadata(self) -> AIProviderMetadata:
        """Return immutable provider metadata object."""

    @property
    def provider_id(self) -> str:
        """Return unique provider identifier string."""
        return self.metadata.provider_id

    @property
    def supported_models(self) -> list[str]:
        """Return list of model identifiers this provider exposes."""
        return self.metadata.supported_models

    @abstractmethod
    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        """Generate a text completion for the given request."""

    @abstractmethod
    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for the given input texts."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return whether this provider is currently reachable.

        A minimal reachability check only. Installation, configuration,
        licensing, and authorization state are explicitly out of scope for
        this method in Milestone 1 — those are richer, provider-registry-
        level concerns (Milestone 2) to be added only if the actual
        registry design demonstrates they are necessary, not built in
        advance of that need.
        """

    async def test_connection(self, credential: str | None = None) -> bool:
        """Validate that `credential` (if any) actually works against this provider.

        The credential-aware counterpart to `health_check()`: reachability
        without authentication state versus authentication state itself.
        `credential` is a resolved plaintext value (never a handle) supplied
        by the caller for exactly this one check and is never retained —
        see `kortex.engines.ai.credentials`, whose no-caching rule this
        method must not violate by holding onto it.

        Default: delegates to `health_check()` and ignores `credential`,
        preserving today's behavior for every credential-less provider
        (`local_host`/`network` endpoints such as Ollama, and every
        pre-B2 test double). A provider whose `credential_requirement`
        is not `"none"` should override this to perform the cheapest real
        authenticated call its API offers, and raise a
        `PermanentProviderError`/`TransientProviderError` describing *why*
        on failure rather than returning `False` — the caller can then
        report an actionable reason instead of a bare boolean.
        """
        return await self.health_check()

    async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
        """Report the models this provider can currently serve.

        Default: reflects this provider's own static
        `AIProviderMetadata.supported_models` — identical to what
        `AIOrchestrationEngine.list_models()` already computes today by
        flattening every registered provider's metadata, just scoped to
        one provider and ignoring `credential`. This keeps every existing
        provider's behavior byte-for-byte unchanged.

        A provider backed by a real catalog API should override this to
        call it with `credential` and return the live result instead of
        the static default — the live result is the source of truth for a
        connection test; the static default exists so `ModelRouter`'s
        existing `supported_models`-gated routing keeps working for a
        provider that has not (or cannot) implement live discovery.
        """
        return [
            AIModelSummary(
                model_id=model_id,
                provider_id=self.provider_id,
                provider_display_name=self.metadata.display_name,
            )
            for model_id in self.supported_models
        ]


__all__ = ["BaseAIProvider"]
