"""
KORTEX Connector Action Semantic Capability Bridge (Milestone F5).

Turns individual connector actions into ordinary, first-class Kernel capabilities with real
`parameters_schema`/`returns_schema` metadata, discoverable through the existing
`Kernel.search_capabilities()`/`RegistryEngine.CapabilityDescriptor` catalog and invocable through
the existing `Kernel.invoke_capability()` boundary -- exactly like any other capability in KORTEX.

Architectural boundary (ratified F5 decision set):
- Connector Engine remains the sole owner of transport/execution: profile resolution, secret
  resolution, rate limiting, retry, driver dispatch, and audit history all continue to happen
  entirely inside `ConnectorEngine.execute_action()`, completely unmodified by this module.
- This module owns exactly one thing: translating a declarative `ConnectorActionDescriptor` into
  (a) a registered Kernel capability and (b) a thin, generic dispatch handler that builds an
  `ActionRequest` from the capability's own validated parameters and calls
  `ConnectorEngine.execute_action()` -- nothing more. It never resolves a driver, a profile, or a
  secret directly, and never duplicates retry/rate-limit/authorization logic.
- No new registry: `register_action_capabilities()` is authoring/registration sugar over the
  existing `Kernel.register_capability()`; `CapabilityRegistry` remains the sole runtime source of
  truth for what capabilities exist. `ConnectorActionDescriptor` instances are not looked up or
  read again once registration completes -- resolving a connector action at invocation time always
  goes through the same `Kernel.get_capability()`/`Kernel.invoke_capability()` path any other
  capability uses, never back through a descriptor.

Capability identity (D5): `kortex.connector.<semantic-domain>.<resource>.<action>` -- the semantic
operation, not the transport verb (`ConnectorActionType.SEND/RECEIVE/FETCH/PUSH/VERIFY` remain
internal metadata on the descriptor, mapped to `ActionRequest.action_type`, never exposed in the
capability name) and not a specific third-party provider (the provider/driver a given invocation
actually reaches is determined by which `ConnectorProfile` the caller names, carried as ordinary
profile metadata -- never baked into capability identity, so the same semantic capability can be
backed by different provider connections without a name change).

Capability stability (D14): a published capability name is a stable semantic contract.
Backward-compatible metadata/schema changes may keep the same name; a breaking semantic or schema
change must register under a new capability name. No capability-version field, table, or pinning
mechanism is introduced -- this matches every other capability in the Kernel Registry today.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.engines.connector.models import ActionRequest, ActionResult, ConnectorActionType
from kortex.engines.security.models import SecurityPrincipal

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel
    from kortex.engines.connector.engine import ConnectorEngine

logger = logging.getLogger("kortex.engines.connector.actions")


class ConnectorActionDescriptor(BaseModel):
    """Declarative definition of one semantic connector action.

    The smallest set of fields needed to turn a connector action into an ordinary Kernel
    capability (D4/D6) -- nothing here is a second source of runtime truth; every field maps
    directly onto an existing `Kernel.register_capability()` parameter or `ActionRequest` field.

    `parameters_schema`'s declared properties become the connector action's `ActionRequest.payload`
    verbatim (minus the always-required `profile_id`, which every action capability accepts
    separately and never as part of the semantic payload) -- there is no second, hidden payload
    transformation step. For the F5 reference actions (built on the generic
    `HttpRestConnectorDriver`), this means schema property names intentionally mirror that driver's
    own payload contract (`url`/`body`/`params`); a future richer descriptor could add a per-driver
    payload-mapping layer without changing this model's public shape.
    """

    model_config = ConfigDict(frozen=True)

    capability_name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    connector_action_type: ConnectorActionType
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    is_read_only: bool
    is_idempotent: bool
    security_classification: str = "INTERNAL"
    provider: str | None = None
    resource: str | None = None
    action: str | None = None


def _resolve_principal(
    execution_context: CapabilityExecutionContext | None, principal: SecurityPrincipal | None
) -> SecurityPrincipal | None:
    """Mirror the identity-resolution precedent already established for F4 lifecycle handlers:
    `execution_context` (dispatcher-injected, always present in production dispatch since every
    connector-action capability registers `requires_execution_context=True`) is authoritative when
    present; a directly-injected `principal` is used otherwise."""
    if principal is not None:
        return principal
    if execution_context is not None:
        return execution_context.principal
    return None


def make_action_handler(
    descriptor: ConnectorActionDescriptor, connector_engine: ConnectorEngine
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the one reusable, generic dispatch handler for a connector action capability (D7).

    The returned handler:
      1. Accepts ordinary Kernel capability parameters -- `profile_id` plus whatever the
         descriptor's own `parameters_schema` declares -- exactly as the Kernel dispatcher splats
         a capability's `parameters` dict onto the handler (`handler(**parameters, ...)`).
      2. Builds an `ActionRequest` from those parameters, with NO caller-supplied `tenant_id`
         parameter at all -- tenant authority comes exclusively from the dispatcher-injected
         `execution_context`/`principal` (D17), passed straight through to
         `ConnectorEngine.execute_action`, which applies its own existing
         principal-authoritative-tenant override.
      3. Invokes the existing, unmodified `ConnectorEngine.execute_action()` -- never a driver,
         never `ConnectorProfileManager`, never the rate limiter/pipeline directly.
      4. Returns the connector result's `response_payload` on success (shaped by the descriptor's
         own `returns_schema`), and raises on a non-SUCCESS result so a workflow step or direct
         Kernel caller sees an ordinary capability failure -- this mirrors
         `ConnectorEngine.execute_action` itself already raising for security/not-found errors, and
         extends that same "failure surfaces as an exception" contract to a driver-level failure
         (e.g. a non-2xx HTTP response) that `execute_action` otherwise returns as a normal
         `ActionResult(status="FAILED")` for its own generic caller.

    Argument checking is deliberately limited to "every property the descriptor's own schema
    marks `required` is present" -- a presence check derived from the descriptor's single existing
    `parameters_schema`, not a second schema-validation engine. Full type/enum/bounds validation
    for a tool-invoked call already happens for free via `ToolDefinition.validate_arguments()`
    (the generated tool copies this same `parameters_schema` verbatim, see `capability_tool_bridge.
    py`); a directly-dispatched or workflow-invoked call that violates the schema's shape instead
    surfaces as an ordinary driver-level error (e.g. `HttpRestConnectorDriver`'s own "payload must
    contain a non-empty 'url' string") -- reusing `ai.tools.validate_schema` here would invert the
    Connector Engine's position below the AI/tool layer in the ratified F5 architecture.
    """

    required_fields = tuple(descriptor.parameters_schema.get("required", ()))

    async def _handler(
        profile_id: str,
        execution_context: CapabilityExecutionContext | None = None,
        principal: SecurityPrincipal | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        missing = [field for field in required_fields if field not in kwargs]
        if missing:
            raise ConnectorActionValidationError(
                f"Missing required parameter(s) {missing} for connector action '{descriptor.capability_name}'.",
                descriptor.capability_name,
            )

        resolved_principal = _resolve_principal(execution_context, principal)
        correlation_id = execution_context.correlation_id if execution_context is not None else None

        action_request = ActionRequest(
            request_id=str(uuid.uuid4()),
            profile_id=profile_id,
            action_type=descriptor.connector_action_type,
            payload=dict(kwargs),
            correlation_id=correlation_id,
        )

        result: ActionResult = await connector_engine.execute_action(action_request, principal=resolved_principal)

        if result.status != "SUCCESS":
            error_message = "Connector action did not succeed."
            if result.error_details and isinstance(result.error_details, dict):
                error_message = str(result.error_details.get("error", error_message))
            raise ConnectorActionFailedError(error_message, descriptor.capability_name, result.error_details)

        return result.response_payload

    _handler.__name__ = f"connector_action_handler__{descriptor.capability_name.replace('.', '_')}"
    return _handler


class ConnectorActionValidationError(Exception):
    """Raised when a connector-action capability is invoked without one of the required
    parameters its own `parameters_schema` declares."""

    def __init__(self, message: str, capability_name: str) -> None:
        super().__init__(message)
        self.capability_name = capability_name


class ConnectorActionFailedError(Exception):
    """Raised by a connector-action capability handler when the underlying `ActionResult` did not
    succeed, so ordinary Kernel/Workflow failure handling applies (retry, compensation, step
    failure) exactly as it would for any other capability's own exception."""

    def __init__(self, message: str, capability_name: str, error_details: dict[str, Any] | None) -> None:
        super().__init__(message)
        self.capability_name = capability_name
        self.error_details = error_details


_PROFILE_ID_SCHEMA_PROPERTY: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "description": "The tenant-scoped ConnectorProfile (connection instance) this action executes against.",
}


def _build_full_parameters_schema(descriptor: ConnectorActionDescriptor) -> dict[str, Any]:
    """Prepend the always-required `profile_id` property to a descriptor's own action-specific
    `parameters_schema`, producing the complete input contract the registered
    `CapabilityDescriptor` publishes -- callers, workflow authors, and the AI tool generator must
    see `profile_id` as part of the schema, not as an undocumented implicit parameter."""
    own_schema = descriptor.parameters_schema or {}
    properties = {"profile_id": _PROFILE_ID_SCHEMA_PROPERTY, **own_schema.get("properties", {})}
    required = ["profile_id", *[r for r in own_schema.get("required", []) if r != "profile_id"]]
    return {"type": "object", "properties": properties, "required": required}


def register_action_capabilities(
    kernel: Kernel,
    connector_engine: ConnectorEngine,
    descriptors: list[ConnectorActionDescriptor],
) -> None:
    """Register a list of `ConnectorActionDescriptor`s as ordinary Kernel capabilities (D6).

    Pure authoring/registration convenience: every descriptor becomes exactly one
    `kernel.register_capability()` call with a freshly-built generic handler
    (`make_action_handler`). After this returns, `CapabilityRegistry` is the only place these
    capabilities live -- the `descriptors` list itself is not retained or consulted again.

    Every registered capability sets `requires_execution_context=True` (D9/D17): tenant authority
    for connector actions comes exclusively from the dispatcher-injected execution context, never
    from a caller-supplied parameter.
    """
    for descriptor in descriptors:
        handler = make_action_handler(descriptor, connector_engine)
        kernel.register_capability(
            name=descriptor.capability_name,
            description=descriptor.description,
            provider=connector_engine.name,
            handler=handler,
            parameters_schema=_build_full_parameters_schema(descriptor),
            returns_schema=descriptor.returns_schema,
            required_permissions=list(descriptor.required_permissions),
            security_classification=descriptor.security_classification,
            requires_execution_context=True,
            is_read_only=descriptor.is_read_only,
            is_idempotent=descriptor.is_idempotent,
        )
        logger.info("Registered connector action capability '%s'.", descriptor.capability_name)


class ConnectorActionBootstrapEngine(BaseEngine):
    """Minimal Kernel-registered engine whose sole job is registering a fixed list of
    `ConnectorActionDescriptor`s as Kernel capabilities during boot.

    `Kernel.register_capability` rejects registration once the Kernel has finished booting (state
    RUNNING) -- confirmed by direct testing, not merely assumed -- so connector action capabilities
    cannot be registered from `kernel_bootstrap.py` after `await kernel.boot()` the way connector
    *drivers* are (`ConnectorEngine.register_driver` requires the opposite: READY/RUNNING). A
    dedicated engine keeps this registration inside the normal `initialize()` boot phase without
    modifying `ConnectorEngine` itself in any way (D2) -- it depends on `"connector"` purely to be
    initialized after the Connector Engine exists and has registered its own capabilities, and holds
    no state or behavior beyond this one registration step.
    """

    def __init__(self, descriptors: list[ConnectorActionDescriptor]) -> None:
        super().__init__()
        self._descriptors = list(descriptors)

    @property
    def name(self) -> str:
        return "connector_actions"

    @property
    def dependencies(self) -> list[str]:
        return ["connector"]

    async def initialize(self, kernel: Kernel) -> None:
        self._set_state(EngineState.INITIALIZING)
        try:
            connector_engine = cast("ConnectorEngine", kernel.get_engine("connector"))
            register_action_capabilities(kernel, connector_engine, self._descriptors)
            self._set_state(EngineState.READY)
        except Exception:
            self._set_state(EngineState.FAILED)
            raise

    async def start(self) -> None:
        self._set_state(EngineState.RUNNING)

    async def stop(self) -> None:
        self._set_state(EngineState.STOPPED)

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy", "registered_action_count": len(self._descriptors)}
