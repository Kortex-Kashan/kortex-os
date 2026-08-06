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
    handler: Optional[Any] = Field(default=None, exclude=True, description="Callable execution reference")


class RegistryEngine(BaseEngine):
    """Central Capability and System Registry Engine."""

    def __init__(self) -> None:
        super().__init__()
        self._stores: Dict[RegistryCategory, Dict[str, ResourceMetadata]] = {
            cat: {} for cat in RegistryCategory
        }
        self._handlers: Dict[str, Any] = {}
        self._capabilities: Dict[str, CapabilityDescriptor] = {}

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
    ) -> CapabilityDescriptor:
        """Register an AI-discoverable capability."""
        if name in self._capabilities:
            raise ResourceAlreadyExistsError(f"Capability '{name}' is already registered.")

        descriptor = CapabilityDescriptor(
            name=name,
            description=description,
            provider=provider,
            parameters_schema=parameters_schema or {},
            returns_schema=returns_schema or {},
            handler=handler,
        )
        self._capabilities[name] = descriptor
        self.register_resource(name, RegistryCategory.CAPABILITY, handler, description=description, provider=provider)
        self.logger.info("Registered Capability: '%s' (Provider: %s)", name, provider)
        return descriptor

    def get_capability(self, name: str) -> CapabilityDescriptor:
        """Fetch capability descriptor by name."""
        if name not in self._capabilities:
            raise CapabilityNotFoundError(f"Capability '{name}' not found in registry.")
        return self._capabilities[name]

    def list_capabilities(self) -> List[CapabilityDescriptor]:
        """List all discoverable capabilities."""
        return list(self._capabilities.values())
