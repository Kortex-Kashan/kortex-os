"""
KORTEX Capability Projection API & AI Tool Seam (Milestone F6).

Registers canonical tenant-facing capability projection endpoints on the Kernel and
provides the tenant-safe AI tool filtering seam.

Conceptual Path:
    GLOBAL ToolDefinitions (ToolRegistry)
                  ↓
    F6 Tenant Authorization Projection
                  ↓
    Authorized ToolDefinitions (Future AI Context / Tool Pipeline)

Architectural Invariants (Ratified F6 Architecture):
1. ToolRegistry remains process-global; never converted to a tenant registry or mutated.
2. Tenant identity is derived exclusively from SecurityPrincipal / CapabilityExecutionContext.
3. Caller-supplied tenant_id is NEVER trusted or accepted as an authority.
4. Unauthorized capabilities are omitted from collection discovery.
5. Single capability lookup raises CapabilityNotFoundError when unauthorized, preventing
   sensitive metadata leakage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.projection import CapabilityProjection
from kortex.engines.ai.tools import IToolRegistry, ToolDefinition
from kortex.engines.registry.engine import CapabilityDescriptor
from kortex.engines.security.models import SecurityPrincipal

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.api.capability_projection")

PROJECT_CAPABILITY_NAME = "kortex.system.capability.project"
GET_CAPABILITY_NAME = "kortex.system.capability.get"

_PROJECT_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "keyword": {
            "type": "string",
            "description": "Optional case-insensitive keyword filter matching capability name or description.",
        },
        "owner_domain": {
            "type": "string",
            "description": "Optional filter matching owner domain.",
        },
        "resource_type": {
            "type": "string",
            "description": "Optional filter matching resource type.",
        },
        "action": {
            "type": "string",
            "description": "Optional filter matching action.",
        },
        "is_read_only": {
            "type": "boolean",
            "description": "Optional filter matching is_read_only classification.",
        },
        "is_idempotent": {
            "type": "boolean",
            "description": "Optional filter matching is_idempotent classification.",
        },
    },
    "additionalProperties": False,
}

_PROJECT_RETURNS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "provider": {"type": "string"},
            "parameters_schema": {"type": "object"},
            "returns_schema": {"type": "object"},
            "required_permissions": {
                "type": ["array", "null"],
                "items": {"type": "string"},
            },
            "requires_authentication": {"type": "boolean"},
            "security_classification": {"type": "string"},
            "is_read_only": {"type": "boolean"},
            "is_idempotent": {"type": "boolean"},
            "owner_domain": {"type": "string"},
            "resource_type": {"type": "string"},
            "action": {"type": "string"},
        },
        "required": ["name", "description", "provider"],
    },
}

_GET_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "capability_name": {
            "type": "string",
            "minLength": 1,
            "description": "Canonical name of the capability to look up.",
        },
    },
    "required": ["capability_name"],
    "additionalProperties": False,
}

_GET_RETURNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "provider": {"type": "string"},
        "parameters_schema": {"type": "object"},
        "returns_schema": {"type": "object"},
        "required_permissions": {
            "type": ["array", "null"],
            "items": {"type": "string"},
        },
        "requires_authentication": {"type": "boolean"},
        "security_classification": {"type": "string"},
        "is_read_only": {"type": "boolean"},
        "is_idempotent": {"type": "boolean"},
        "owner_domain": {"type": "string"},
        "resource_type": {"type": "string"},
        "action": {"type": "string"},
    },
    "required": ["name", "description", "provider"],
}


def register_projection_capabilities(kernel: Kernel) -> list[CapabilityDescriptor]:
    """Register tenant-facing capability projection capabilities with the Kernel.

    Registers:
    - `kortex.system.capability.project`: List/search authorized capabilities for caller's tenant.
    - `kortex.system.capability.get`: Retrieve a single authorized capability descriptor.
    """
    projection = CapabilityProjection(kernel)

    async def _project_handler(
        execution_context: CapabilityExecutionContext,
        *,
        keyword: str | None = None,
        owner_domain: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        is_read_only: bool | None = None,
        is_idempotent: bool | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        # Any caller-supplied tenant_id in kwargs is discarded; tenant identity comes
        # strictly from execution_context.
        descriptors = await projection.project_capabilities(
            execution_context,
            keyword=keyword,
            owner_domain=owner_domain,
            resource_type=resource_type,
            action=action,
            is_read_only=is_read_only,
            is_idempotent=is_idempotent,
        )
        return [d.model_dump() for d in descriptors]

    async def _get_handler(
        execution_context: CapabilityExecutionContext,
        *,
        capability_name: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        descriptor = await projection.get_projected_capability(
            capability_name=capability_name,
            identity=execution_context,
        )
        return descriptor.model_dump()

    project_descriptor = kernel.register_capability(
        name=PROJECT_CAPABILITY_NAME,
        description="Tenant-scoped projection of available capabilities.",
        provider="system",
        handler=_project_handler,
        parameters_schema=_PROJECT_PARAMETERS_SCHEMA,
        returns_schema=_PROJECT_RETURNS_SCHEMA,
        required_permissions=[],
        requires_authentication=True,
        requires_execution_context=True,
        is_read_only=True,
        is_idempotent=True,
    )

    get_descriptor = kernel.register_capability(
        name=GET_CAPABILITY_NAME,
        description="Retrieve a single capability descriptor projected for the caller's tenant.",
        provider="system",
        handler=_get_handler,
        parameters_schema=_GET_PARAMETERS_SCHEMA,
        returns_schema=_GET_RETURNS_SCHEMA,
        required_permissions=[],
        requires_authentication=True,
        requires_execution_context=True,
        is_read_only=True,
        is_idempotent=True,
    )

    return [project_descriptor, get_descriptor]


async def project_tools_for_tenant(
    kernel: Kernel,
    tool_registry: IToolRegistry | list[ToolDefinition],
    execution_context: CapabilityExecutionContext | SecurityPrincipal,
) -> list[ToolDefinition]:
    """Filter global ToolDefinitions against the tenant's authorized capability projection.

    Invariants:
    - ToolRegistry remains process-global and is never mutated.
    - Preserves authorized ToolDefinitions with all schemas, timeouts, and flags intact.
    - Omits unauthorized ToolDefinitions.
    - Tenant identity comes strictly from execution_context / SecurityPrincipal.
    """
    principal = (
        execution_context.principal if isinstance(execution_context, CapabilityExecutionContext) else execution_context
    )

    projection = CapabilityProjection(kernel)
    tools: list[ToolDefinition] = (
        tool_registry.list_tools() if hasattr(tool_registry, "list_tools") else list(tool_registry)
    )

    authorized_tools: list[ToolDefinition] = []
    for tool in tools:
        canonical_name = tool.canonical_capability
        try:
            descriptor = kernel.get_capability(canonical_name)
        except Exception:
            logger.debug(
                "Tool '%s' points to unknown canonical capability '%s'; omitting from projection.",
                tool.name,
                canonical_name,
            )
            continue

        if await projection.is_authorized(descriptor, principal):
            authorized_tools.append(tool)

    return authorized_tools
