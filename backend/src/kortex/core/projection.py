"""
KORTEX Capability Projection Engine (Milestone F6).

Implements a stateless, read-only tenant-scoped capability projection computed as:
    Global Capability Existence (CapabilityRegistry)
                     ∩
    Tenant Authorization (SecurityEngine)
                     =
    TENANT-SCOPED CAPABILITY PROJECTION

Architectural Invariants (Ratified F6 Architecture):
1. CapabilityRegistry remains the global source of truth for capability existence.
2. SecurityEngine remains the sole authority for authorization (RBAC + ABAC).
3. SecurityPrincipal / CapabilityExecutionContext remains the sole authority for tenant identity.
4. Caller-supplied tenant_id is NEVER trusted or accepted as an authority.
5. Zero modifications to CapabilityDescriptor or ConnectorActionDescriptor.
6. Connector profiles, secret validity, and external service health are execution concerns,
   NOT discovery filters.
7. Zero database migrations, zero persistent state, zero cross-request cache.
8. Single capability lookup silently fails closed with CapabilityNotFoundError on unauthorized
   access, preventing metadata leakage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kortex.core.dispatch import CapabilityExecutionContext, _safe_classification
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.engines.registry.engine import CapabilityDescriptor, RegistryEngine
from kortex.engines.security.models import (
    PermissionRequirement,
    SecurityPrincipal,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.core.projection")


class CapabilityProjection:
    """Stateless projection engine computing tenant-authorized capability visibility.

    Evaluates whether globally registered capabilities in CapabilityRegistry are
    authorized for a verified tenant principal by delegating strictly to
    SecurityEngine's authorization authority. Never inspects connector profiles,
    secrets, or external network availability.
    """

    def __init__(
        self,
        kernel: Kernel | None = None,
        *,
        registry_engine: RegistryEngine | None = None,
        security_engine: Any = None,
    ) -> None:
        self._kernel = kernel
        self._registry_engine = registry_engine
        self._security_engine = security_engine

    def _resolve_registry(self) -> Any:
        if self._registry_engine is not None:
            return self._registry_engine
        if self._kernel is not None:
            return self._kernel
        raise RuntimeError("No registry or kernel available for capability projection.")

    def _resolve_security_engine(self) -> Any:
        if self._security_engine is not None:
            return self._security_engine
        if self._kernel is not None:
            try:
                sec_eng = self._kernel.get_engine("security")
                if sec_eng is not None:
                    state = getattr(sec_eng, "state", None)
                    if state is None or getattr(state, "value", str(state)) in ("READY", "RUNNING"):
                        return sec_eng
            except Exception as exc:
                logger.debug("Security engine lookup on kernel failed: %s", exc)
        return None

    @staticmethod
    def _extract_principal(
        identity: CapabilityExecutionContext | SecurityPrincipal | None,
    ) -> SecurityPrincipal | None:
        if isinstance(identity, CapabilityExecutionContext):
            return identity.principal
        if isinstance(identity, SecurityPrincipal):
            return identity
        return None

    async def is_authorized(
        self,
        descriptor: CapabilityDescriptor,
        principal: SecurityPrincipal | None,
    ) -> bool:
        """Evaluate if a capability descriptor is authorized for the given principal.

        Delegates strictly to SecurityEngine's authorization engine (RBAC + ABAC)
        without creating execution audit logs or publishing dispatch events.
        """
        if not descriptor.requires_authentication:
            return True

        if principal is None:
            return False

        sec_engine = self._resolve_security_engine()
        if sec_engine is None:
            logger.warning(
                "SecurityEngine unavailable; failing closed for capability '%s'",
                descriptor.name,
            )
            return False

        evaluator = getattr(sec_engine, "authorization_engine", sec_engine)
        if evaluator is None:
            return False

        requirement = PermissionRequirement(
            capability_name=descriptor.name,
            required_permissions=list(descriptor.required_permissions or []),
            security_classification=_safe_classification(descriptor.security_classification),
        )
        context = {"resource_tenant_id": principal.tenant_id}

        try:
            decision = await evaluator.authorize(principal, requirement, context)
            return bool(decision.is_allowed)
        except Exception as exc:
            logger.debug(
                "Authorization check failed for principal '%s' on capability '%s': %s",
                principal.principal_id,
                descriptor.name,
                exc,
            )
            return False

    async def project_capabilities(
        self,
        identity: CapabilityExecutionContext | SecurityPrincipal | None,
        *,
        keyword: str | None = None,
        owner_domain: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        is_read_only: bool | None = None,
        is_idempotent: bool | None = None,
    ) -> list[CapabilityDescriptor]:
        """Return a tenant-scoped read-only projection of globally registered capabilities.

        The projection silently omits unauthorized capabilities, returning only
        those capabilities for which the authoritative tenant principal is permitted.
        """
        principal = self._extract_principal(identity)
        registry = self._resolve_registry()

        candidates = registry.search_capabilities(
            owner_domain=owner_domain,
            resource_type=resource_type,
            action=action,
            is_read_only=is_read_only,
            is_idempotent=is_idempotent,
            keyword=keyword,
        )

        projected: list[CapabilityDescriptor] = []
        for descriptor in candidates:
            if await self.is_authorized(descriptor, principal):
                projected.append(descriptor)

        return projected

    async def get_projected_capability(
        self,
        capability_name: str,
        identity: CapabilityExecutionContext | SecurityPrincipal | None,
    ) -> CapabilityDescriptor:
        """Retrieve a single capability descriptor projected for the tenant.

        Raises CapabilityNotFoundError if the capability either does not exist
        in the global registry or is not authorized for the tenant principal,
        preventing sensitive metadata disclosure.
        """
        principal = self._extract_principal(identity)
        registry = self._resolve_registry()

        try:
            descriptor: CapabilityDescriptor = registry.get_capability(capability_name)
        except Exception:
            raise CapabilityNotFoundError(f"Capability '{capability_name}' not found.") from None

        if not await self.is_authorized(descriptor, principal):
            raise CapabilityNotFoundError(f"Capability '{capability_name}' not found.")

        return descriptor
