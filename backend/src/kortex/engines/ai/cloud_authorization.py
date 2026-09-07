"""Server-derived cloud-routing authorization (Phase B / B4.1).

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

    async def is_cloud_permitted(self, tenant_id: str | None) -> bool:
        """Whether `tenant_id` may route to a cloud provider right now.

        Never raises. Returns `False` for every state it cannot positively
        confirm as permitted, and logs the reason so "denied by policy" is
        distinguishable from "denied because state was unavailable".
        """
        if not tenant_id or not tenant_id.strip():
            # Not merely invalid input: an absent tenant means there is no
            # tenant whose configuration could authorize this, so there is
            # no authorization.
            logger.warning("Cloud routing denied: no tenant identity was available to authorize it.")
            return False

        cloud_ids = self.cloud_provider_ids()
        if not cloud_ids:
            return False

        try:
            configs = await self._provider_configs.list_for_tenant(tenant_id)
        except Exception:
            logger.exception(
                "Cloud routing denied for tenant '%s': provider configuration could not be read.",
                tenant_id,
            )
            return False

        has_enabled_cloud_provider = any(
            config.provider_id in cloud_ids and config.enabled and bool(config.secret_handle) for config in configs
        )
        if not has_enabled_cloud_provider:
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


__all__ = [
    "GovernancePolicyReader",
    "ProviderConfigLister",
    "ProviderMetadataLister",
    "TenantCloudRoutingAuthority",
]
