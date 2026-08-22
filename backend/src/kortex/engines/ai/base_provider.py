"""Abstract Base Class for AI providers in the KORTEX OS AI Orchestration Engine.

Mirrors `kortex.engines.connector.base_driver.BaseConnectorDriver`: one
abstract class covers every provider category (local-host, network/LAN,
and cloud), differentiated only by `AIProviderMetadata.endpoint_type` —
there is deliberately no separate subclass hierarchy per category, so that
local and network providers are never structurally second-class relative
to cloud providers.

No concrete provider (dummy or real) is implemented here — that is
Milestone 2 scope, once the provider registry exists to register one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse


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


__all__ = ["BaseAIProvider"]
