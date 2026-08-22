"""Unit tests for the KORTEX OS AI Orchestration Engine Model Router (Milestone 3).

Every test is failure-oriented: each one fails if a specific invariant from
`docs/architecture/ai_engine_m3_model_router_spec.md` §15 is violated, rather
than merely exercising a happy path.

Local fakes only — no Ollama/OpenAI/Anthropic, no network, no credentials,
no external services.
"""

from __future__ import annotations

import ast
import contextlib
import pathlib
import threading

import pytest

from kortex.core.exceptions import KortexError
from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.exceptions import (
    AIOrchestrationError,
    AIProviderError,
    NoRoutableProviderError,
    ProviderNotFoundError,
    ProviderNotRoutableError,
    RoutingError,
    RoutingValidationError,
)
from kortex.engines.ai.interfaces import IModelRouter
from kortex.engines.ai.models import AIProviderMetadata, LLMRequest, LLMResponse
from kortex.engines.ai.registry import MetadataOnlyAIProvider, ProviderRegistry
from kortex.engines.ai.router import ModelRouter

# Distinctive sentinels used to prove no exception message ever leaks them (I7).
# Not credentials: `SECRET_HANDLE` is an opaque Security Engine *handle*, and
# the host is an RFC 2606 `.invalid` name that cannot resolve.
SECRET_URL = "https://super-secret-host.example.invalid:9999/v1"  # noqa: S105
SECRET_HANDLE = "secret:kortex/ai/DO-NOT-LEAK-THIS-HANDLE"  # noqa: S105


def _metadata(
    provider_id: str = "p",
    endpoint_type: str = "local_host",
    supported_models: list[str] | None = None,
    with_secrets: bool = False,
) -> AIProviderMetadata:
    if with_secrets:
        return AIProviderMetadata(
            provider_id=provider_id,
            display_name=provider_id,
            vendor="fake",
            endpoint_type=endpoint_type,  # type: ignore[arg-type]
            url=SECRET_URL,
            credential_requirement="api_key",
            secret_handle=SECRET_HANDLE,
            supported_models=supported_models or [],
        )
    return AIProviderMetadata(
        provider_id=provider_id,
        display_name=provider_id,
        vendor="fake",
        endpoint_type=endpoint_type,  # type: ignore[arg-type]
        supported_models=supported_models or [],
    )


class _FakeProvider(BaseAIProvider):
    """Executable local fake. Records whether forbidden methods were called."""

    def __init__(
        self,
        provider_id: str = "p",
        endpoint_type: str = "local_host",
        supported_models: list[str] | None = None,
        healthy: bool = True,
        with_secrets: bool = False,
    ) -> None:
        self._metadata = _metadata(provider_id, endpoint_type, supported_models, with_secrets)
        self._healthy = healthy
        self.generate_text_calls = 0
        self.generate_embeddings_calls = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        self.generate_text_calls += 1
        return LLMResponse(request_id=request.request_id, text_content="fake")

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        self.generate_embeddings_calls += 1
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return self._healthy


class _ExplodingHealthProvider(_FakeProvider):
    """Proves the router never calls health_check() (I1)."""

    async def health_check(self) -> bool:
        raise AssertionError("ModelRouter must never call health_check()")


class _MetadataOnlySubclass(MetadataOnlyAIProvider):
    """Proves executability filtering covers subclasses too (I4)."""


class _VaryingMetadataProvider(BaseAIProvider):
    """Returns a different endpoint_type on every `metadata` access.

    Exists to prove the single-read rule (I13): whatever the router filters
    on must be exactly what it returns.
    """

    def __init__(self, provider_id: str = "varying") -> None:
        self._provider_id = provider_id
        self.access_count = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        self.access_count += 1
        endpoint = "local_host" if self.access_count % 2 == 1 else "cloud"
        return _metadata(self._provider_id, endpoint_type=endpoint)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


class _IdentityFlipProvider(BaseAIProvider):
    """Truthful for the first `truthful_reads` metadata accesses, then claims a
    different identity.

    Reproduces a provider whose `metadata` property is unstable *across* the
    router's enumerate-then-resolve sequence: registration consumes read 1,
    `list_providers()` consumes read 2, and the router's own authoritative
    read is read 3 — which is where the mismatch appears. This is the only
    way to exercise the discovery-path identity-consistency branch (I8).
    """

    def __init__(self, real_id: str, other_id: str, truthful_reads: int = 2) -> None:
        self._real_id = real_id
        self._other_id = other_id
        self._truthful_reads = truthful_reads
        self.reads = 0

    @property
    def metadata(self) -> AIProviderMetadata:
        self.reads += 1
        claimed = self._real_id if self.reads <= self._truthful_reads else self._other_id
        return _metadata(claimed)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


class _LyingIdentityProvider(BaseAIProvider):
    """Reports a provider_id different from the key it is registered under."""

    def __init__(self, registered_as: str, claims_to_be: str) -> None:
        self._registered_as = registered_as
        self._claims = claims_to_be
        self._first = True

    @property
    def metadata(self) -> AIProviderMetadata:
        # Truthful on the first read (so registration stores it under
        # `registered_as`), then claims a different identity afterwards.
        if self._first:
            self._first = False
            return _metadata(self._registered_as)
        return _metadata(self._claims)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def _request() -> LLMRequest:
    return LLMRequest(
        request_id="r1", tenant_id="t1", user_id="u1", conversation_id="c1", prompt="hi"
    )


def _router(*providers: BaseAIProvider) -> tuple[ModelRouter, ProviderRegistry]:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return ModelRouter(registry), registry


# --------------------------------------------------------------------------
# Conformance
# --------------------------------------------------------------------------


def test_router_satisfies_imodelrouter_protocol() -> None:
    router, _ = _router()
    assert isinstance(router, IModelRouter)


# --------------------------------------------------------------------------
# Context validation (I14, I16)
# --------------------------------------------------------------------------


async def test_model_id_key_is_rejected_with_specific_explanation() -> None:
    """I14: model routing is refused loudly, not silently mishandled."""
    router, _ = _router(_FakeProvider("p1", supported_models=["qwen"]))
    with pytest.raises(RoutingValidationError) as exc_info:
        await router.select_model(_request(), {"model_id": "qwen"})
    message = str(exc_info.value)
    assert "model_id" in message
    assert "LLMRequest" in message  # explains *why*, not just "unknown key"


async def test_unknown_context_key_rejected() -> None:
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(RoutingValidationError):
        await router.select_model(_request(), {"endpointtype": "cloud"})


async def test_validation_error_does_not_echo_submitted_values() -> None:
    """I16: an unknown key's value must not appear in the error message."""
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(RoutingValidationError) as exc_info:
        await router.select_model(_request(), {"unexpected": "SENSITIVE-VALUE-XYZ"})
    assert "SENSITIVE-VALUE-XYZ" not in str(exc_info.value)
    assert "unexpected" in str(exc_info.value)


@pytest.mark.parametrize("bad_context", [None, [], "ctx", 42])
async def test_non_dict_context_rejected(bad_context: object) -> None:
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(RoutingValidationError):
        await router.select_model(_request(), bad_context)  # type: ignore[arg-type]


@pytest.mark.parametrize("provider_id", ["", "   ", "\t"])
async def test_blank_provider_id_rejected(provider_id: str) -> None:
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(RoutingValidationError):
        await router.select_model(_request(), {"provider_id": provider_id})


async def test_invalid_endpoint_type_rejected() -> None:
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(RoutingValidationError):
        await router.select_model(_request(), {"endpoint_type": "on_prem"})


@pytest.mark.parametrize("bad_value", ["yes", 1, None])
async def test_non_bool_allow_cloud_rejected(bad_value: object) -> None:
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(RoutingValidationError):
        await router.select_model(_request(), {"allow_cloud": bad_value})


async def test_empty_context_is_valid() -> None:
    router, _ = _router(_FakeProvider("p1"))
    assert (await router.select_model(_request(), {})).provider_id == "p1"


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


async def test_empty_registry_raises() -> None:
    router, _ = _router()
    with pytest.raises(NoRoutableProviderError):
        await router.select_model(_request(), {})


async def test_endpoint_type_filter_selects_only_matching() -> None:
    router, _ = _router(
        _FakeProvider("local-1", endpoint_type="local_host"),
        _FakeProvider("net-1", endpoint_type="network"),
    )
    result = await router.select_candidates(_request(), {"endpoint_type": "network"})
    assert [m.provider_id for m in result] == ["net-1"]


async def test_no_match_raises_from_select_model_but_returns_empty_from_candidates() -> None:
    """I6: discovery is a query — matching nothing is an empty result, not an error."""
    router, _ = _router(_FakeProvider("local-1", endpoint_type="local_host"))
    assert await router.select_candidates(_request(), {"endpoint_type": "network"}) == []
    with pytest.raises(NoRoutableProviderError):
        await router.select_model(_request(), {"endpoint_type": "network"})


# --------------------------------------------------------------------------
# Ordering and determinism (I2, I12)
# --------------------------------------------------------------------------


async def test_local_beats_earlier_registered_network() -> None:
    """Ranking must override registration order."""
    router, _ = _router(
        _FakeProvider("net-first", endpoint_type="network"),
        _FakeProvider("local-second", endpoint_type="local_host"),
    )
    assert (await router.select_model(_request(), {})).provider_id == "local-second"


async def test_full_rank_order_local_then_network_then_cloud() -> None:
    router, _ = _router(
        _FakeProvider("c", endpoint_type="cloud"),
        _FakeProvider("n", endpoint_type="network"),
        _FakeProvider("l", endpoint_type="local_host"),
    )
    result = await router.select_candidates(_request(), {"allow_cloud": True})
    assert [m.provider_id for m in result] == ["l", "n", "c"]


async def test_same_rank_preserves_registration_order() -> None:
    router, _ = _router(
        _FakeProvider("third", endpoint_type="local_host"),
        _FakeProvider("first", endpoint_type="local_host"),
        _FakeProvider("second", endpoint_type="local_host"),
    )
    result = await router.select_candidates(_request(), {})
    assert [m.provider_id for m in result] == ["third", "first", "second"]


async def test_repeated_calls_are_identical() -> None:
    """I2: determinism across many invocations."""
    router, _ = _router(
        _FakeProvider("a", endpoint_type="network"),
        _FakeProvider("b", endpoint_type="local_host"),
        _FakeProvider("c", endpoint_type="network"),
    )
    results = {(await router.select_model(_request(), {})).provider_id for _ in range(50)}
    assert results == {"b"}


async def test_select_model_equals_first_candidate() -> None:
    """I12."""
    router, _ = _router(
        _FakeProvider("c", endpoint_type="cloud"),
        _FakeProvider("l", endpoint_type="local_host"),
    )
    context = {"allow_cloud": True}
    candidates = await router.select_candidates(_request(), context)
    chosen = await router.select_model(_request(), context)
    assert chosen == candidates[0]


async def test_candidate_list_is_independent() -> None:
    router, _ = _router(_FakeProvider("p1"))
    result = await router.select_candidates(_request(), {})
    result.clear()
    assert len(await router.select_candidates(_request(), {})) == 1


# --------------------------------------------------------------------------
# Executability (I4)
# --------------------------------------------------------------------------


async def test_metadata_only_provider_never_discovered() -> None:
    router, registry = _router(_FakeProvider("real"))
    registry.register(_metadata("meta-only"))
    result = await router.select_candidates(_request(), {})
    assert [m.provider_id for m in result] == ["real"]


async def test_metadata_only_subclass_also_excluded() -> None:
    router, registry = _router()
    registry.register(_MetadataOnlySubclass(_metadata("sub-meta-only")))
    assert await router.select_candidates(_request(), {}) == []


async def test_registry_of_only_metadata_only_providers_raises() -> None:
    router, registry = _router()
    registry.register(_metadata("m1"))
    registry.register(_metadata("m2"))
    with pytest.raises(NoRoutableProviderError):
        await router.select_model(_request(), {})


async def test_pinned_metadata_only_provider_raises_not_routable() -> None:
    router, registry = _router()
    registry.register(_metadata("meta-only"))
    with pytest.raises(ProviderNotRoutableError):
        await router.select_model(_request(), {"provider_id": "meta-only"})


# --------------------------------------------------------------------------
# Health boundary (I1)
# --------------------------------------------------------------------------


async def test_health_check_is_never_called() -> None:
    router, _ = _router(_ExplodingHealthProvider("p1"))
    assert (await router.select_model(_request(), {})).provider_id == "p1"


async def test_unhealthy_but_executable_provider_is_still_selected() -> None:
    """Registration and reachability are orthogonal; the router does not pre-empt execution."""
    router, _ = _router(_FakeProvider("down", healthy=False))
    assert (await router.select_model(_request(), {})).provider_id == "down"


# --------------------------------------------------------------------------
# Cloud egress default (I11) — the security-critical group
# --------------------------------------------------------------------------


async def test_cloud_excluded_by_default_in_discovery() -> None:
    """I11: the core fail-closed guarantee."""
    router, _ = _router(_FakeProvider("cloud-1", endpoint_type="cloud"))
    assert await router.select_candidates(_request(), {}) == []


async def test_only_cloud_registry_raises_by_default() -> None:
    router, _ = _router(
        _FakeProvider("c1", endpoint_type="cloud"),
        _FakeProvider("c2", endpoint_type="cloud"),
    )
    with pytest.raises(NoRoutableProviderError) as exc_info:
        await router.select_model(_request(), {})
    assert "allow_cloud" in str(exc_info.value)  # error must be actionable


async def test_cloud_included_with_explicit_allow_cloud() -> None:
    router, _ = _router(_FakeProvider("cloud-1", endpoint_type="cloud"))
    assert (await router.select_model(_request(), {"allow_cloud": True})).provider_id == "cloud-1"


async def test_local_preferred_over_cloud_even_when_cloud_allowed() -> None:
    router, _ = _router(
        _FakeProvider("cloud-1", endpoint_type="cloud"),
        _FakeProvider("local-1", endpoint_type="local_host"),
    )
    assert (await router.select_model(_request(), {"allow_cloud": True})).provider_id == "local-1"


async def test_explicit_cloud_endpoint_type_needs_no_allow_cloud() -> None:
    """An explicit placement choice is itself the conscious decision."""
    router, _ = _router(_FakeProvider("cloud-1", endpoint_type="cloud"))
    chosen = await router.select_model(_request(), {"endpoint_type": "cloud"})
    assert chosen.provider_id == "cloud-1"


async def test_explicit_pin_to_cloud_needs_no_allow_cloud() -> None:
    router, _ = _router(_FakeProvider("cloud-1", endpoint_type="cloud"))
    chosen = await router.select_model(_request(), {"provider_id": "cloud-1"})
    assert chosen.provider_id == "cloud-1"


async def test_network_endpoint_is_not_treated_as_cloud() -> None:
    """LAN self-hosted is on-premise; it must not require the cloud opt-in."""
    router, _ = _router(_FakeProvider("lan-1", endpoint_type="network"))
    assert (await router.select_model(_request(), {})).provider_id == "lan-1"


# --------------------------------------------------------------------------
# Explicit pin
# --------------------------------------------------------------------------


async def test_pin_bypasses_ranking() -> None:
    router, _ = _router(
        _FakeProvider("local-1", endpoint_type="local_host"),
        _FakeProvider("net-1", endpoint_type="network"),
    )
    chosen = await router.select_model(_request(), {"provider_id": "net-1"})
    assert chosen.provider_id == "net-1"


async def test_pin_to_unregistered_raises_provider_not_found() -> None:
    """The M2 exception is reused, not duplicated."""
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(ProviderNotFoundError):
        await router.select_model(_request(), {"provider_id": "nope"})


async def test_pin_with_conflicting_endpoint_type_raises() -> None:
    """I6: an unsatisfiable assertion raises rather than silently returning nothing."""
    router, _ = _router(_FakeProvider("local-1", endpoint_type="local_host"))
    with pytest.raises(NoRoutableProviderError):
        await router.select_model(
            _request(), {"provider_id": "local-1", "endpoint_type": "cloud"}
        )


async def test_padded_pin_is_not_normalized() -> None:
    """Consistent with the registry's exact-match rule."""
    router, _ = _router(_FakeProvider("p1"))
    with pytest.raises(ProviderNotFoundError):
        await router.select_model(_request(), {"provider_id": " p1 "})


# --------------------------------------------------------------------------
# Single-read and identity consistency (I8, I13, I15)
# --------------------------------------------------------------------------


async def test_varying_metadata_cannot_cause_filter_return_divergence() -> None:
    """I13: whatever was filtered on is exactly what is returned."""
    router, _ = _router(_VaryingMetadataProvider("varying"))
    for _ in range(10):
        result = await router.select_candidates(_request(), {"endpoint_type": "local_host"})
        assert all(m.endpoint_type == "local_host" for m in result)


async def test_enumerated_id_that_no_longer_resolves_is_skipped() -> None:
    """A provider whose metadata renames it out from under its own registry key
    is simply unroutable — the router skips it rather than erroring."""
    router, registry = _router()
    registry.register(_LyingIdentityProvider(registered_as="real-key", claims_to_be="other"))
    assert await router.select_candidates(_request(), {}) == []
    # The registry key itself is untouched by the router.
    assert registry.get("real-key") is not None


async def test_provider_misreporting_identity_is_skipped_in_discovery() -> None:
    """I8: exercises the discovery-path identity-consistency branch directly —
    the enumerated id *does* resolve, but the resolved provider then reports a
    different identity, so its metadata must never be returned."""
    router, registry = _router()
    flipper = _IdentityFlipProvider(real_id="real-key", other_id="impostor")
    registry.register(flipper)
    result = await router.select_candidates(_request(), {})
    assert result == []
    assert flipper.reads >= 3  # proves the enumerate-then-resolve path was taken


async def test_pinned_provider_misreporting_identity_raises() -> None:
    router, registry = _router()
    registry.register(_LyingIdentityProvider(registered_as="real-key", claims_to_be="other"))
    with pytest.raises(ProviderNotRoutableError):
        await router.select_model(_request(), {"provider_id": "real-key"})


async def test_no_duplicate_candidates_when_providers_report_same_id() -> None:
    """I15: deduplication prevents one provider entering the list twice."""
    router, registry = _router(_FakeProvider("shared"))
    registry.register(_LyingIdentityProvider(registered_as="other-key", claims_to_be="shared"))
    result = await router.select_candidates(_request(), {})
    ids = [m.provider_id for m in result]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------
# Concurrency (I5)
# --------------------------------------------------------------------------


def test_concurrent_routing_is_consistent_and_lock_free() -> None:
    import asyncio

    registry = ProviderRegistry()
    registry.register(_FakeProvider("local-1", endpoint_type="local_host"))
    registry.register(_FakeProvider("net-1", endpoint_type="network"))
    router = ModelRouter(registry)

    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(20)

    def _route() -> None:
        barrier.wait()
        try:
            chosen = asyncio.run(router.select_model(_request(), {}))
            results.append(chosen.provider_id)
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [threading.Thread(target=_route) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert set(results) == {"local-1"}


def test_routing_during_registry_churn_never_leaks_provider_not_found() -> None:
    """Discovery must skip vanished providers, never surface them as errors."""
    import asyncio

    registry = ProviderRegistry()
    for index in range(30):
        registry.register(_FakeProvider(f"p{index}"))
    router = ModelRouter(registry)

    errors: list[Exception] = []
    stop = threading.Event()

    def _churn() -> None:
        index = 0
        while not stop.is_set():
            registry.unregister(f"p{index % 30}")
            # A concurrent re-registration of the same id is a benign race here;
            # the churn thread's job is only to keep the registry mutating.
            with contextlib.suppress(Exception):
                registry.register(_FakeProvider(f"p{index % 30}"))
            index += 1

    def _route() -> None:
        try:
            for _ in range(100):
                asyncio.run(router.select_candidates(_request(), {}))
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    churn_thread = threading.Thread(target=_churn)
    churn_thread.start()
    route_threads = [threading.Thread(target=_route) for _ in range(4)]
    for thread in route_threads:
        thread.start()
    for thread in route_threads:
        thread.join()
    stop.set()
    churn_thread.join()

    assert errors == []


# --------------------------------------------------------------------------
# Exception hierarchy
# --------------------------------------------------------------------------


def test_routing_exception_hierarchy() -> None:
    for exc_cls in (RoutingValidationError, NoRoutableProviderError, ProviderNotRoutableError):
        assert issubclass(exc_cls, RoutingError)
        assert issubclass(exc_cls, AIOrchestrationError)
        assert issubclass(exc_cls, KortexError)
        assert not issubclass(exc_cls, AIProviderError)
    assert issubclass(RoutingError, AIOrchestrationError)
    assert not issubclass(RoutingError, AIProviderError)


# --------------------------------------------------------------------------
# Security (I7)
# --------------------------------------------------------------------------


async def test_no_exception_message_leaks_url_or_secret_handle() -> None:
    """I7: across every raising path, provider secrets must never surface."""
    registry = ProviderRegistry()
    registry.register(_FakeProvider("cloud-secret", endpoint_type="cloud", with_secrets=True))
    registry.register(_metadata("meta-secret", with_secrets=True))
    registry.register(_LyingIdentityProvider(registered_as="liar", claims_to_be="someone-else"))
    router = ModelRouter(registry)

    raising_calls = [
        {},                                                     # cloud excluded -> NoRoutable
        {"provider_id": "meta-secret"},                          # metadata-only -> NotRoutable
        {"provider_id": "cloud-secret", "endpoint_type": "local_host"},  # mismatch -> NoRoutable
        {"provider_id": "absent"},                               # unknown -> NotFound
        {"provider_id": "liar"},                                 # identity mismatch -> NotRoutable
        {"model_id": "x"},                                       # rejected key
        {"bogus": SECRET_HANDLE},                                # unknown key
        {"endpoint_type": "not-a-real-endpoint"},                # invalid literal
    ]

    raised = 0
    for context in raising_calls:
        try:
            await router.select_model(_request(), context)
        except (RoutingError, ProviderNotFoundError) as exc:
            raised += 1
            message = str(exc)
            assert SECRET_URL not in message
            assert SECRET_HANDLE not in message
    assert raised == len(raising_calls)


# --------------------------------------------------------------------------
# Purity (I1)
# --------------------------------------------------------------------------


async def test_routing_never_executes_a_provider() -> None:
    provider = _FakeProvider("p1")
    router, _ = _router(provider)
    await router.select_model(_request(), {})
    await router.select_candidates(_request(), {})
    assert provider.generate_text_calls == 0
    assert provider.generate_embeddings_calls == 0


async def test_no_state_leaks_between_calls_with_different_contexts() -> None:
    """I5: the router is stateless — a permissive call must not widen a later
    restrictive one (which a cached candidate set or memoized context would)."""
    router, _ = _router(
        _FakeProvider("cloud-1", endpoint_type="cloud"),
        _FakeProvider("local-1", endpoint_type="local_host"),
    )
    permissive = await router.select_candidates(_request(), {"allow_cloud": True})
    assert [m.provider_id for m in permissive] == ["local-1", "cloud-1"]

    restrictive = await router.select_candidates(_request(), {})
    assert [m.provider_id for m in restrictive] == ["local-1"]

    # And back again — order of calls must not matter.
    assert len(await router.select_candidates(_request(), {"allow_cloud": True})) == 2


async def test_selection_creates_no_reservation() -> None:
    """I10: the result is an advisory snapshot — selecting a provider neither
    pins it in the registry nor guarantees it still exists afterwards."""
    router, registry = _router(_FakeProvider("p1"))
    chosen = await router.select_model(_request(), {})
    assert chosen.provider_id == "p1"

    # Selection granted no hold: the provider can still be removed immediately.
    assert registry.unregister("p1") is True
    with pytest.raises(ProviderNotFoundError):
        registry.get(chosen.provider_id)
    with pytest.raises(NoRoutableProviderError):
        await router.select_model(_request(), {})


def test_ai_package_imports_no_forbidden_dependency() -> None:
    """I9 (refined per M4 spec section 8.1): dependency direction is enforced
    **per module**, which is stricter than the original package-wide rule.

    `persistence.py` is the single designated infrastructure adapter and may
    import SQLAlchemy, the ORM base, and the Storage Engine's interfaces.
    Every other module keeps the original narrow allowlist. Authority
    boundaries — Security Engine, the Kernel, the DI container — plus
    Knowledge Engine remain forbidden **everywhere, without exception**.
    """
    import kortex.engines.ai as ai_package

    package_dir = pathlib.Path(ai_package.__file__).parent

    base_allowed = (
        "kortex.engines.ai",
        "kortex.core.exceptions",
        "kortex.core.base_engine",
    )
    adapter_allowed = (*base_allowed, "kortex.core.db", "kortex.engines.storage")
    adapter_module = "persistence.py"

    # Forbidden in every module, including the adapter. Security/Kernel/container
    # are authority boundaries; Knowledge must stay behind a port (M4 spec 8.3).
    forbidden_everywhere = (
        "kortex.engines.security",
        "kortex.core.container",
        "kortex.core.kernel",
        "kortex.engines.knowledge",
    )
    # Third-party infrastructure permitted only in the adapter.
    restricted_third_party = ("sqlalchemy",)

    offenders: list[str] = []
    for source_file in sorted(package_dir.glob("*.py")):
        is_adapter = source_file.name == adapter_module
        allowed_prefixes = adapter_allowed if is_adapter else base_allowed

        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module_names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                module_names.append(node.module)
            elif isinstance(node, ast.Import):
                module_names.extend(alias.name for alias in node.names)

            for module_name in module_names:
                if any(module_name.startswith(bad) for bad in forbidden_everywhere):
                    offenders.append(f"{source_file.name} -> {module_name} (forbidden everywhere)")
                    continue
                if not is_adapter and any(
                    module_name.startswith(bad) for bad in restricted_third_party
                ):
                    offenders.append(f"{source_file.name} -> {module_name} (adapter-only)")
                    continue
                if not module_name.startswith("kortex"):
                    continue
                if not any(module_name.startswith(good) for good in allowed_prefixes):
                    offenders.append(f"{source_file.name} -> {module_name}")

    assert offenders == []


async def test_routing_never_mutates_the_registry() -> None:
    router, registry = _router(
        _FakeProvider("a"), _FakeProvider("b", endpoint_type="cloud")
    )
    before = [m.provider_id for m in registry.list_providers()]
    await router.select_candidates(_request(), {})
    await router.select_candidates(_request(), {"allow_cloud": True})
    with pytest.raises(NoRoutableProviderError):
        await router.select_model(_request(), {"endpoint_type": "network"})
    assert [m.provider_id for m in registry.list_providers()] == before
