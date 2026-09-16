"""
KORTEX MCP Gateway / Catalog (Integration Hub — MCP Gateway/Catalog milestone).

Two thin, additive boundaries layered on top of the *existing*, unmodified
Integration Hub M1/M2 architecture. Neither is a second registry, a second
dispatcher, or a second execution engine:

- **Catalog** — a pure *read projection* over the two existing sources of
  truth. Profiles come from `ConnectorProfileManager` (reached only through
  `ConnectorEngine`'s already principal-authoritative `get_profile()`/
  `list_profiles()`); capability metadata comes from `RegistryEngine` (read
  back through `Kernel.list_capabilities()`/`get_capability()` and filtered
  through the existing F6 `CapabilityProjection` authorization seam). The
  Catalog persists nothing, registers nothing, mutates no profile state, and
  never triggers MCP reconciliation — reconciliation remains owned entirely
  by `McpConnectorDriver`'s own session lifecycle (`reconcile_tools()`), the
  same as before this module existed.

- **Gateway** — a controlled *access/routing* boundary in front of the
  existing capability execution path. It authorizes (tenant → profile →
  capability), resolves the canonical capability identifier, and then hands
  off to `Kernel.invoke_capability()`, the single authoritative execution
  boundary. It never touches `McpConnectorDriver`, `ConnectorDriverRegistry`,
  a transport, or a secret — this module imports none of them.

Execution path (unchanged below the Gateway's hand-off):

    caller -> IPC -> kortex.mcp.gateway.invoke
           -> tenant/profile/capability authorization (here)
           -> kortex.mcp.<profile_id>.<tool_name>
           -> Kernel.invoke_capability -> CapabilityDispatcher
           -> McpConnectorDriver capability handler -> MCP server -> result

The nested dispatch forwards `execution_context.session_token` — the *same*,
already-verified token this invocation's own identity was established from,
never a caller-suppliable one — exactly the precedent set by Workflow's
external-operation executor (`WorkflowEngine.execute_external_operation`).
Tenant identity comes exclusively from `execution_context.principal`;
`tenant_id` is not, and must never become, a caller-supplied parameter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from kortex.core.dispatch import CapabilityExecutionContext, CapabilityRequest
from kortex.core.exceptions import CapabilityNotFoundError
from kortex.core.projection import CapabilityProjection
from kortex.engines.connector.exceptions import (
    ConnectorProfileNotFoundError,
    ConnectorSecurityError,
    ConnectorValidationError,
)
from kortex.engines.connector.models import ConnectorProfile

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.connector.engine import ConnectorEngine
    from kortex.engines.registry.engine import CapabilityDescriptor
    from kortex.engines.security.models import SecurityPrincipal

logger = logging.getLogger("kortex.engines.connector.mcp_gateway")

CATALOG_PROFILE_LIST_CAPABILITY = "kortex.mcp.catalog.profile.list"
CATALOG_PROFILE_GET_CAPABILITY = "kortex.mcp.catalog.profile.get"
CATALOG_CAPABILITY_LIST_CAPABILITY = "kortex.mcp.catalog.capability.list"
CATALOG_CAPABILITY_GET_CAPABILITY = "kortex.mcp.catalog.capability.get"
GATEWAY_INVOKE_CAPABILITY = "kortex.mcp.gateway.invoke"

# The `driver_id` values that mark a ConnectorProfile as an MCP connection —
# the exact pair `ConnectorEngine.register_profile()`/`delete_profile()`
# already branch on for MCP driver wiring and teardown. Kept identical here
# rather than re-derived, so "what counts as an MCP profile" has one meaning
# across the engine and this module.
MCP_DRIVER_IDS: frozenset[str] = frozenset({"connector-mcp", "kortex.mcp"})

# Canonical MCP capability namespace, owned by `McpConnectorDriver`
# (`reconcile_tools()` registers `kortex.mcp.<profile_id>.<tool_name>` with
# `owner_id=profile_id`). This module only *reads back* that namespace; it
# never registers into it.
MCP_CAPABILITY_NAMESPACE = "kortex.mcp"


def mcp_capability_name(profile_id: str, tool_name: str) -> str:
    """Build the canonical MCP capability identifier for a profile's tool.

    The single place the Gateway and Catalog agree on capability identity —
    identical to the string `McpConnectorDriver.reconcile_tools()` registers,
    so a `tool_name` read out of the Catalog always round-trips back through
    `kortex.mcp.gateway.invoke` unchanged.
    """
    return f"{MCP_CAPABILITY_NAMESPACE}.{profile_id}.{tool_name}"


def _mcp_capability_prefix(profile_id: str) -> str:
    return f"{MCP_CAPABILITY_NAMESPACE}.{profile_id}."


# Profile `options` keys the Catalog is permitted to publish. An allowlist,
# not a denylist: a profile's `options` is a free-form dict a tenant (or a
# future integration) can put anything into, so enumerating what may be shown
# is the only formulation that stays safe as new option keys appear. Note
# `secret_handle` is a first-class `ConnectorProfile` field and is simply
# never read by `_public_profile_view()` below — it cannot leak through this
# allowlist at all.
_PUBLIC_PROFILE_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "endpoint_url",
        "transport",
        "integration_provider",
    }
)

# Parameter keys the Kernel dispatcher reserves for dispatcher-injected
# identity (`kortex.core.dispatch._RESERVED_PARAMETER_KEYS`). A caller's MCP
# `arguments` may not carry them: the nested dispatch below would be rejected
# outright by the dispatcher anyway, and rejecting here produces an honest,
# attributable Gateway-level validation error instead of an opaque one from
# two frames deeper.
_RESERVED_ARGUMENT_KEYS: frozenset[str] = frozenset({"execution_context", "principal"})


def _public_profile_view(profile: ConnectorProfile) -> dict[str, Any]:
    """Project a `ConnectorProfile` down to its non-sensitive, catalog-visible fields.

    Constructed by explicit field enumeration rather than `model_dump()`-then-
    redact: a field added to `ConnectorProfile` in future is then invisible to
    the Catalog by default (fail-closed) instead of silently published.
    `secret_handle` is never read here, and `options` is allowlist-filtered.
    """
    return {
        "profile_id": profile.profile_id,
        "tenant_id": profile.tenant_id,
        "name": profile.name,
        "driver_id": profile.driver_id,
        "is_active": profile.is_active,
        "rate_limit_per_sec": profile.rate_limit_per_sec,
        "max_retries": profile.max_retries,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
        "options": {k: v for k, v in profile.options.items() if k in _PUBLIC_PROFILE_OPTION_KEYS},
    }


def _public_capability_view(descriptor: CapabilityDescriptor, profile_id: str) -> dict[str, Any]:
    """Project a `CapabilityDescriptor` down to its catalog-visible metadata.

    Same explicit-enumeration rationale as `_public_profile_view()`. `owner_id`
    is deliberately re-exposed as `profile_id` (the caller already named it) and
    the driver-internal handler is, by construction, unreachable — `RegistryEngine`
    does not carry a handler on the descriptor at all.
    """
    prefix = _mcp_capability_prefix(profile_id)
    return {
        "capability_name": descriptor.name,
        "profile_id": profile_id,
        "tool_name": descriptor.name[len(prefix) :],
        "description": descriptor.description,
        "provider": descriptor.provider,
        "parameters_schema": descriptor.parameters_schema,
        "returns_schema": descriptor.returns_schema,
        "requires_authentication": descriptor.requires_authentication,
        "security_classification": descriptor.security_classification,
        "is_read_only": descriptor.is_read_only,
        "is_idempotent": descriptor.is_idempotent,
    }


class McpGateway:
    """MCP Gateway (access/routing) and MCP Catalog (discovery/metadata).

    Holds no state of its own beyond references to the `Kernel` and the
    `ConnectorEngine` it was built for — every read resolves live against the
    existing sources of truth on each call, so there is no cache to go stale
    and nothing here to reconcile.
    """

    def __init__(self, kernel: Kernel, connector_engine: ConnectorEngine) -> None:
        self._kernel = kernel
        self._connector_engine = connector_engine
        self._projection = CapabilityProjection(kernel)

    # -- Caller identity -----------------------------------------------------

    @staticmethod
    def _require_principal(execution_context: CapabilityExecutionContext | None) -> SecurityPrincipal:
        """Resolve the authenticated caller, failing closed when absent.

        Every capability this module registers sets
        `requires_authentication=True` and `requires_execution_context=True`,
        so in production dispatch the principal is always present. This check
        is defense in depth against a direct, non-dispatch call path — and it
        is what makes "tenant_id is never caller-supplied" structurally true:
        there is no other channel this class reads tenancy from.
        """
        if execution_context is None or execution_context.principal is None:
            raise ConnectorSecurityError(
                "MCP Gateway/Catalog requires an authenticated caller execution context.",
            )
        return execution_context.principal

    # -- Profile resolution (shared by Catalog and Gateway) ------------------

    async def _resolve_mcp_profile(self, profile_id: str, principal: SecurityPrincipal) -> ConnectorProfile:
        """Resolve one MCP profile the caller's tenant owns, or raise "not found".

        Tenant ownership is enforced by `ConnectorEngine.get_profile()`, which
        passes `principal.tenant_id` down to the already enumeration-resistant
        `ConnectorProfileManager.get_profile()` (M6.3-1): another tenant's
        profile raises `ConnectorProfileNotFoundError`, indistinguishable from
        a profile that never existed.

        A profile that exists under the caller's own tenant but is *not* an MCP
        profile is masked the same way, for the same reason — the MCP Catalog
        must not become an oracle for the existence of a tenant's non-MCP
        connector profiles, which it has no business exposing.
        """
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ConnectorValidationError("'profile_id' must be a non-empty string.")

        profile = await self._connector_engine.get_profile(profile_id.strip(), principal=principal)

        if profile.driver_id not in MCP_DRIVER_IDS:
            raise ConnectorProfileNotFoundError(
                f"MCP profile '{profile_id}' not found.",
                details={"profile_id": profile_id},
            )
        return profile

    # -- Catalog: profiles ---------------------------------------------------

    async def list_profiles(
        self,
        execution_context: CapabilityExecutionContext | None = None,
        *,
        active_only: bool = False,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """`kortex.mcp.catalog.profile.list` — MCP profiles visible to the caller's tenant.

        A pure read: `ConnectorEngine.list_profiles()` (principal-authoritative
        tenant scoping, M7.3) filtered down to MCP `driver_id`s and projected
        through `_public_profile_view()`. No registry access, no driver access,
        no reconciliation, no mutation.

        `active_only` mirrors the identical parameter on the existing
        `kortex.connector.profile.list` capability, including its `False`
        default — the Catalog does not invent a stricter visibility convention
        than the connector catalog it projects.
        """
        principal = self._require_principal(execution_context)
        profiles = await self._connector_engine.list_profiles(
            active_only=active_only,
            principal=principal,
        )
        return [_public_profile_view(p) for p in profiles if p.driver_id in MCP_DRIVER_IDS]

    async def get_profile(
        self,
        profile_id: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.mcp.catalog.profile.get` — metadata for one authorized MCP profile."""
        principal = self._require_principal(execution_context)
        profile = await self._resolve_mcp_profile(profile_id, principal)
        return _public_profile_view(profile)

    # -- Catalog: capabilities -----------------------------------------------

    async def _authorized_profile_capabilities(
        self,
        profile_id: str,
        principal: SecurityPrincipal,
    ) -> list[CapabilityDescriptor]:
        """Read this profile's capabilities back out of `RegistryEngine`, authorized for the caller.

        Ownership is checked twice, deliberately, because the two checks mean
        different things: `owner_id == profile_id` proves `McpConnectorDriver`
        registered the capability *for this profile* (the authoritative link),
        while the name prefix proves it lives in this profile's slice of the
        canonical MCP namespace. Requiring both means neither a name collision
        nor a mis-set owner alone can surface a capability under the wrong
        profile.

        Each surviving descriptor then passes through the existing F6
        `CapabilityProjection.is_authorized()` seam, so the Catalog can never
        advertise a capability the caller could not actually execute.
        """
        prefix = _mcp_capability_prefix(profile_id)
        owned = [
            desc
            for desc in self._kernel.list_capabilities()
            if desc.owner_id == profile_id and desc.name.startswith(prefix)
        ]
        authorized: list[CapabilityDescriptor] = []
        for desc in owned:
            if await self._projection.is_authorized(desc, principal):
                authorized.append(desc)
        return sorted(authorized, key=lambda d: d.name)

    async def list_capabilities(
        self,
        profile_id: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        """`kortex.mcp.catalog.capability.list` — tools of one authorized MCP profile.

        The profile is resolved and tenant-authorized *first*, so an unowned
        `profile_id` fails as "not found" before any registry read happens at
        all — the caller learns nothing about another tenant's capabilities,
        not even their count.
        """
        principal = self._require_principal(execution_context)
        profile = await self._resolve_mcp_profile(profile_id, principal)
        descriptors = await self._authorized_profile_capabilities(profile.profile_id, principal)
        return [_public_capability_view(d, profile.profile_id) for d in descriptors]

    async def get_capability(
        self,
        profile_id: str,
        tool_name: str,
        execution_context: CapabilityExecutionContext | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.mcp.catalog.capability.get` — metadata for one authorized MCP tool.

        Resolves through the same authorization chain the Gateway itself uses
        (`_resolve_authorized_capability`), so catalog visibility and gateway
        invocability can never disagree: anything readable here is executable
        by this caller, and anything hidden here is rejected there.
        """
        principal = self._require_principal(execution_context)
        profile = await self._resolve_mcp_profile(profile_id, principal)
        descriptor = await self._resolve_authorized_capability(profile, tool_name, principal)
        return _public_capability_view(descriptor, profile.profile_id)

    # -- Gateway -------------------------------------------------------------

    @staticmethod
    def _validate_tool_name(tool_name: str) -> str:
        """Validate a caller-supplied `tool_name` before it becomes part of a capability identifier.

        A dot is rejected rather than sanitized: `McpConnectorDriver.
        reconcile_tools()` already replaces dots in a remote tool's name with
        underscores at registration time, so a dotted `tool_name` can never
        match a registered capability — and accepting one would let a caller
        inject extra segments into the canonical
        `kortex.mcp.<profile_id>.<tool_name>` identifier, aiming the lookup at
        a name the profile does not own. Rejecting it keeps the identifier's
        segment structure decided here, not by caller input.
        """
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ConnectorValidationError("'tool_name' must be a non-empty string.")
        candidate = tool_name.strip()
        if "." in candidate:
            raise ConnectorValidationError(
                f"Invalid 'tool_name' {tool_name!r}: MCP tool names may not contain '.'.",
            )
        return candidate

    async def _resolve_authorized_capability(
        self,
        profile: ConnectorProfile,
        tool_name: str,
        principal: SecurityPrincipal,
    ) -> CapabilityDescriptor:
        """Resolve `kortex.mcp.<profile_id>.<tool_name>` and prove the caller may reach it.

        Three separate conditions must hold, and all three fail as the same
        `CapabilityNotFoundError` — mirroring `CapabilityProjection.
        get_projected_capability()`'s existing masking convention, so an
        unauthorized caller cannot distinguish "exists but not yours" from
        "does not exist":

        1. the capability is registered at all;
        2. its `owner_id` is exactly this profile (a capability belonging to a
           *different* profile of the caller's own tenant is still rejected —
           this is the "wrong profile/tool combination" case);
        3. the caller is authorized for it under the existing F6 projection.
        """
        validated_tool = self._validate_tool_name(tool_name)
        capability_name = mcp_capability_name(profile.profile_id, validated_tool)

        try:
            descriptor: CapabilityDescriptor = self._kernel.get_capability(capability_name)
        except Exception:
            raise CapabilityNotFoundError(f"MCP capability '{capability_name}' not found.") from None

        if descriptor.owner_id != profile.profile_id:
            raise CapabilityNotFoundError(f"MCP capability '{capability_name}' not found.")

        if not await self._projection.is_authorized(descriptor, principal):
            raise CapabilityNotFoundError(f"MCP capability '{capability_name}' not found.")

        return descriptor

    @staticmethod
    def _validate_arguments(arguments: Any) -> dict[str, Any]:
        """Validate the caller-supplied MCP tool `arguments` object."""
        if arguments is None:
            return {}
        if not isinstance(arguments, dict):
            raise ConnectorValidationError("'arguments' must be a JSON object.")
        offending = _RESERVED_ARGUMENT_KEYS.intersection(arguments)
        if offending:
            raise ConnectorValidationError(
                f"'arguments' may not supply dispatcher-reserved key(s) {sorted(offending)} — "
                "caller identity is dispatcher-injected only.",
            )
        return dict(arguments)

    async def invoke(
        self,
        profile_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        execution_context: CapabilityExecutionContext | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """`kortex.mcp.gateway.invoke` — securely invoke one authorized MCP tool.

        Authorize, resolve, hand off. Every step below either reads already-
        verified identity or consults an existing authority; the actual
        execution is performed by `Kernel.invoke_capability()`, never here:

        1. authenticated caller from `execution_context.principal` (tenancy is
           never a parameter);
        2. profile resolved tenant-scoped, and masked as "not found" if the
           caller's tenant does not own it;
        3. an inactive profile is refused outright — a deactivated integration
           must not be reachable through a discovery/routing front door, and
           this is reported distinctly from "not found" because the caller
           demonstrably already owns the profile, so there is nothing left to
           conceal from them;
        4. capability resolved, owner-verified against this exact profile, and
           authorization-checked;
        5. dispatched by canonical name through the Kernel's single
           authoritative execution boundary, carrying forward the same verified
           session token this invocation's own identity came from.

        Returns the structured MCP result the driver produced, verbatim.
        """
        principal = self._require_principal(execution_context)
        assert execution_context is not None  # narrowed by `_require_principal` above

        profile = await self._resolve_mcp_profile(profile_id, principal)

        if not profile.is_active:
            raise ConnectorSecurityError(
                f"MCP profile '{profile.profile_id}' is not active; gateway invocation refused.",
                details={"profile_id": profile.profile_id},
            )

        descriptor = await self._resolve_authorized_capability(profile, tool_name, principal)
        tool_arguments = self._validate_arguments(arguments)

        logger.info(
            "MCP Gateway dispatching '%s' for tenant '%s'.",
            descriptor.name,
            principal.tenant_id,
        )

        nested_request = CapabilityRequest(
            capability_name=descriptor.name,
            session_token=execution_context.session_token,
            parameters=tool_arguments,
            correlation_id=execution_context.correlation_id,
            context={"resource_tenant_id": principal.tenant_id},
        )
        # `Kernel.invoke_capability` is deliberately typed `Any` — it dispatches
        # every capability in the platform, each with its own return shape. The
        # cast narrows it to the one contract that applies here:
        # `McpConnectorDriver.invoke_tool_with_retry`'s structured
        # `dict[str, Any]` result, which is what the canonical
        # `kortex.mcp.<profile_id>.<tool_name>` capability this line just
        # resolved always returns.
        return cast("dict[str, Any]", await self._kernel.invoke_capability(nested_request))


# -- Capability schemas ------------------------------------------------------

_PROFILE_ID_PROPERTY: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "description": "Identifier of the MCP ConnectorProfile, which must be owned by the caller's tenant.",
}

_TOOL_NAME_PROPERTY: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "description": (
        "Canonical KORTEX tool name within the profile, exactly as published by "
        "'kortex.mcp.catalog.capability.list'. May not contain '.'."
    ),
}

_PROFILE_VIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile_id": {"type": "string"},
        "tenant_id": {"type": "string"},
        "name": {"type": "string"},
        "driver_id": {"type": "string"},
        "is_active": {"type": "boolean"},
        "rate_limit_per_sec": {"type": "number"},
        "max_retries": {"type": "integer"},
        "created_at": {"type": ["string", "null"]},
        "updated_at": {"type": ["string", "null"]},
        "options": {
            "type": "object",
            "description": "Allow-listed, non-sensitive profile options. Never contains credential material.",
        },
    },
    "required": ["profile_id", "tenant_id", "name", "driver_id", "is_active"],
}

_CAPABILITY_VIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "capability_name": {"type": "string"},
        "profile_id": {"type": "string"},
        "tool_name": {"type": "string"},
        "description": {"type": "string"},
        "provider": {"type": "string"},
        "parameters_schema": {"type": "object"},
        "returns_schema": {"type": "object"},
        "requires_authentication": {"type": "boolean"},
        "security_classification": {"type": "string"},
        "is_read_only": {"type": "boolean"},
        "is_idempotent": {"type": "boolean"},
    },
    "required": ["capability_name", "profile_id", "tool_name", "description"],
}

_GATEWAY_INVOKE_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile_id": _PROFILE_ID_PROPERTY,
        "tool_name": _TOOL_NAME_PROPERTY,
        "arguments": {
            "type": "object",
            "description": "Arguments passed verbatim to the remote MCP tool.",
        },
    },
    "required": ["profile_id", "tool_name"],
}

_GATEWAY_INVOKE_RETURNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_error": {"type": "boolean"},
        "content": {"type": "array", "items": {"type": "object"}},
        "tool_name": {"type": "string"},
        "profile_id": {"type": "string"},
    },
    "required": ["is_error", "content"],
}


def register_mcp_gateway_capabilities(kernel: Kernel, connector_engine: ConnectorEngine) -> McpGateway:
    """Register the five MCP Gateway/Catalog capabilities on the Kernel.

    Called from `ConnectorEngine.initialize()`, alongside that engine's other
    `kernel.register_capability()` calls, and for the same reason they live
    there: registration must happen inside the normal boot phase, while the
    Kernel is still `BOOTING` (`Kernel.register_capability` refuses once boot
    has completed). Deterministic and idempotent in exactly the sense the
    surrounding engine registrations already are — a fixed, ordered set of
    names registered exactly once per engine initialization.

    These five are static, boot-time capabilities with no `owner_id`, unlike
    the per-profile `kortex.mcp.<profile_id>.<tool>` capabilities that
    `McpConnectorDriver` registers dynamically at connect time. The Gateway
    front door exists whether or not any MCP profile has been created yet.

    Returns the constructed `McpGateway` so the engine can hold a reference.
    """
    gateway = McpGateway(kernel, connector_engine)

    kernel.register_capability(
        name=CATALOG_PROFILE_LIST_CAPABILITY,
        description="List MCP connector profiles visible to the caller's tenant.",
        provider=connector_engine.name,
        handler=gateway.list_profiles,
        parameters_schema={
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "When true, only active MCP profiles are returned.",
                },
            },
        },
        returns_schema={"type": "array", "items": _PROFILE_VIEW_SCHEMA},
        required_permissions=["connector:read"],
        requires_execution_context=True,
        is_read_only=True,
        is_idempotent=True,
    )

    kernel.register_capability(
        name=CATALOG_PROFILE_GET_CAPABILITY,
        description="Retrieve metadata for one MCP connector profile owned by the caller's tenant.",
        provider=connector_engine.name,
        handler=gateway.get_profile,
        parameters_schema={
            "type": "object",
            "properties": {"profile_id": _PROFILE_ID_PROPERTY},
            "required": ["profile_id"],
        },
        returns_schema=_PROFILE_VIEW_SCHEMA,
        required_permissions=["connector:read"],
        requires_execution_context=True,
        is_read_only=True,
        is_idempotent=True,
    )

    kernel.register_capability(
        name=CATALOG_CAPABILITY_LIST_CAPABILITY,
        description="List the MCP tools of one authorized MCP profile, as projected from the Registry Engine.",
        provider=connector_engine.name,
        handler=gateway.list_capabilities,
        parameters_schema={
            "type": "object",
            "properties": {"profile_id": _PROFILE_ID_PROPERTY},
            "required": ["profile_id"],
        },
        returns_schema={"type": "array", "items": _CAPABILITY_VIEW_SCHEMA},
        required_permissions=["connector:read"],
        requires_execution_context=True,
        is_read_only=True,
        is_idempotent=True,
    )

    kernel.register_capability(
        name=CATALOG_CAPABILITY_GET_CAPABILITY,
        description="Retrieve metadata for one MCP tool belonging to an authorized MCP profile.",
        provider=connector_engine.name,
        handler=gateway.get_capability,
        parameters_schema={
            "type": "object",
            "properties": {"profile_id": _PROFILE_ID_PROPERTY, "tool_name": _TOOL_NAME_PROPERTY},
            "required": ["profile_id", "tool_name"],
        },
        returns_schema=_CAPABILITY_VIEW_SCHEMA,
        required_permissions=["connector:read"],
        requires_execution_context=True,
        is_read_only=True,
        is_idempotent=True,
    )

    kernel.register_capability(
        name=GATEWAY_INVOKE_CAPABILITY,
        description=("Securely invoke an authorized MCP tool through the KORTEX capability execution boundary."),
        provider=connector_engine.name,
        handler=gateway.invoke,
        parameters_schema=_GATEWAY_INVOKE_PARAMETERS_SCHEMA,
        returns_schema=_GATEWAY_INVOKE_RETURNS_SCHEMA,
        required_permissions=["connector:execute"],
        requires_execution_context=True,
        is_read_only=False,
        is_idempotent=False,
    )

    logger.info("Registered MCP Gateway/Catalog capabilities.")
    return gateway


__all__ = [
    "CATALOG_CAPABILITY_GET_CAPABILITY",
    "CATALOG_CAPABILITY_LIST_CAPABILITY",
    "CATALOG_PROFILE_GET_CAPABILITY",
    "CATALOG_PROFILE_LIST_CAPABILITY",
    "GATEWAY_INVOKE_CAPABILITY",
    "MCP_CAPABILITY_NAMESPACE",
    "MCP_DRIVER_IDS",
    "McpGateway",
    "mcp_capability_name",
    "register_mcp_gateway_capabilities",
]
