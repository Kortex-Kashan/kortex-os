"""Server-derived cloud-routing authorization (Phase B / B4.1, extended B5.1).

**Why this module exists.** Before B4, cloud egress was gated by a single
process-wide boolean (`AIEngineRuntimeConfig.enable_cloud_models`) that
production bootstrap left at `False`, and by a caller-supplied
`RoutingContext.allow_cloud`. That left exactly two possibilities, both
wrong: keep cloud unreachable for everyone, or flip one global flag and let
*any* caller push *any* tenant's data to a vendor. Neither is a tenant
decision, and the second makes the frontend the authorizer of cloud egress.

This module makes the decision **server-derived and per-tenant**:

    allow_cloud = tenant_has_an_enabled_credentialed_cloud_provider
                  AND NOT policy.strict_local_only

Both inputs are authoritative backend state that a caller cannot influence:
`ai_provider_configs` rows are written only through
`kortex.ai.provider.configure`, whose tenant comes from the verified
execution context; `strict_local_only` lives on the tenant's
`AIGovernancePolicy`. Nothing here reads a request field, a routing context,
or any other caller-supplied value except the tenant id — which callers do
not supply either (see `AIOrchestrationEngine.generate_response`, where
`request.tenant_id` is rebound to the verified principal's tenant before
routing, and `EngineAgentContextPort.build_step_context`, which stamps it
from the persisted task).

**This enforces two invariants that were already declared and never
enforced**, rather than inventing new policy:

* `AIGovernancePolicy.strict_local_only` — "Disallow external cloud
  providers regardless of request flags" (`governance.py`). It was defined,
  persisted, and round-tripped through capabilities, but no routing or
  generation path ever read it.
* `AIProviderConfig` — "`enabled` is a real gate, not a display flag: a
  disabled configuration must not resolve a credential and **must not
  route**." The credential half was enforced by
  `TenantCredentialResolver`; the routing half was not enforced anywhere.

**Fail-closed, deliberately.** Every unresolvable state answers "no cloud":
a blank tenant id, a store that raises, a registry that cannot be read, a
policy read that fails. `is_cloud_permitted` therefore never propagates an
exception — an authorization gate that raises would either break generation
outright or, worse, invite a caller to catch the error and proceed. The
denial is logged so an operator can see the difference between "denied by
policy" and "denied because the database was unreachable".

**A credential is required, not just `enabled=True`.** An enabled cloud
configuration with no `secret_handle` cannot serve a request: routing to it
guarantees a vendor 401 after the prompt has already been composed. Treating
it as "not available for cloud routing" is both the safer and the more
honest reading of "an enabled cloud provider".

**B5.1 extension: `resolve_tenant_preference`.** B4 answered only "may this
tenant reach cloud" (a bool). The agent/chat path (`agent.orchestrate`) needs
one more fact to make that permission *observable* rather than merely
*possible*: *which* cloud provider the tenant explicitly configured, so
`RouterLLMExecutionPort` can prefer it over ADR #001's local-first default
instead of a healthy Ollama silently outranking a tenant's own choice on
every request (`router.py`'s `_ENDPOINT_RANK` ranks `local_host` before
`cloud` unconditionally in discovery mode; nothing before B5.1 ever
overrode that with a tenant-specific preference).

**B5 correction: `is_cloud_permitted` and `resolve_tenant_preference` answer
two genuinely different questions and must not collapse into one.** The
first B5.1 pass made `is_cloud_permitted` a one-line wrapper over
`resolve_tenant_preference`, which was correct only by coincidence — for a
tenant with *at most one* qualifying cloud configuration, "cloud is
permitted" and "this is the specific provider" happen to be the same fact.
They stop agreeing the moment a tenant has **more than one** enabled,
credentialed cloud configuration (the schema has always permitted this;
nothing before B5 ever needed to distinguish the cases): cloud routing
should still be *permitted* (B4's original definition — at least one
qualifying configuration exists), but there is no longer a single,
unambiguous *preference* to pin to. Silently picking the first row would
make routing depend on `AIProviderConfigStore.list_for_tenant`'s `ORDER BY`
clause — a persistence-layer implementation detail with no tenant-facing
meaning — so `resolve_tenant_preference` instead answers `None` in that
case, deliberately falling back to the same local-first/cloud-fallback
`_discover` behavior B4 already had for every tenant, rather than
manufacturing a preference nobody actually expressed. Both methods now
share `_qualifying_cloud_configs` (every candidate) and `_policy_allows`
(the strict_local_only gate) so the fail-closed rules cannot drift apart
between them, without conflating "how many qualify" with "was exactly one
of them chosen".

**B5 correction: a configured `default_model` is verified against the
provider's live catalog, not merely its static allow-list.** A model
present in `AIProviderMetadata.supported_models` (a per-provider-instance,
KORTEX-curated constant — e.g. `SUPPORTED_OPENAI_MODELS`) proves only that
this KORTEX build knows how to route to a model with that name; it proves
nothing about whether *this tenant's own credential* can actually serve it
(model access varies by account, by API tier, by vendor-side deprecation).
`RouterLLMExecutionPort` therefore calls the provider's own
`discover_models(credential)` — the exact mechanism `kortex.ai.provider.test`
already uses for the identical question — before trusting a configured
`default_model` enough to pin to it. See that class's docstring for the
full mechanism and the deliberate tradeoff (one extra vendor round trip per
cloud-preference request; no caching, per the standing no-credential-
caching rule this module and `credentials.py` already share).
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kortex.engines.ai.models import AIProviderConfig

logger = logging.getLogger(__name__)

_CLOUD_ENDPOINT_TYPE = "cloud"


class ProviderConfigLister(Protocol):
    """The read surface this authority needs from the provider config store.

    A Protocol, not a concrete import, for the same reason
    `credentials.ProviderConfigReader` is one: this module stays testable
    with a plain fake and carries no persistence dependency. Deliberately
    narrower than `AIProviderConfigStore` — the authority can list one
    tenant's configurations and can do nothing else, so it cannot write,
    delete, or read across tenants even by mistake.
    """

    async def list_for_tenant(self, tenant_id: str) -> list[AIProviderConfig]: ...


class GovernancePolicyReader(Protocol):
    """The read surface this authority needs from governance.

    Satisfied by both `AIGovernanceStore` (returns `None` when the tenant
    has no policy row) and `AIGovernanceManager` (returns a defaulted
    policy). Typed to return `Any` because the only attribute read is
    `strict_local_only`, and duck-typing it keeps this module free of an
    import cycle with `governance.py`.
    """

    async def get_policy(self, tenant_id: str) -> Any | None: ...


class ProviderMetadataLister(Protocol):
    """The read surface this authority needs from the provider registry.

    Synchronous and I/O-free, matching `ProviderRegistry.list_providers`.
    Which providers are "cloud" is read from live registry metadata rather
    than a hardcoded id list, so registering a fourth cloud provider needs
    no change here — and a provider that stops reporting `endpoint_type ==
    "cloud"` stops counting, automatically.
    """

    def list_providers(self) -> list[Any]: ...


class TenantCloudRoutingAuthority:
    """The single authority on whether one tenant may route to a cloud provider.

    Stateless apart from its three injected readers: there is no cache. A
    cached "yes" would outlive the configuration that produced it, so a
    tenant who disables their provider or switches on `strict_local_only`
    would keep reaching the vendor until the entry expired. This mirrors
    `TenantCredentialResolver`'s no-cache rule and exists for the same
    reason.

    Every decision is one registry read plus at most two store reads, on a
    path that is about to make a network call to an LLM, so the cost is
    immaterial next to what it guards.
    """

    def __init__(
        self,
        registry: ProviderMetadataLister,
        provider_configs: ProviderConfigLister,
        policy_reader: GovernancePolicyReader,
    ) -> None:
        self._registry = registry
        self._provider_configs = provider_configs
        self._policy_reader = policy_reader

    def cloud_provider_ids(self) -> set[str]:
        """Ids of every registered provider reporting `endpoint_type == "cloud"`.

        Returns an empty set rather than raising if the registry cannot be
        read: the caller's next step on an empty set is to deny, which is
        the correct answer when the set of cloud providers is unknown.
        """
        try:
            return {
                metadata.provider_id
                for metadata in self._registry.list_providers()
                if getattr(metadata, "endpoint_type", None) == _CLOUD_ENDPOINT_TYPE
            }
        except Exception:
            logger.exception("Cloud routing denied: the provider registry could not be enumerated.")
            return set()

    def is_cloud_provider(self, provider_id: str) -> bool:
        """Whether `provider_id` is a registered cloud provider.

        Used to reject a caller-supplied pin at a cloud provider when that
        tenant is not authorized for cloud routing — `ModelRouter.
        _resolve_pinned` deliberately ignores `allow_cloud`, so a pin would
        otherwise be an unguarded path to the vendor.
        """
        return provider_id in self.cloud_provider_ids()

    async def _qualifying_cloud_configs(self, tenant_id: str | None) -> list[AIProviderConfig] | None:
        """Every enabled, credentialed cloud configuration for `tenant_id`, or `None` if unresolvable.

        `None` (unresolvable: blank tenant, or the config store raised) is
        deliberately distinct from `[]` (resolvable: the store answered,
        this tenant simply has no qualifying configuration) — both callers
        below treat an empty *or* unresolvable result as "no candidate", but
        `None` is what makes the failure mode observable in logs, matching
        every other fail-closed branch in this class.
        """
        if not tenant_id or not tenant_id.strip():
            # Not merely invalid input: an absent tenant means there is no
            # tenant whose configuration could authorize this, so there is
            # no authorization.
            logger.warning("Cloud routing denied: no tenant identity was available to authorize it.")
            return None

        cloud_ids = self.cloud_provider_ids()
        if not cloud_ids:
            return []

        try:
            configs = await self._provider_configs.list_for_tenant(tenant_id)
        except Exception:
            logger.exception(
                "Cloud routing denied for tenant '%s': provider configuration could not be read.",
                tenant_id,
            )
            return None

        return [
            config
            for config in configs
            if config.provider_id in cloud_ids and config.enabled and bool(config.secret_handle)
        ]

    async def _policy_allows_cloud(self, tenant_id: str | None) -> bool:
        """Whether `tenant_id`'s governance policy permits cloud routing.

        Isolated from `_qualifying_cloud_configs` because both
        `is_cloud_permitted` and `resolve_tenant_preference` need the exact
        same policy check applied to a different upstream result (any
        qualifying config vs. exactly one) — sharing this keeps
        `strict_local_only`'s fail-closed handling defined in one place.

        Takes `tenant_id: str | None` (rather than requiring callers to
        re-narrow it) purely so both call sites can pass the same value
        `_qualifying_cloud_configs` already validated as non-blank without
        a second, redundant guard; a `None` here is unreachable in
        practice (both callers only reach this after a non-empty
        qualifying list, which `_qualifying_cloud_configs` never returns
        for a blank tenant) but is handled explicitly rather than assumed.
        """
        if not tenant_id:
            logger.warning("Cloud routing denied: no tenant identity was available to authorize it.")
            return False
        try:
            policy = await self._policy_reader.get_policy(tenant_id)
        except Exception:
            logger.exception(
                "Cloud routing denied for tenant '%s': governance policy could not be read.",
                tenant_id,
            )
            return False

        if policy is None:
            # A tenant with no policy row is a resolved state, not an
            # unresolved one: `AIGovernancePolicy.strict_local_only`
            # defaults to False, and this tenant has already taken the
            # explicit, authenticated action of configuring and enabling a
            # credentialed cloud provider. Denying here would make the
            # feature unreachable until an unrelated policy row happened to
            # exist.
            return True

        strict_local_only = getattr(policy, "strict_local_only", None)
        if not isinstance(strict_local_only, bool):
            # A policy object that cannot state this field is an
            # unresolved state, not a permissive one.
            logger.warning(
                "Cloud routing denied for tenant '%s': governance policy did not report strict_local_only.",
                tenant_id,
            )
            return False

        if strict_local_only:
            logger.info(
                "Cloud routing denied for tenant '%s': strict_local_only is enabled on its governance policy.",
                tenant_id,
            )
            return False

        return True

    async def is_cloud_permitted(self, tenant_id: str | None) -> bool:
        """Whether `tenant_id` may route to a cloud provider right now.

        Never raises. Returns `False` for every state it cannot positively
        confirm as permitted, and logs the reason so "denied by policy" is
        distinguishable from "denied because state was unavailable".

        `True` whenever **at least one** enabled, credentialed cloud
        configuration exists and policy allows it — B4's original
        definition, independent of how many configurations qualify. This is
        deliberately *not* derived from `resolve_tenant_preference`: a
        tenant with two or three qualifying cloud configurations is still
        permitted to reach cloud (via ordinary `_discover`-mode ranking/
        fallback) even though B5 correction makes such a tenant have no
        single, unambiguous *preference* to pin to — see that method.
        """
        qualifying = await self._qualifying_cloud_configs(tenant_id)
        if not qualifying:
            return False
        return await self._policy_allows_cloud(tenant_id)

    async def resolve_tenant_preference(self, tenant_id: str | None) -> AIProviderConfig | None:
        """The tenant's explicit, unambiguous cloud-provider preference, or `None` (Phase B / B5.1).

        Returns the tenant's `AIProviderConfig` **only when exactly one**
        enabled, credentialed cloud configuration qualifies and policy
        allows cloud routing. Every other state — zero qualifying
        configurations, an unresolvable config or policy store,
        `strict_local_only=True` (an **absolute** deny — no configured
        preference overrides it), and, since the B5 correction, **more than
        one** qualifying configuration — resolves to `None`, never to a
        guessed or first-row fallback.

        The multi-configuration case is not an error: it means the tenant
        has not expressed an unambiguous single preference, so this method
        says so honestly (`None`) rather than picking one by an accident of
        `AIProviderConfigStore.list_for_tenant`'s SQL ordering. `is_cloud_
        permitted` is unaffected — cloud stays reachable via ordinary
        discovery/fallback for such a tenant; only the *pin* is withheld.

        Never raises, for the identical reason `is_cloud_permitted` never
        raises: an unresolved state must read as "no preference", not as an
        exception a caller might mishandle into a false positive.
        """
        qualifying = await self._qualifying_cloud_configs(tenant_id)
        if not qualifying or len(qualifying) != 1:
            return None

        if not await self._policy_allows_cloud(tenant_id):
            return None

        return qualifying[0]


__all__ = [
    "GovernancePolicyReader",
    "ProviderConfigLister",
    "ProviderMetadataLister",
    "TenantCloudRoutingAuthority",
]
