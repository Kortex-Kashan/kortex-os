"""Unit tests for `BaseAIProvider`'s shared default behavior (Phase B / B2).

`test_connection`/`discover_models` are additive, concrete methods added to
the abstract base -- see `base_provider.py`'s module docstring for why.
These tests exist to pin down the DEFAULT behavior every pre-B2 provider
(and every test double across the whole suite) inherits unchanged, since
none of them override either method.
"""

from __future__ import annotations

import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse


class _MinimalProvider(BaseAIProvider):
    """The simplest possible concrete provider -- implements only what
    `BaseAIProvider` actually requires, so `test_connection`/`discover_models`
    are exercised purely via inheritance, with no override of either."""

    _SENTINEL_DEFAULT = ("model-a", "model-b")

    def __init__(self, healthy: bool = True, supported_models: list[str] | None = None) -> None:
        self._healthy = healthy
        self._metadata = AIProviderMetadata(
            provider_id="minimal",
            display_name="Minimal Provider",
            vendor="test",
            endpoint_type="local_host",
            supported_models=list(self._SENTINEL_DEFAULT) if supported_models is None else supported_models,
        )

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(request_id=request.request_id, text_content="ok")

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return self._healthy


@pytest.mark.asyncio
async def test_default_test_connection_delegates_to_health_check() -> None:
    provider = _MinimalProvider(healthy=True)
    assert await provider.test_connection("any-credential") is True
    assert await provider.test_connection(None) is True

    unhealthy = _MinimalProvider(healthy=False)
    assert await unhealthy.test_connection("any-credential") is False


@pytest.mark.asyncio
async def test_default_test_connection_ignores_the_credential_argument() -> None:
    """A credential-less provider's default validation must not treat
    receiving A credential as somehow different from receiving none."""
    provider = _MinimalProvider(healthy=True)
    assert await provider.test_connection("some-value") == await provider.test_connection(None)


@pytest.mark.asyncio
async def test_default_discover_models_reflects_static_supported_models() -> None:
    provider = _MinimalProvider(supported_models=["alpha", "beta"])
    models = await provider.discover_models("irrelevant-credential")

    assert {m.model_id for m in models} == {"alpha", "beta"}
    assert all(m.provider_id == "minimal" for m in models)
    assert all(m.provider_display_name == "Minimal Provider" for m in models)


@pytest.mark.asyncio
async def test_default_discover_models_matches_supported_models_property_exactly() -> None:
    """This is the SAME computation `AIOrchestrationEngine.list_models()`
    already performs by flattening every registered provider -- scoped to
    one provider. If these ever diverge, the two would silently disagree
    about what a provider can serve."""
    provider = _MinimalProvider(supported_models=["only-one"])
    models = await provider.discover_models(None)
    assert [m.model_id for m in models] == provider.supported_models


@pytest.mark.asyncio
async def test_default_discover_models_empty_for_a_provider_with_no_supported_models() -> None:
    provider = _MinimalProvider(supported_models=[])
    assert await provider.discover_models(None) == []
