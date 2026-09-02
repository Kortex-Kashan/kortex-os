"""Unit tests for the KORTEX OS AI Orchestration Engine Provider Registry (Milestone 2).

Target: 100% pass rate, 100% code coverage of `registry.py`. Uses only
local fake `BaseAIProvider` implementations — no Ollama/OpenAI/Anthropic,
no network calls, no credentials, no environment secrets, no external
services.
"""

from __future__ import annotations

import threading

import pytest

from kortex.core.exceptions import KortexError
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import (
    AIOrchestrationError,
    AIProviderError,
    ProviderAlreadyRegisteredError,
    ProviderNotFoundError,
    ProviderValidationError,
)
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.ai.registry import MetadataOnlyAIProvider, ProviderRegistry


def _metadata(
    provider_id: str = "fake-provider",
    endpoint_type: str = "local_host",
    supported_models: list[str] | None = None,
) -> AIProviderMetadata:
    return AIProviderMetadata(
        provider_id=provider_id,
        display_name=provider_id,
        vendor="fake",
        endpoint_type=endpoint_type,  # type: ignore[arg-type]
        supported_models=supported_models or [],
    )


class _FakeAIProvider(BaseAIProvider):
    """Configurable local fake provider — never touches a real AI service."""

    def __init__(
        self,
        provider_id: str = "fake-provider",
        endpoint_type: str = "local_host",
        supported_models: list[str] | None = None,
        healthy: bool = True,
    ) -> None:
        self._metadata = _metadata(provider_id, endpoint_type, supported_models)
        self._healthy = healthy
        self.health_check_call_count = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(request_id=request.request_id, text_content="fake")

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        self.health_check_call_count += 1
        return self._healthy


class _RaisingHealthCheckProvider(_FakeAIProvider):
    """A provider whose health_check() raises if ever called — used to prove
    the registry never calls it during registration."""

    async def health_check(self) -> bool:
        raise AssertionError("health_check() must not be called by ProviderRegistry.register()")


class _RaisingMetadataProvider(BaseAIProvider):
    """A provider whose metadata property raises when accessed."""

    @property
    def metadata(self) -> AIProviderMetadata:
        raise RuntimeError("metadata source unavailable")

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return False


class _BadMetadataTypeProvider(BaseAIProvider):
    """A provider whose metadata property returns something that is not AIProviderMetadata."""

    @property
    def metadata(self) -> object:  # type: ignore[override]
        return {"provider_id": "not-a-model-instance"}

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return False


# --- 1. Register a live provider and verify get() returns the same instance ---


def test_register_live_provider_and_get_returns_same_instance() -> None:
    registry = ProviderRegistry()
    provider = _FakeAIProvider(provider_id="p1")
    result = registry.register(provider)
    assert result is provider
    assert registry.get("p1") is provider


# --- 2. Register AIProviderMetadata creates a MetadataOnlyAIProvider ---


def test_register_bare_metadata_creates_metadata_only_provider() -> None:
    registry = ProviderRegistry()
    metadata = _metadata(provider_id="p1", supported_models=["m1"])
    result = registry.register(metadata)
    assert isinstance(result, MetadataOnlyAIProvider)
    retrieved = registry.get("p1")
    assert retrieved is result
    assert retrieved.metadata == metadata
    assert retrieved.provider_id == "p1"
    assert retrieved.supported_models == ["m1"]


@pytest.mark.asyncio
async def test_metadata_only_provider_generation_methods_raise_ai_provider_error() -> None:
    provider = MetadataOnlyAIProvider(_metadata(provider_id="p1"))
    request = LLMRequest(request_id="r1", tenant_id="t1", user_id="u1", conversation_id="c1", prompt="hi")
    with pytest.raises(AIProviderError):
        await provider.generate_text(request)
    with pytest.raises(AIProviderError):
        await provider.generate_embeddings(["a"])


@pytest.mark.asyncio
async def test_metadata_only_provider_health_check_always_false() -> None:
    provider = MetadataOnlyAIProvider(_metadata(provider_id="p1"))
    assert await provider.health_check() is False


# --- 3. Duplicate registration raises ProviderAlreadyRegisteredError ---


def test_duplicate_registration_raises() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1"))
    with pytest.raises(ProviderAlreadyRegisteredError):
        registry.register(_FakeAIProvider(provider_id="p1"))


# --- 4. Duplicate registration does not replace the original provider ---


def test_duplicate_registration_does_not_replace_original() -> None:
    registry = ProviderRegistry()
    original = _FakeAIProvider(provider_id="p1")
    registry.register(original)
    replacement = _FakeAIProvider(provider_id="p1")
    with pytest.raises(ProviderAlreadyRegisteredError):
        registry.register(replacement)
    assert registry.get("p1") is original


def test_duplicate_registration_across_live_and_metadata_only_forms() -> None:
    """Duplicate detection must apply regardless of which registration form is used second."""
    registry = ProviderRegistry()
    original = _FakeAIProvider(provider_id="p1")
    registry.register(original)
    with pytest.raises(ProviderAlreadyRegisteredError):
        registry.register(_metadata(provider_id="p1"))
    assert registry.get("p1") is original


# --- 5. Unknown get() raises ProviderNotFoundError ---


def test_unknown_provider_lookup_raises() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderNotFoundError):
        registry.get("does-not-exist")


# --- 6. unregister() removes an existing provider and returns True ---


def test_successful_unregister() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1"))
    assert registry.unregister("p1") is True
    with pytest.raises(ProviderNotFoundError):
        registry.get("p1")


# --- 7. unregister() on an unknown provider returns False ---


def test_unregister_unknown_provider_returns_false() -> None:
    registry = ProviderRegistry()
    assert registry.unregister("does-not-exist") is False


# --- 8. list_providers() returns metadata in registration order ---


def test_list_providers_preserves_registration_order() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="third"))
    registry.register(_FakeAIProvider(provider_id="first"))
    registry.register(_FakeAIProvider(provider_id="second"))
    ids = [meta.provider_id for meta in registry.list_providers()]
    assert ids == ["third", "first", "second"]


# --- 9. find_providers_by_endpoint_type() returns the correct candidates ---


def test_find_providers_by_endpoint_type() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="local-1", endpoint_type="local_host"))
    registry.register(_FakeAIProvider(provider_id="cloud-1", endpoint_type="cloud"))
    registry.register(_FakeAIProvider(provider_id="local-2", endpoint_type="local_host"))
    results = registry.find_providers_by_endpoint_type("local_host")
    assert [m.provider_id for m in results] == ["local-1", "local-2"]


# --- 10. find_providers_supporting_model() returns the correct candidates ---


def test_find_providers_supporting_model() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1", supported_models=["qwen2.5:7b"]))
    registry.register(_FakeAIProvider(provider_id="p2", supported_models=["deepseek-v3"]))
    registry.register(_FakeAIProvider(provider_id="p3", supported_models=["qwen2.5:7b", "deepseek-v3"]))
    results = registry.find_providers_supporting_model("qwen2.5:7b")
    assert [m.provider_id for m in results] == ["p1", "p3"]


# --- 11. Empty searches return [] ---


def test_find_providers_returns_empty_list_when_no_match() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1", endpoint_type="cloud", supported_models=["a"]))
    assert registry.find_providers_by_endpoint_type("network") == []
    assert registry.find_providers_supporting_model("nonexistent-model") == []
    assert ProviderRegistry().find_providers_by_endpoint_type("local_host") == []


# --- 12. Provider with health_check() == False remains registered ---


@pytest.mark.asyncio
async def test_unhealthy_provider_remains_registered() -> None:
    registry = ProviderRegistry()
    provider = _FakeAIProvider(provider_id="p1", healthy=False)
    registry.register(provider)
    assert await provider.health_check() is False
    assert registry.get("p1") is provider
    assert "p1" in [m.provider_id for m in registry.list_providers()]


# --- 13. register() never calls health_check() ---


def test_health_check_not_called_during_registration() -> None:
    registry = ProviderRegistry()
    provider = _RaisingHealthCheckProvider(provider_id="p1")
    registry.register(provider)  # must not raise, must not call health_check


def test_health_check_call_count_stays_zero_after_registration() -> None:
    registry = ProviderRegistry()
    provider = _FakeAIProvider(provider_id="p1")
    registry.register(provider)
    assert provider.health_check_call_count == 0


# --- 14. Metadata property raising -> ProviderValidationError chained ---


def test_register_wraps_metadata_access_exception() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderValidationError) as exc_info:
        registry.register(_RaisingMetadataProvider())
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, RuntimeError)


# --- 15. Invalid metadata -> ProviderValidationError ---


def test_register_rejects_non_provider_object() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderValidationError):
        registry.register(object())  # type: ignore[arg-type]


def test_register_rejects_wrong_metadata_type() -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderValidationError):
        registry.register(_BadMetadataTypeProvider())


# --- 16. Empty/whitespace provider_id -> ProviderValidationError ---


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_register_rejects_empty_provider_id_live_provider(provider_id: str) -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderValidationError):
        registry.register(_FakeAIProvider(provider_id=provider_id))


@pytest.mark.parametrize("provider_id", ["", "   "])
def test_register_rejects_empty_provider_id_bare_metadata(provider_id: str) -> None:
    registry = ProviderRegistry()
    with pytest.raises(ProviderValidationError):
        registry.register(_metadata(provider_id=provider_id))


@pytest.mark.parametrize("provider_id", [" p1", "p1 ", " p1 ", "\tp1", "p1\n"])
def test_register_rejects_provider_id_with_leading_or_trailing_whitespace(provider_id: str) -> None:
    """The registry must reject a padded provider_id outright rather than
    silently stripping it — silently trimming would let the registry's
    internal key diverge from `provider.metadata.provider_id` as returned
    by list_providers()/find_*()."""
    registry = ProviderRegistry()
    with pytest.raises(ProviderValidationError):
        registry.register(_FakeAIProvider(provider_id=provider_id))


def test_get_and_unregister_use_exact_match_no_normalization() -> None:
    """Since register() rejects padded IDs, get()/unregister() must not
    apply any whitespace normalization of their own — a padded lookup key
    must simply not match, not be silently coerced into matching."""
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1"))
    with pytest.raises(ProviderNotFoundError):
        registry.get(" p1 ")
    assert registry.unregister(" p1 ") is False
    # the real, unpadded key still works
    assert registry.get("p1") is not None
    assert registry.unregister("p1") is True


# --- 17. Separate ProviderRegistry instances are isolated ---


def test_independent_registry_instances() -> None:
    registry_a = ProviderRegistry()
    registry_b = ProviderRegistry()
    registry_a.register(_FakeAIProvider(provider_id="p1"))
    with pytest.raises(ProviderNotFoundError):
        registry_b.get("p1")
    assert registry_b.list_providers() == []


# --- 18. Concurrent registration of distinct provider IDs does not corrupt state ---


def test_concurrent_registration_of_distinct_ids() -> None:
    registry = ProviderRegistry()
    provider_ids = [f"provider-{i}" for i in range(50)]
    errors: list[Exception] = []

    def _register(pid: str) -> None:
        try:
            registry.register(_FakeAIProvider(provider_id=pid))
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=_register, args=(pid,)) for pid in provider_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert {m.provider_id for m in registry.list_providers()} == set(provider_ids)


def test_concurrent_duplicate_registration_exactly_one_succeeds() -> None:
    """Two threads racing to register the SAME provider_id must not both
    succeed, and must not corrupt the registry into an inconsistent state —
    exactly one registration wins, the other observes
    ProviderAlreadyRegisteredError."""
    registry = ProviderRegistry()
    successes: list[BaseAIProvider] = []
    failures: list[Exception] = []
    barrier = threading.Barrier(20)

    def _attempt() -> None:
        barrier.wait()
        try:
            successes.append(registry.register(_FakeAIProvider(provider_id="contested")))
        except ProviderAlreadyRegisteredError as exc:
            failures.append(exc)

    threads = [threading.Thread(target=_attempt) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 1
    assert len(failures) == 19
    assert registry.get("contested") is successes[0]


def test_unregister_then_reregister_moves_provider_to_end_of_order() -> None:
    """Documents and verifies plain-dict re-insertion semantics: removing
    and re-adding a provider_id moves it to the end of list_providers()'s
    order, it does not retain its original position."""
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="a"))
    registry.register(_FakeAIProvider(provider_id="b"))
    registry.register(_FakeAIProvider(provider_id="c"))
    registry.unregister("a")
    registry.register(_FakeAIProvider(provider_id="a"))
    ids = [m.provider_id for m in registry.list_providers()]
    assert ids == ["b", "c", "a"]


# --- 19. Exception hierarchy ---


def test_exception_hierarchy() -> None:
    for exc_cls in (ProviderAlreadyRegisteredError, ProviderNotFoundError, ProviderValidationError):
        assert issubclass(exc_cls, AIOrchestrationError)
        assert issubclass(exc_cls, KortexError)
        assert not issubclass(exc_cls, AIProviderError)


# --- Additional: registry does not expose mutable internal state ---


def test_list_providers_returns_independent_list() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1"))
    result = registry.list_providers()
    result.append(_metadata(provider_id="injected"))
    assert [m.provider_id for m in registry.list_providers()] == ["p1"]


def test_find_providers_results_are_independent_lists() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1", endpoint_type="local_host"))
    result = registry.find_providers_by_endpoint_type("local_host")
    result.clear()
    assert len(registry.find_providers_by_endpoint_type("local_host")) == 1


def test_clear_removes_all_registrations() -> None:
    registry = ProviderRegistry()
    registry.register(_FakeAIProvider(provider_id="p1"))
    registry.register(_FakeAIProvider(provider_id="p2"))
    registry.clear()
    assert registry.list_providers() == []
    with pytest.raises(ProviderNotFoundError):
        registry.get("p1")
