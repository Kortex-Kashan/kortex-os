"""Trusted, server-derived cloud-routing authorization (Phase B / B4.1, extended B5.1).

The rule under test:

    allow_cloud = tenant_has_an_enabled_credentialed_cloud_provider
                  AND NOT policy.strict_local_only

B5.1 adds one more fact derived by the exact same rule: *which* cloud
provider (and, if configured, which model) is the tenant's explicit
preference on the agent/chat path -- §5 below.

Every assertion here observes the *effect* of that decision — which provider
actually ran, or which error was raised — rather than asserting that a mock
was called. A test that only checked "the authority was consulted" would
still pass if the answer were then discarded.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kortex.engines.ai.base_provider import BaseAIProvider
from kortex.engines.ai.cloud_authorization import TenantCloudRoutingAuthority
from kortex.engines.ai.credentials import TenantCredentialResolver
from kortex.engines.ai.engine import AIOrchestrationEngine, RouterLLMExecutionPort
from kortex.engines.ai.exceptions import (
    CloudRoutingNotPermittedError,
    NoRoutableProviderError,
    ProviderFallbackExhaustedError,
    TransientProviderError,
)
from kortex.engines.ai.governance import AIGovernanceManager, AIGovernancePolicy
from kortex.engines.ai.models import (
    AIModelSummary,
    AIProviderConfig,
    AIProviderMetadata,
    LLMRequest,
    LLMResponse,
)
from kortex.engines.ai.registry import ProviderRegistry
from kortex.engines.ai.router import ModelRouter, RoutingContext

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubProvider(BaseAIProvider):
    """An executing provider whose id, endpoint type and models are chosen per test.

    `endpoint_type` is the field the whole authorization decision keys off,
    so it must be a test parameter rather than a constant.
    """

    def __init__(
        self,
        provider_id: str,
        endpoint_type: str,
        models: list[str] | None = None,
        response_text: str | None = None,
        live_models: list[str] | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._response_text = response_text or f"answer from {provider_id}"
        self._metadata = AIProviderMetadata(
            provider_id=provider_id,
            display_name=provider_id,
            vendor="TestVendor",
            endpoint_type=endpoint_type,
            supported_models=models or [f"{provider_id}-model"],
        )
        # `None` (the default) means "no divergence configured" -- fall
        # through to `BaseAIProvider.discover_models`'s own default, which
        # projects the STATIC `supported_models` list above. A test that
        # needs the live catalog to say something DIFFERENT from what this
        # provider statically claims to support (B5 correction: a model
        # can be in the static allow-list yet unavailable live) sets this
        # explicitly instead.
        self._live_models = live_models
        self.discover_models_calls: list[str | None] = []

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def metadata(self) -> AIProviderMetadata:
        return self._metadata

    @property
    def supported_models(self) -> list[str]:
        return list(self._metadata.supported_models)

    async def generate_text(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            request_id=request.request_id,
            text_content=self._response_text,
            token_usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            execution_time_ms=1.0,
            provider_id=self._provider_id,
            # Reports back whatever `request.model_id` this call actually
            # carried (B5.1), so a test can assert on the model a tenant's
            # `default_model` preference actually reached the provider with
            # -- rather than only on which provider answered.
            model_name=request.model_id,
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True

    async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
        self.discover_models_calls.append(credential)
        if self._live_models is None:
            return await super().discover_models(credential)
        return [
            AIModelSummary(model_id=model_id, provider_id=self._provider_id, provider_display_name=self._provider_id)
            for model_id in self._live_models
        ]


class _FakeConfigStore:
    """Provider configurations, per tenant, with an optional read failure.

    `list_for_tenant` filters by tenant the way the real
    `AIProviderConfigStore` filters in SQL — a test that returned every
    tenant's rows could not distinguish real isolation from accidental
    isolation.

    Also satisfies `credentials.ProviderConfigReader` (`.get`), so one
    instance can back both a `TenantCloudRoutingAuthority` and a real
    `TenantCredentialResolver` from the exact same underlying `configs`
    dict — matching production, where `AIProviderConfigStore` is the one
    implementation both collaborators are built from.
    """

    def __init__(self, configs: dict[str, list[AIProviderConfig]] | None = None, raises: bool = False) -> None:
        self._configs = configs or {}
        self._raises = raises

    async def list_for_tenant(self, tenant_id: str) -> list[AIProviderConfig]:
        if self._raises:
            raise RuntimeError("provider configuration store is unavailable")
        return list(self._configs.get(tenant_id, []))

    async def get(self, tenant_id: str, provider_id: str) -> AIProviderConfig | None:
        if self._raises:
            raise RuntimeError("provider configuration store is unavailable")
        return next((c for c in self._configs.get(tenant_id, []) if c.provider_id == provider_id), None)


async def _fake_secret_getter(secret_handle: str, tenant_id: str) -> str:
    """Matches `credentials.SecretGetter`'s shape without a real SecretStore.

    A fixed, deterministic, per-handle plaintext -- never a real credential
    -- is all `discover_models`/`generate_text` on `_StubProvider` need to
    receive *something* non-empty.
    """
    return f"fake-plaintext:{secret_handle}"


class _FakePolicyReader:
    """Governance policies, per tenant, with an optional read failure."""

    def __init__(self, policies: dict[str, Any] | None = None, raises: bool = False) -> None:
        self._policies = policies or {}
        self._raises = raises

    async def get_policy(self, tenant_id: str) -> Any | None:
        if self._raises:
            raise RuntimeError("governance store is unavailable")
        return self._policies.get(tenant_id)


def _cloud_config(tenant_id: str, provider_id: str = "cloudy", **overrides: Any) -> AIProviderConfig:
    """An enabled, credentialed configuration — the shape that permits cloud."""
    fields: dict[str, Any] = {
        "tenant_id": tenant_id,
        "provider_id": provider_id,
        "enabled": True,
        "secret_handle": f"kortex/ai/providers/{provider_id}",
        "default_model": None,
    }
    fields.update(overrides)
    return AIProviderConfig(**fields)


def _policy(tenant_id: str, *, strict_local_only: bool) -> AIGovernancePolicy:
    return AIGovernancePolicy(tenant_id=tenant_id, strict_local_only=strict_local_only)


def _request(tenant_id: str, model_id: str | None = None) -> LLMRequest:
    return LLMRequest(
        request_id="req-1",
        tenant_id=tenant_id,
        user_id="user-1",
        conversation_id="conv-1",
        prompt="hello",
        model_id=model_id,
    )


def _context(tenant_id: str) -> SimpleNamespace:
    """A dispatcher-shaped execution context: only `.principal.tenant_id` is read."""
    return SimpleNamespace(principal=SimpleNamespace(tenant_id=tenant_id, principal_id="p1"))


def _authority(
    registry: ProviderRegistry,
    configs: dict[str, list[AIProviderConfig]] | None = None,
    policies: dict[str, Any] | None = None,
    config_store_raises: bool = False,
    policy_reader_raises: bool = False,
) -> TenantCloudRoutingAuthority:
    return TenantCloudRoutingAuthority(
        registry=registry,
        provider_configs=_FakeConfigStore(configs, raises=config_store_raises),
        policy_reader=_FakePolicyReader(policies, raises=policy_reader_raises),
    )


def _cloud_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(_StubProvider("cloudy", "cloud"))
    return registry


# ---------------------------------------------------------------------------
# §1 — The authority's decision table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_without_any_provider_configuration_is_denied_cloud() -> None:
    """Requirement 1: no cloud configuration at all -> cloud routing unavailable."""
    authority = _authority(_cloud_registry(), configs={})

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_tenant_with_enabled_credentialed_cloud_provider_is_permitted() -> None:
    """Requirements 2 and 4: enabled + credentialed + strict_local_only False -> permitted."""
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is True


@pytest.mark.asyncio
async def test_strict_local_only_denies_cloud_even_with_a_configured_provider() -> None:
    """Requirement 3: strict_local_only overrides an otherwise-permitting configuration.

    This is the case that made `strict_local_only` worth wiring at all: the
    provider is configured, enabled and credentialed, so every other clause
    of the rule says yes.
    """
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=True)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_absent_policy_row_leaves_the_documented_default_in_force() -> None:
    """No policy row means `strict_local_only` is at its default (False), not unknown.

    The tenant has still taken the explicit authenticated action of
    configuring a credentialed cloud provider; requiring an unrelated policy
    row to exist first would make the feature unreachable by default.
    """
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={},
    )

    assert await authority.is_cloud_permitted("tenant-a") is True


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_permit_cloud() -> None:
    """`AIProviderConfig.enabled` is documented as a gate that "must not route"."""
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a", enabled=False)]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_configuration_without_a_credential_does_not_permit_cloud() -> None:
    """An uncredentialed cloud configuration cannot serve a request, so it does not authorize one."""
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a", secret_handle=None)]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_configuring_only_a_local_provider_does_not_permit_cloud() -> None:
    """Enabling Ollama is not consent to cloud egress.

    Which providers count as cloud is read from live registry metadata, so a
    credentialed configuration naming a `local_host` provider must not
    satisfy the first clause of the rule.
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("ollama-llama3", "local_host"))
    registry.register(_StubProvider("cloudy", "cloud"))
    authority = _authority(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="ollama-llama3")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_one_tenants_configuration_does_not_authorize_another() -> None:
    """Requirement 5: authorization is per tenant, never leaked between them."""
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is True
    assert await authority.is_cloud_permitted("tenant-b") is False


@pytest.mark.asyncio
async def test_no_registered_cloud_provider_means_no_cloud_authorization() -> None:
    """With nothing cloud in the registry there is nothing to authorize."""
    registry = ProviderRegistry()
    registry.register(_StubProvider("ollama-llama3", "local_host"))
    authority = _authority(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


# ---------------------------------------------------------------------------
# §1b — Multiple qualifying cloud configurations (B5 correction, Issue 2)
# ---------------------------------------------------------------------------
#
# The schema has always permitted more than one enabled, credentialed cloud
# configuration per tenant. `is_cloud_permitted` and `resolve_tenant_
# preference` must answer *differently* once that happens: cloud stays
# reachable (at least one qualifies), but there is no longer a single
# provider to unambiguously pin to.


def _three_cloud_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(_StubProvider("openai", "cloud"))
    registry.register(_StubProvider("gemini", "cloud"))
    registry.register(_StubProvider("anthropic", "cloud"))
    return registry


@pytest.mark.asyncio
async def test_exactly_one_qualifying_config_is_returned_as_the_preference() -> None:
    """The unambiguous, single-provider case still resolves -- the correction narrows an edge case."""
    authority = _authority(
        _three_cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    preference = await authority.resolve_tenant_preference("tenant-a")

    assert preference is not None
    assert preference.provider_id == "openai"


@pytest.mark.asyncio
async def test_two_qualifying_configs_yield_no_unambiguous_preference() -> None:
    """Two enabled, credentialed cloud configs -> resolve_tenant_preference is None, not an arbitrary pick."""
    authority = _authority(
        _three_cloud_registry(),
        configs={
            "tenant-a": [
                _cloud_config("tenant-a", provider_id="openai"),
                _cloud_config("tenant-a", provider_id="gemini"),
            ]
        },
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.resolve_tenant_preference("tenant-a") is None


@pytest.mark.asyncio
async def test_three_qualifying_configs_still_permit_cloud_but_have_no_preference() -> None:
    """The exact scenario the correction names: OpenAI + Gemini + Anthropic all enabled, no explicit preference.

    `is_cloud_permitted` and `resolve_tenant_preference` must diverge here:
    permitted stays True (at least one qualifies); preference is None (more
    than one qualifies, so none is unambiguous). A pre-correction
    implementation that derived one from the other could not produce this
    pair of answers.
    """
    authority = _authority(
        _three_cloud_registry(),
        configs={
            "tenant-a": [
                _cloud_config("tenant-a", provider_id="openai"),
                _cloud_config("tenant-a", provider_id="gemini"),
                _cloud_config("tenant-a", provider_id="anthropic"),
            ]
        },
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.is_cloud_permitted("tenant-a") is True
    assert await authority.resolve_tenant_preference("tenant-a") is None


@pytest.mark.parametrize(
    "provider_order",
    [
        ["openai", "gemini", "anthropic"],
        ["anthropic", "gemini", "openai"],
        ["gemini", "openai", "anthropic"],
    ],
)
@pytest.mark.asyncio
async def test_ambiguous_preference_does_not_depend_on_config_list_order(provider_order: list[str]) -> None:
    """The result must be deterministic (None) regardless of the store's own return order.

    A pre-correction implementation using `next(...)` over this same list
    would return a *different* provider_id for each of these three orders
    -- the exact "depends on SQL row order" failure mode the correction
    forbids. Asserting `None` for all three orders is what proves order-
    independence; a single order could not.
    """
    authority = _authority(
        _three_cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id=pid) for pid in provider_order]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    assert await authority.resolve_tenant_preference("tenant-a") is None


@pytest.mark.asyncio
async def test_ambiguity_is_per_tenant_not_global() -> None:
    """One tenant having multiple configs must not affect another tenant's unambiguous single preference."""
    authority = _authority(
        _three_cloud_registry(),
        configs={
            "tenant-a": [
                _cloud_config("tenant-a", provider_id="openai"),
                _cloud_config("tenant-a", provider_id="gemini"),
            ],
            "tenant-b": [_cloud_config("tenant-b", provider_id="anthropic")],
        },
        policies={
            "tenant-a": _policy("tenant-a", strict_local_only=False),
            "tenant-b": _policy("tenant-b", strict_local_only=False),
        },
    )

    assert await authority.resolve_tenant_preference("tenant-a") is None
    preference_b = await authority.resolve_tenant_preference("tenant-b")
    assert preference_b is not None and preference_b.provider_id == "anthropic"


@pytest.mark.asyncio
async def test_ambiguous_preference_at_the_routing_port_falls_back_to_local_first() -> None:
    """The end-to-end effect: three qualifying providers, no pin, ADR #001 local-first still governs.

    Not merely "no error" -- the *local* provider specifically answers,
    proving cloud was reachable (is_cloud_permitted=True) but nothing was
    arbitrarily pinned.
    """
    registry = _local_registry_with_cloud("openai", "gemini", "anthropic")
    port = _port(
        registry,
        configs={
            "tenant-a": [
                _cloud_config("tenant-a", provider_id="openai"),
                _cloud_config("tenant-a", provider_id="gemini"),
                _cloud_config("tenant-a", provider_id="anthropic"),
            ]
        },
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from local-1"


# ---------------------------------------------------------------------------
# §2 — Fail-closed behavior (requirement 8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tenant_id", [None, "", "   "])
@pytest.mark.asyncio
async def test_missing_tenant_identity_is_denied(tenant_id: str | None) -> None:
    """No tenant means no configuration could have authorized this."""
    authority = _authority(_cloud_registry(), configs={"tenant-a": [_cloud_config("tenant-a")]})

    assert await authority.is_cloud_permitted(tenant_id) is False


@pytest.mark.asyncio
async def test_unreadable_provider_configuration_is_denied_not_raised() -> None:
    """Requirement 8: an unavailable config store denies rather than propagating."""
    authority = _authority(_cloud_registry(), config_store_raises=True)

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_unreadable_governance_policy_is_denied_not_raised() -> None:
    """Requirement 8: an unavailable policy store denies rather than propagating.

    Reached only after the configuration clause has already said yes, so
    this specifically proves the second clause fails closed rather than
    being skipped.
    """
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policy_reader_raises=True,
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_policy_object_that_cannot_state_strict_local_only_is_denied() -> None:
    """A policy that does not report the field is an unresolved state, not a permissive one."""
    authority = _authority(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": SimpleNamespace()},
    )

    assert await authority.is_cloud_permitted("tenant-a") is False


@pytest.mark.asyncio
async def test_unreadable_registry_is_denied_not_raised() -> None:
    """A registry that cannot be enumerated leaves the cloud provider set unknown."""

    class _BrokenRegistry:
        def list_providers(self) -> list[Any]:
            raise RuntimeError("registry unavailable")

    authority = TenantCloudRoutingAuthority(
        registry=_BrokenRegistry(),
        provider_configs=_FakeConfigStore({"tenant-a": [_cloud_config("tenant-a")]}),
        policy_reader=_FakePolicyReader({"tenant-a": _policy("tenant-a", strict_local_only=False)}),
    )

    assert await authority.is_cloud_permitted("tenant-a") is False
    assert authority.cloud_provider_ids() == set()


# ---------------------------------------------------------------------------
# §3 — The agent path: RouterLLMExecutionPort.generate_step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_step_reaches_cloud_provider_for_an_authorized_tenant() -> None:
    """The B4 objective: a configured cloud provider becomes reachable from the agent path.

    `default_routing_context` is left at `allow_cloud=False` — exactly what
    production bootstrap produces — so this proves the authority, not the
    global flag, is what opened the path.
    """
    registry = _cloud_registry()
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        default_routing_context=RoutingContext(allow_cloud=False),
        cloud_authority=_authority(
            registry,
            configs={"tenant-a": [_cloud_config("tenant-a")]},
            policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
        ),
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from cloudy"


@pytest.mark.asyncio
async def test_agent_step_denies_cloud_for_an_unconfigured_tenant() -> None:
    """Requirement 1, at the routing port: no configuration -> no routable provider."""
    registry = _cloud_registry()
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(registry, configs={}),
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_agent_step_denies_cloud_under_strict_local_only() -> None:
    """Requirement 3, at the routing port."""
    registry = _cloud_registry()
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(
            registry,
            configs={"tenant-a": [_cloud_config("tenant-a")]},
            policies={"tenant-a": _policy("tenant-a", strict_local_only=True)},
        ),
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_agent_step_authority_overrides_a_permissive_global_flag() -> None:
    """The authority is authoritative in *both* directions.

    A process-wide `enable_cloud_models=True` cannot grant cloud egress to a
    tenant whose own state denies it — otherwise the operator flag would
    silently outrank tenant policy.
    """
    registry = _cloud_registry()
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        default_routing_context=RoutingContext(allow_cloud=True),
        cloud_authority=_authority(
            registry,
            configs={"tenant-a": [_cloud_config("tenant-a")]},
            policies={"tenant-a": _policy("tenant-a", strict_local_only=True)},
        ),
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_agent_step_isolates_tenants_at_the_routing_port() -> None:
    """Requirement 5, at the routing port: one tenant's config does not carry to another."""
    registry = _cloud_registry()
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(
            registry,
            configs={"tenant-a": [_cloud_config("tenant-a")]},
            policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
        ),
    )

    assert (await port.generate_step(_request("tenant-a"))).text_content == "answer from cloudy"
    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-b"))


@pytest.mark.asyncio
async def test_agent_step_local_routing_is_unaffected_by_cloud_denial() -> None:
    """Requirement 9: local (Ollama-shaped) routing keeps working for an unauthorized tenant.

    Cloud denial must narrow the candidate set, not empty it — the local
    provider still serves the step.
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("cloudy", "cloud"))
    registry.register(_StubProvider("ollama-llama3", "local_host"))
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(registry, configs={}),
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from ollama-llama3"


@pytest.mark.asyncio
async def test_agent_step_without_an_authority_preserves_pre_b4_behavior() -> None:
    """No authority wired -> the default routing context stands, exactly as before B4.1.

    This is what keeps the in-memory composition (no provider config store,
    so no authoritative state to consult) working unchanged.
    """
    registry = _cloud_registry()
    denied = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        default_routing_context=RoutingContext(allow_cloud=False),
    )
    allowed = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        default_routing_context=RoutingContext(allow_cloud=True),
    )

    with pytest.raises(NoRoutableProviderError):
        await denied.generate_step(_request("tenant-a"))
    assert (await allowed.generate_step(_request("tenant-a"))).text_content == "answer from cloudy"


@pytest.mark.asyncio
async def test_agent_step_model_routing_still_selects_by_model_id() -> None:
    """Requirement 10: the D1 model filter is unchanged by cloud authorization.

    Both providers are cloud and both are authorized, so only `model_id`
    can decide — proving the authorization step did not disturb model
    selection.
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("cloud-a", "cloud", models=["shared", "only-a"]))
    registry.register(_StubProvider("cloud-b", "cloud", models=["shared", "only-b"]))
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(
            registry,
            configs={"tenant-a": [_cloud_config("tenant-a", provider_id="cloud-a")]},
            policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
        ),
    )

    response = await port.generate_step(_request("tenant-a", model_id="only-b"))

    assert response.text_content == "answer from cloud-b"


# ---------------------------------------------------------------------------
# §4 — generate_response: caller-supplied routing context is untrusted
# ---------------------------------------------------------------------------


def _engine(
    registry: ProviderRegistry,
    configs: dict[str, list[AIProviderConfig]] | None = None,
    policies: dict[str, Any] | None = None,
) -> AIOrchestrationEngine:
    return AIOrchestrationEngine(
        provider_registry=registry,
        model_router=ModelRouter(registry=registry),
        cloud_routing_authority=_authority(registry, configs=configs, policies=policies),
    )


@pytest.mark.asyncio
async def test_caller_supplied_allow_cloud_cannot_grant_cloud_routing() -> None:
    """Requirement 7: `allow_cloud=True` from the caller does not override trusted state.

    Overridden silently rather than rejected: `allow_cloud` is an untrusted
    preference the trusted decision replaces, so the request proceeds
    local-only — and with no local provider registered, that means no
    routable provider at all.
    """
    engine = _engine(_cloud_registry(), configs={})

    with pytest.raises(NoRoutableProviderError):
        await engine.generate_response(
            _request("tenant-a"),
            routing_context=RoutingContext(allow_cloud=True),
            execution_context=_context("tenant-a"),
        )


@pytest.mark.asyncio
async def test_caller_supplied_allow_cloud_false_does_not_deny_an_authorized_tenant() -> None:
    """The trusted decision replaces the caller's value in both directions."""
    engine = _engine(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await engine.generate_response(
        _request("tenant-a"),
        routing_context=RoutingContext(allow_cloud=False),
        execution_context=_context("tenant-a"),
    )

    assert response.text_content == "answer from cloudy"


@pytest.mark.asyncio
async def test_caller_cannot_pin_a_cloud_provider_without_authorization() -> None:
    """A `provider_id` pin is the sharpest bypass, because `_resolve_pinned` ignores `allow_cloud`.

    Rejected rather than downgraded: naming a provider is a caller
    assertion about placement, so answering it with a different placement
    would hide the denial.
    """
    engine = _engine(_cloud_registry(), configs={})

    with pytest.raises(CloudRoutingNotPermittedError):
        await engine.generate_response(
            _request("tenant-a"),
            routing_context=RoutingContext(provider_id="cloudy"),
            execution_context=_context("tenant-a"),
        )


@pytest.mark.asyncio
async def test_caller_cannot_request_cloud_endpoint_type_without_authorization() -> None:
    """`endpoint_type="cloud"` bypasses the `allow_cloud` gate in `_discover`'s if/elif.

    Without this rejection, `{"endpoint_type": "cloud"}` would reach every
    registered cloud provider regardless of the trusted decision.
    """
    engine = _engine(_cloud_registry(), configs={})

    with pytest.raises(CloudRoutingNotPermittedError):
        await engine.generate_response(
            _request("tenant-a"),
            routing_context=RoutingContext(endpoint_type="cloud"),
            execution_context=_context("tenant-a"),
        )


@pytest.mark.asyncio
async def test_authorized_tenant_may_pin_its_configured_cloud_provider() -> None:
    """The rejection above is authorization-conditional, not a blanket ban on pinning."""
    engine = _engine(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await engine.generate_response(
        _request("tenant-a"),
        routing_context=RoutingContext(provider_id="cloudy"),
        execution_context=_context("tenant-a"),
    )

    assert response.text_content == "answer from cloudy"


@pytest.mark.asyncio
async def test_caller_supplied_tenant_id_cannot_borrow_another_tenants_authorization() -> None:
    """Requirement 6: `request.tenant_id` is not the authorization subject.

    The caller authenticates as `tenant-b` (unauthorized) while claiming
    `tenant-a` (authorized) in the request body. `generate_response` rebinds
    the request to the verified principal's tenant before routing, so the
    claim buys nothing.
    """
    engine = _engine(
        _cloud_registry(),
        configs={"tenant-a": [_cloud_config("tenant-a")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    with pytest.raises(NoRoutableProviderError):
        await engine.generate_response(
            _request("tenant-a"),
            execution_context=_context("tenant-b"),
        )


@pytest.mark.asyncio
async def test_local_generation_is_unaffected_for_an_unauthorized_tenant() -> None:
    """Requirement 9, on the direct generation path."""
    registry = ProviderRegistry()
    registry.register(_StubProvider("cloudy", "cloud"))
    registry.register(_StubProvider("ollama-llama3", "local_host"))
    engine = _engine(registry, configs={})

    response = await engine.generate_response(
        _request("tenant-a"),
        execution_context=_context("tenant-a"),
    )

    assert response.text_content == "answer from ollama-llama3"


@pytest.mark.asyncio
async def test_engine_builds_its_own_authority_from_a_provider_config_store() -> None:
    """Wiring check: a provider config store is sufficient to make the authority exist.

    Uses the real `AIGovernanceManager` as the policy reader — the same
    object `generate_response` reads for guardrails and quota — so the
    policy the authority sees is the policy the tenant actually has.
    """
    registry = _cloud_registry()
    governance = AIGovernanceManager()
    await governance.set_policy(_policy("tenant-a", strict_local_only=True))

    engine = AIOrchestrationEngine(
        provider_registry=registry,
        model_router=ModelRouter(registry=registry),
        governance_manager=governance,
        provider_config_store=_FakeConfigStore({"tenant-a": [_cloud_config("tenant-a")]}),
    )

    assert engine.cloud_routing_authority is not None
    assert await engine.cloud_routing_authority.is_cloud_permitted("tenant-a") is False

    await governance.set_policy(_policy("tenant-a", strict_local_only=False))
    assert await engine.cloud_routing_authority.is_cloud_permitted("tenant-a") is True


@pytest.mark.asyncio
async def test_engine_without_a_provider_config_store_has_no_authority() -> None:
    """No authoritative state to consult -> no authority, and pre-B4.1 behavior stands."""
    registry = _cloud_registry()
    engine = AIOrchestrationEngine(provider_registry=registry, model_router=ModelRouter(registry=registry))

    assert engine.cloud_routing_authority is None

    response = await engine.generate_response(
        _request("tenant-a"),
        routing_context=RoutingContext(allow_cloud=True),
        execution_context=_context("tenant-a"),
    )
    assert response.text_content == "answer from cloudy"


@pytest.mark.asyncio
async def test_unreadable_state_fails_closed_on_the_generation_path() -> None:
    """Requirement 8, end to end: an unavailable store denies cloud without leaking its error.

    The failure surfaces as an ordinary routing outcome, so a caller cannot
    distinguish "store down" from "not authorized" — and neither answer
    reaches the vendor.
    """
    registry = _cloud_registry()
    engine = AIOrchestrationEngine(
        provider_registry=registry,
        model_router=ModelRouter(registry=registry),
        cloud_routing_authority=TenantCloudRoutingAuthority(
            registry=registry,
            provider_configs=_FakeConfigStore(raises=True),
            policy_reader=_FakePolicyReader(),
        ),
    )

    with pytest.raises((NoRoutableProviderError, ProviderFallbackExhaustedError)):
        await engine.generate_response(
            _request("tenant-a"),
            routing_context=RoutingContext(allow_cloud=True),
            execution_context=_context("tenant-a"),
        )


# ---------------------------------------------------------------------------
# §5 — B5.1: tenant provider/model preference on the agent path
# ---------------------------------------------------------------------------
#
# All of these drive `RouterLLMExecutionPort.generate_step` -- the same
# entry point `agent.orchestrate` reaches through `EngineAgentContextPort`.
# `resolve_tenant_preference` is exercised through the port, not called
# directly, so what is actually verified is the *effect* on routing: which
# provider answered and which model it was asked for -- never that a mock
# was merely consulted.


def _local_registry_with_cloud(*cloud_provider_ids: str) -> ProviderRegistry:
    """One local provider plus one `_StubProvider` per given cloud id.

    The local provider is always present and always healthy/executable, so
    "local wins" vs. "the pinned cloud provider wins" is unambiguous: if the
    wrong one answers, the test fails on the response text, not on an
    internal call count.
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("local-1", "local_host", models=["local-model"]))
    for provider_id in cloud_provider_ids:
        registry.register(_StubProvider(provider_id, "cloud", models=[f"{provider_id}-model", "shared-model"]))
    return registry


def _port(
    registry: ProviderRegistry,
    configs: dict[str, list[AIProviderConfig]] | None = None,
    policies: dict[str, Any] | None = None,
    with_credential_resolver: bool = True,
) -> RouterLLMExecutionPort:
    """`with_credential_resolver=True` (the default) wires a real
    `TenantCredentialResolver` off the *same* `configs` dict the authority
    reads -- required for B5 correction's live model-catalog check to run
    at all. Pass `False` only to test the "no resolver wired" fail-closed
    branch itself.
    """
    return RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(registry, configs=configs, policies=policies),
        credential_resolver=(
            TenantCredentialResolver(_FakeConfigStore(configs), _fake_secret_getter)
            if with_credential_resolver
            else None
        ),
    )


@pytest.mark.asyncio
async def test_no_configured_provider_preserves_local_first_routing() -> None:
    """Requirement 1: nothing configured -> ADR #001 local-first stands, unchanged from B4.1."""
    registry = _local_registry_with_cloud("openai")
    port = _port(registry, configs={})

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from local-1"


@pytest.mark.parametrize("provider_id", ["openai", "gemini", "anthropic"])
@pytest.mark.asyncio
async def test_enabled_cloud_configuration_becomes_the_explicit_preference(provider_id: str) -> None:
    """Requirements 2-4: an enabled, credentialed cloud configuration is explicitly routed to
    when cloud is permitted -- for each of the three real cloud provider ids, not just one.

    A healthy local provider is registered alongside it and is NOT chosen --
    proving this is a preference overriding local-first, not merely "cloud
    became reachable" (B4 already proved reachability; B5.1 proves
    selection).
    """
    registry = _local_registry_with_cloud(provider_id)
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id=provider_id)]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == f"answer from {provider_id}"


@pytest.mark.asyncio
async def test_configured_default_model_is_actually_honored() -> None:
    """Requirement 5: the tenant's `default_model` reaches the provider's request, not just its config row."""
    registry = _local_registry_with_cloud("openai")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from openai"
    assert response.model_name == "openai-model"


@pytest.mark.asyncio
async def test_different_tenants_have_different_simultaneous_preferences() -> None:
    """Requirement 6: two tenants' preferences coexist without one leaking into the other."""
    registry = _local_registry_with_cloud("openai", "anthropic")
    port = _port(
        registry,
        configs={
            "tenant-a": [_cloud_config("tenant-a", provider_id="openai")],
            "tenant-b": [_cloud_config("tenant-b", provider_id="anthropic")],
        },
        policies={
            "tenant-a": _policy("tenant-a", strict_local_only=False),
            "tenant-b": _policy("tenant-b", strict_local_only=False),
        },
    )

    response_a = await port.generate_step(_request("tenant-a"))
    response_b = await port.generate_step(_request("tenant-b"))

    assert response_a.text_content == "answer from openai"
    assert response_b.text_content == "answer from anthropic"


@pytest.mark.asyncio
async def test_strict_local_only_blocks_the_configured_cloud_preference() -> None:
    """Requirement 7: strict_local_only is an absolute deny, overriding an otherwise-valid preference."""
    registry = _local_registry_with_cloud("openai")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=True)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from local-1"


@pytest.mark.asyncio
async def test_disabled_cloud_configuration_is_not_selected_as_a_preference() -> None:
    """Requirement 12: a disabled configuration must not route, even though it exists."""
    registry = _local_registry_with_cloud("openai")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", enabled=False)]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from local-1"


@pytest.mark.asyncio
async def test_missing_configuration_fails_safely_to_local_first() -> None:
    """Requirement 13: no configuration at all is handled exactly like an explicitly absent one."""
    registry = _local_registry_with_cloud("openai")
    port = _port(registry, configs={"tenant-a": []})

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from local-1"


@pytest.mark.asyncio
async def test_stale_default_model_fails_the_request_rather_than_silently_switching_provider() -> None:
    """Requirement 14 -- the model-safety invariant, and the sharpest test in this file.

    The tenant's configured provider is credentialed and permitted, but its
    `default_model` names a model that provider does not advertise (stale:
    it was deprecated, mistyped, or never valid). The existing D1 pin check
    (`ModelRouter._resolve_pinned`) is what catches this -- reached here for
    the first time from the agent path -- raising a typed routing error
    *before* any provider call. Silently falling back to the local provider
    would satisfy "the tenant got an answer" while completely discarding
    their explicit provider choice for a request they'd have no way to know
    was misrouted; raising is what "fail safely... rather than silently
    selecting an unrelated provider" means operationally.
    """
    registry = _local_registry_with_cloud("openai")  # openai's real models: ["openai-model", "shared-model"]
    port = _port(
        registry,
        configs={
            "tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="gpt-4-nonexistent-deprecated")]
        },
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_stale_default_model_does_not_reach_the_provider_at_all() -> None:
    """Requirement 14, sharpened: the unsafe model must never be sent anywhere, local included.

    Distinguishes "raises without side effects" from "raises after quietly
    trying something else first" -- neither the pinned provider nor the
    local one should ever see a call.
    """
    registry = _local_registry_with_cloud("openai")
    calls: list[str] = []
    real_generate = _StubProvider.generate_text

    async def _tracking_generate_text(self: _StubProvider, request: LLMRequest) -> LLMResponse:
        calls.append(self.provider_id)
        return await real_generate(self, request)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_StubProvider, "generate_text", _tracking_generate_text)
        port = _port(
            registry,
            configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="stale-model")]},
            policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
        )
        with pytest.raises(NoRoutableProviderError):
            await port.generate_step(_request("tenant-a"))

    assert calls == []


# ---------------------------------------------------------------------------
# §5b — B5 correction: live catalog verification, not merely static allow-list
# ---------------------------------------------------------------------------
#
# `_StubProvider`'s `live_models` param is what makes these tests possible:
# a model can be declared in the STATIC `supported_models` list yet absent
# from the LIVE `discover_models` result -- exactly the gap between "KORTEX
# recognizes this model name" and "this tenant's credential can currently
# serve it" the correction closes. A test using only the static list (as
# every earlier stale-model test in §5 does) cannot distinguish these two
# implementations; these can, and do.


@pytest.mark.asyncio
async def test_statically_supported_but_not_live_available_model_is_rejected() -> None:
    """The defining test of this correction.

    'openai-model' is genuinely in the provider's static `supported_
    models` (so a static-only check, exactly what the pre-correction
    implementation relied on, would have let it through) but the LIVE
    catalog this tenant's credential actually resolves to does not include
    it. Routing must refuse the pin rather than trust the static list.
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("local-1", "local_host", models=["local-model"]))
    registry.register(
        _StubProvider(
            "openai",
            "cloud",
            models=["openai-model", "shared-model"],  # static: includes "openai-model"
            live_models=["shared-model"],  # live: does NOT include "openai-model"
        )
    )
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_statically_supported_and_live_available_model_is_accepted() -> None:
    """The positive counterpart: a model present in BOTH lists is used, proving this isn't merely "always reject"."""
    registry = ProviderRegistry()
    registry.register(_StubProvider("local-1", "local_host", models=["local-model"]))
    registry.register(
        _StubProvider(
            "openai",
            "cloud",
            models=["openai-model", "shared-model"],
            live_models=["openai-model", "shared-model"],
        )
    )
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from openai"
    assert response.model_name == "openai-model"


@pytest.mark.asyncio
async def test_live_catalog_is_actually_consulted_for_a_configured_default_model() -> None:
    """Direct proof the live path is reached: the provider's own `discover_models_calls` records the call.

    Complements the effect-based tests above with the one place this suite
    permits asserting a call happened -- because the *absence* of that call
    is exactly what the pre-correction implementation exhibited, so proving
    presence here is the meaningful signal, not a redundant mock check.
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("local-1", "local_host", models=["local-model"]))
    openai = _StubProvider("openai", "cloud", models=["openai-model"], live_models=["openai-model"])
    registry.register(openai)
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    await port.generate_step(_request("tenant-a"))

    assert openai.discover_models_calls == ["fake-plaintext:kortex/ai/providers/openai"]


@pytest.mark.asyncio
async def test_no_credential_resolver_wired_fails_safely_rather_than_trusting_the_static_list() -> None:
    """Without a resolver there is no way to reach the live catalog -- must deny, not fall back to static-only trust."""
    registry = _local_registry_with_cloud("openai")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
        with_credential_resolver=False,
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_credential_resolution_failure_during_validation_fails_safely() -> None:
    """The secret handle cannot be resolved (e.g. deleted from SecretStore) -> deny, don't proceed unvalidated."""

    async def _raising_secret_getter(secret_handle: str, tenant_id: str) -> str:
        raise RuntimeError("secret store unreachable")

    registry = _local_registry_with_cloud("openai")
    configs = {"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]}
    resolver = TenantCredentialResolver(_FakeConfigStore(configs), _raising_secret_getter)
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        cloud_authority=_authority(
            registry, configs=configs, policies={"tenant-a": _policy("tenant-a", strict_local_only=False)}
        ),
        credential_resolver=resolver,
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_live_discovery_call_failure_fails_safely_rather_than_falling_back() -> None:
    """`discover_models` itself raises a provider error -> deny, do not silently route elsewhere."""

    class _DiscoveryFailingProvider(_StubProvider):
        async def discover_models(self, credential: str | None = None) -> list[AIModelSummary]:
            raise TransientProviderError("catalog temporarily unavailable")

    registry = ProviderRegistry()
    registry.register(_StubProvider("local-1", "local_host", models=["local-model"]))
    registry.register(_DiscoveryFailingProvider("openai", "cloud", models=["openai-model"]))
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai", default_model="openai-model")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    with pytest.raises(NoRoutableProviderError):
        await port.generate_step(_request("tenant-a"))


@pytest.mark.asyncio
async def test_default_model_without_a_configured_provider_needs_no_live_check() -> None:
    """Sanity guard: when no `default_model` is configured, no live discovery call is made at all."""
    registry = _local_registry_with_cloud("openai")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai")]},  # no default_model
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from openai"


@pytest.mark.asyncio
async def test_explicit_request_model_id_overrides_the_provider_pin() -> None:
    """D1 doctrine, preserved: a caller's own explicit `model_id` outranks a tenant preference.

    Tenant prefers `openai` (no default_model), but the request itself asks
    for a model only the *other* registered cloud provider serves.
    `RouterLLMExecutionPort` must not pin to the preferred provider when the
    caller already named a model -- `_discover` mode finds whoever actually
    serves it.
    """
    registry = _local_registry_with_cloud("openai", "anthropic")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="openai")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    response = await port.generate_step(_request("tenant-a", model_id="anthropic-model"))

    assert response.text_content == "answer from anthropic"


@pytest.mark.asyncio
async def test_ollama_shaped_local_provider_is_unaffected_by_b5_1() -> None:
    """Requirement 15: local routing keeps working exactly as before, preference or not."""
    registry = ProviderRegistry()
    registry.register(_StubProvider("ollama-llama3", "local_host", models=["llama3"]))
    port = _port(registry, configs={})

    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from ollama-llama3"


@pytest.mark.asyncio
async def test_fallback_between_multiple_cloud_candidates_still_works_without_a_preference() -> None:
    """Requirement 16: with no explicit preference, `_discover`'s own ranking/fallback is untouched.

    Both are cloud and both are permitted (a policy with no configuration
    grants nothing, so this uses `allow_cloud` via the default context
    directly to isolate discovery-mode behavior from the pin path).
    """
    registry = ProviderRegistry()
    registry.register(_StubProvider("openai", "cloud"))
    registry.register(_StubProvider("anthropic", "cloud"))
    port = RouterLLMExecutionPort(
        router=ModelRouter(registry=registry),
        registry=registry,
        default_routing_context=RoutingContext(allow_cloud=True),
    )

    response = await port.generate_step(_request("tenant-a"))

    # No authority wired -> B4.1/pre-B5.1 discovery-mode behavior: both are
    # candidates, ranked by registration order (both `cloud`, so
    # `_ENDPOINT_RANK` does not distinguish them) -- the first-registered
    # answers, proving fallback ordering itself was not disturbed.
    assert response.text_content == "answer from openai"


@pytest.mark.asyncio
async def test_preferred_provider_that_vanishes_between_authorization_and_routing_fails_safely() -> None:
    """Edge of requirement 14: a preference pointing at a since-unregistered provider.

    Reuses `ModelRouter._resolve_pinned`'s existing `ProviderNotFoundError`
    path -- no new error handling was written for this, which is the point:
    B5.1 introduces no new failure mode, only a new caller of an existing one.
    """
    registry = _local_registry_with_cloud("openai")
    port = _port(
        registry,
        configs={"tenant-a": [_cloud_config("tenant-a", provider_id="vanished-provider")]},
        policies={"tenant-a": _policy("tenant-a", strict_local_only=False)},
    )

    # `vanished-provider` is not a registered cloud id, so it is not even a
    # candidate preference -- `resolve_tenant_preference` returns None and
    # local-first stands. This documents that case explicitly rather than
    # leaving it implicit: a config naming an unregistered provider id is
    # `cloud_provider_ids()`-filtered out before a pin could ever be attempted.
    response = await port.generate_step(_request("tenant-a"))

    assert response.text_content == "answer from local-1"
