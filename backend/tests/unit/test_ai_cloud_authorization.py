"""Trusted, server-derived cloud-routing authorization (Phase B / B4.1).

The rule under test:

    allow_cloud = tenant_has_an_enabled_credentialed_cloud_provider
                  AND NOT policy.strict_local_only

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
from kortex.engines.ai.engine import AIOrchestrationEngine, RouterLLMExecutionPort
from kortex.engines.ai.exceptions import (
    CloudRoutingNotPermittedError,
    NoRoutableProviderError,
    ProviderFallbackExhaustedError,
)
from kortex.engines.ai.governance import AIGovernanceManager, AIGovernancePolicy
from kortex.engines.ai.models import (
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
        )

    async def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def health_check(self) -> bool:
        return True


class _FakeConfigStore:
    """Provider configurations, per tenant, with an optional read failure.

    `list_for_tenant` filters by tenant the way the real
    `AIProviderConfigStore` filters in SQL — a test that returned every
    tenant's rows could not distinguish real isolation from accidental
    isolation.
    """

    def __init__(self, configs: dict[str, list[AIProviderConfig]] | None = None, raises: bool = False) -> None:
        self._configs = configs or {}
        self._raises = raises

    async def list_for_tenant(self, tenant_id: str) -> list[AIProviderConfig]:
        if self._raises:
            raise RuntimeError("provider configuration store is unavailable")
        return list(self._configs.get(tenant_id, []))


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
