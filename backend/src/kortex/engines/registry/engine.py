"""
KORTEX Registry Engine.

Provides centralized runtime registration and capability discovery for Modules,
System Engines, Recipes, Templates, Connectors, Capabilities, and Services.
"""

from __future__ import annotations

import datetime
import enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.exceptions import CapabilityNotFoundError, ResourceAlreadyExistsError, ResourceNotFoundError

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel


_BOOTSTRAP_EXEMPT_CAPABILITY = "kortex.security.auth.authenticate"
"""The one capability permitted to register with `requires_authentication=False` —
it must be reachable before any session token exists. Enforced in
`RegistryEngine.register_capability`, not merely documented as a convention."""


class RegistryCategory(str, enum.Enum):
    """Resource registration categories."""

    ENGINE = "ENGINE"
    MODULE = "MODULE"
    RECIPE = "RECIPE"
    TEMPLATE = "TEMPLATE"
    CONNECTOR = "CONNECTOR"
    CAPABILITY = "CAPABILITY"
    SERVICE = "SERVICE"


class ResourceMetadata(BaseModel):
    """Metadata descriptor for registered resources."""

    name: str = Field(description="Unique name identifier")
    category: RegistryCategory = Field(description="Category classification")
    version: str = Field(default="0.1.0", description="Resource version string")
    description: str = Field(default="", description="Human readable description")
    provider: str = Field(default="system", description="Provider or author module name")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Custom attribute metadata")
    registered_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc),
        description="Timestamp of registration",
    )


class CapabilityDescriptor(BaseModel):
    """Specification for an AI or system capability registered by a module or engine."""

    name: str = Field(description="Unique capability name, e.g., 'payroll.calculate'")
    description: str = Field(description="Description of what this capability performs")
    provider: str = Field(description="Name of the providing module or engine")
    parameters_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON schema for parameters")
    returns_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON schema for return value")
    required_permissions: Optional[List[str]] = Field(
        default=None,
        description=(
            "RBAC permission keys required to execute this capability, sourced exclusively from this "
            "descriptor by the Kernel dispatcher (`kortex.core.dispatch`) — never from a caller's "
            "request. `None` means this capability has never been explicitly classified: it still "
            "requires authentication when `requires_authentication` is True, but no RBAC permission "
            "check is performed for it. `None` does not mean unrestricted, does not grant permissions, "
            "and does not disable ABAC or classification checks. An empty list `[]` means explicitly "
            "classified as requiring zero specific permissions."
        ),
    )
    requires_authentication: bool = Field(
        default=True,
        description=(
            "Whether the Kernel dispatcher requires a verified session token before invoking this "
            "capability's handler. Only 'kortex.security.auth.authenticate' may register with this set "
            "to False — enforced in `register_capability` below, not merely a convention. Every other "
            "capability defaults to True."
        ),
    )
    security_classification: str = Field(
        default="INTERNAL",
        description=(
            "Minimum security classification governing this capability, stored as a plain string so "
            "the Registry stays independent of Security Engine's `ClassificationLevel` enum. "
            "Interpreted by the Kernel dispatcher, which fails closed to RESTRICTED on any unparseable "
            "value."
        ),
    )


class RegistryEngine(BaseEngine):
    """Central Capability and System Registry Engine."""

    def __init__(self) -> None:
        super().__init__()
        self._stores: Dict[RegistryCategory, Dict[str, ResourceMetadata]] = {
            cat: {} for cat in RegistryCategory
        }
        self._handlers: Dict[str, Any] = {}
        self._capabilities: Dict[str, CapabilityDescriptor] = {}
        self._capability_handlers: Dict[str, Optional[Callable[..., Any]]] = {}
        """Milestone M8: the SOLE store of real capability handler callables.

        Deliberately separate from `_capabilities` (which holds the public
        `CapabilityDescriptor` returned by `get_capability()`/
        `list_capabilities()`). A `CapabilityDescriptor` never carries a
        reference to its handler — only this private dict does, and it is
        resolved exclusively via `_resolve_handler()` (dispatcher-internal)
        or `get_raw_handler_for_testing()` (test-only). This closes the
        direct `descriptor.handler(...)` bypass of `Kernel.invoke_capability()`
        found during M8 adversarial hardening.
        """

    @property
    def name(self) -> str:
        return "registry"

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize the Registry Engine."""
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing Registry Engine...")
        self._set_state(EngineState.READY)

    async def start(self) -> None:
        """Start the Registry Engine."""
        self.ensure_state(EngineState.READY)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Registry Engine running.")

    async def health_check(self) -> Dict[str, Any]:
        """Diagnostic health check."""
        counts = {cat.value: len(store) for cat, store in self._stores.items()}
        return {
            "engine": self.name,
            "status": "healthy" if self.state == EngineState.RUNNING else "unhealthy",
            "registered_counts": counts,
            "capabilities_count": len(self._capabilities),
        }

    async def stop(self) -> None:
        """Stop the Registry Engine."""
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Registry Engine stopped.")

    # -- Core Registration APIs ---------------------------------------------

    def register_resource(
        self,
        name: str,
        category: RegistryCategory,
        target_object: Any = None,
        description: str = "",
        version: str = "0.1.0",
        provider: str = "system",
        attributes: Optional[Dict[str, Any]] = None,
        allow_overwrite: bool = False,
    ) -> ResourceMetadata:
        """Register any named resource into a registry category."""
        store = self._stores[category]
        if name in store and not allow_overwrite:
            raise ResourceAlreadyExistsError(
                f"Resource '{name}' is already registered in category '{category.value}'."
            )

        meta = ResourceMetadata(
            name=name,
            category=category,
            version=version,
            description=description,
            provider=provider,
            attributes=attributes or {},
        )
        store[name] = meta
        if target_object is not None:
            self._handlers[f"{category.value}:{name}"] = target_object

        self.logger.info("Registered %s: '%s' (Provider: %s)", category.value, name, provider)
        return meta

    def get_resource(self, name: str, category: RegistryCategory) -> ResourceMetadata:
        """Fetch metadata for a registered resource."""
        store = self._stores[category]
        if name not in store:
            raise ResourceNotFoundError(
                f"Resource '{name}' not found in category '{category.value}'."
            )
        return store[name]

    def get_target_object(self, name: str, category: RegistryCategory) -> Any:
        """Fetch the registered target instance or handler object."""
        key = f"{category.value}:{name}"
        if key not in self._handlers:
            raise ResourceNotFoundError(
                f"Target instance object for '{name}' not found in category '{category.value}'."
            )
        return self._handlers[key]

    def list_resources(self, category: RegistryCategory) -> List[ResourceMetadata]:
        """List all registered metadata objects in a category."""
        return list(self._stores[category].values())

    # -- Specific Helper Methods --------------------------------------------

    def register_engine(self, engine_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(engine_name, RegistryCategory.ENGINE, instance, description=description)

    def get_engine(self, engine_name: str) -> Any:
        return self.get_target_object(engine_name, RegistryCategory.ENGINE)

    def register_module(self, module_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(module_name, RegistryCategory.MODULE, instance, description=description)

    def get_module(self, module_name: str) -> Any:
        return self.get_target_object(module_name, RegistryCategory.MODULE)

    def register_recipe(self, recipe_name: str, recipe_def: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(recipe_name, RegistryCategory.RECIPE, recipe_def, description=description)

    def get_recipe(self, recipe_name: str) -> Any:
        return self.get_target_object(recipe_name, RegistryCategory.RECIPE)

    def register_template(self, template_name: str, template_def: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(template_name, RegistryCategory.TEMPLATE, template_def, description=description)

    def get_template(self, template_name: str) -> Any:
        return self.get_target_object(template_name, RegistryCategory.TEMPLATE)

    def register_connector(self, connector_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(connector_name, RegistryCategory.CONNECTOR, instance, description=description)

    def get_connector(self, connector_name: str) -> Any:
        return self.get_target_object(connector_name, RegistryCategory.CONNECTOR)

    def register_service(self, service_name: str, instance: Any, description: str = "") -> ResourceMetadata:
        return self.register_resource(service_name, RegistryCategory.SERVICE, instance, description=description)

    def get_service(self, service_name: str) -> Any:
        return self.get_target_object(service_name, RegistryCategory.SERVICE)

    # -- Capability Discovery & Lookup -------------------------------------

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Optional[Callable[..., Any]] = None,
        parameters_schema: Optional[Dict[str, Any]] = None,
        returns_schema: Optional[Dict[str, Any]] = None,
        required_permissions: Optional[List[str]] = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
    ) -> CapabilityDescriptor:
        """Register an AI-discoverable capability.

        `requires_authentication=False` is a bootstrap carve-out reserved
        exclusively for `kortex.security.auth.authenticate` — the one
        capability that must be reachable before any session token exists.
        Any other capability name attempting to register with
        `requires_authentication=False` is rejected here: this invariant
        was not enforced anywhere in the platform before this milestone, so
        it is enforced at this single, authoritative capability-registration
        choke point rather than left as an unenforced convention.
        """
        if name in self._capabilities:
            raise ResourceAlreadyExistsError(f"Capability '{name}' is already registered.")

        if not requires_authentication and name != _BOOTSTRAP_EXEMPT_CAPABILITY:
            raise ValueError(
                f"Capability '{name}' cannot register with requires_authentication=False; "
                f"only '{_BOOTSTRAP_EXEMPT_CAPABILITY}' may bypass authentication."
            )

        descriptor = CapabilityDescriptor(
            name=name,
            description=description,
            provider=provider,
            parameters_schema=parameters_schema or {},
            returns_schema=returns_schema or {},
            required_permissions=required_permissions,
            requires_authentication=requires_authentication,
            security_classification=security_classification,
        )
        self._capabilities[name] = descriptor
        self._capability_handlers[name] = handler
        self.register_resource(name, RegistryCategory.CAPABILITY, handler, description=description, provider=provider)
        self.logger.info("Registered Capability: '%s' (Provider: %s)", name, provider)
        return descriptor

    def get_capability(self, name: str) -> CapabilityDescriptor:
        """Fetch capability descriptor by name.

        The returned `CapabilityDescriptor` never contains, references, or
        otherwise provides execution access to the real capability handler
        (Milestone M8) — it is pure introspection metadata. The only
        sanctioned production execution path remains
        `Kernel.invoke_capability()`.
        """
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        return self._capabilities[name]

    def list_capabilities(self) -> List[CapabilityDescriptor]:
        """List all discoverable capabilities."""
        return list(self._capabilities.values())

    def _resolve_handler(self, name: str) -> Optional[Callable[..., Any]]:
        """Internal, dispatcher-only handler resolution (Milestone M8).

        NOT part of the public contract — not exposed on `Kernel`, not
        intended for any caller other than
        `kortex.core.dispatch.CapabilityDispatcher._invoke_handler`, reached
        via `Kernel`'s own already-private `_registry_engine` attribute
        (the dispatcher lives inside the same trust boundary as `Kernel`
        itself; this is not a new public API). Deliberately never returns
        anything derived from a caller-supplied `CapabilityDescriptor`.
        """
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        return self._capability_handlers[name]

    def get_raw_handler_for_testing(self, name: str) -> Optional[Callable[..., Any]]:
        """TEST-ONLY accessor for a capability's raw handler (Milestone M8).

        Exists solely so existing unit/integration tests that deliberately
        exercise a capability handler in isolation — without full Kernel
        dispatch machinery — keep working after `CapabilityDescriptor.handler`
        was removed. Bypasses authentication, RBAC, ABAC, tenant, and
        classification enforcement entirely, exactly like the pre-M8
        `descriptor.handler` did.

        Production code must never call this. Doing so reintroduces the
        exact bypass this milestone closed.
        """
        return self._resolve_handler(name)

    def set_raw_handler_for_testing(self, name: str, handler: Callable[..., Any]) -> None:
        """TEST-ONLY: substitute a registered capability's handler (Milestone M8).

        Preserves the existing test pattern of swapping a capability's
        handler for a spy/counting handler while the capability's real
        metadata (`required_permissions`, `requires_authentication`,
        `security_classification`) and the full `Kernel.invoke_capability`
        enforcement path remain exactly as registered — the substituted
        handler still only runs after authentication/RBAC/ABAC succeed.

        Production code must never call this.
        """
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        self._capability_handlers[name] = handler
