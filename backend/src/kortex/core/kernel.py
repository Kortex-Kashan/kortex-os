"""
KORTEX Central Kernel Runtime.

The Kernel is the central orchestrator responsible for lifecycle management, engine loading,
event dispatching, service discovery, capability registration, and dependency resolution.

Design Principle: The Kernel MUST NOT contain business logic.
"""

from __future__ import annotations

import enum
import logging
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.core.container import Container
from kortex.core.db import DatabaseEngineManager
from kortex.core.exceptions import KernelStateError, ResourceAlreadyExistsError, ResourceNotFoundError
from kortex.engines.boot.engine import BootEngine
from kortex.engines.configuration.engine import ConfigurationEngine
from kortex.engines.event.engine import EventDeliveryResult, EventEngine, EventPriority
from kortex.engines.registry.engine import CapabilityDescriptor, RegistryCategory, RegistryEngine, ResourceMetadata

T = TypeVar("T")

logger = logging.getLogger("kortex.core.kernel")


class KernelState(str, enum.Enum):
    """Lifecycle state of the KORTEX Kernel."""

    CREATED = "CREATED"
    BOOTING = "BOOTING"
    RUNNING = "RUNNING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class Kernel:
    """Central KORTEX Microkernel Runtime."""

    def __init__(self) -> None:
        self._state = KernelState.CREATED
        self._logger = logger
        self._container = Container()

        # Core Foundation Engines
        self._config_engine = ConfigurationEngine()
        self._registry_engine = RegistryEngine()
        self._event_engine = EventEngine()
        self._boot_engine = BootEngine()
        self._db_manager = DatabaseEngineManager()

        # Register core engines into internal engine dictionary
        self._engines: Dict[str, BaseEngine] = {}

        # Register the 4 foundational engines
        self._register_core_engine(self._config_engine)
        self._register_core_engine(self._registry_engine)
        self._register_core_engine(self._event_engine)
        self._register_core_engine(self._boot_engine)

        # Register container bindings
        self._container.register_instance("kernel", self)
        self._container.register_instance("container", self._container)
        self._container.register_type(Kernel, self)
        self._container.register_instance("db", self._db_manager)

    @property
    def state(self) -> KernelState:
        """Current Kernel runtime state."""
        return self._state

    @property
    def container(self) -> Container:
        """Access the IoC Dependency Injection Container."""
        return self._container

    @property
    def db(self) -> DatabaseEngineManager:
        """Access the database persistence manager."""
        return self._db_manager

    # -- Internal Engine Helper ---------------------------------------------

    def _register_core_engine(self, engine: BaseEngine) -> None:
        name = engine.name
        self._engines[name] = engine
        self._registry_engine.register_engine(name, engine, description=f"KORTEX {name.title()} Engine")
        self._container.register_instance(f"engine.{name}", engine)
        self._container.register_type(type(engine), engine)

    # -- Engine Lifecycle Management -----------------------------------------

    def register_engine(self, engine: BaseEngine) -> None:
        """Register a custom System Engine with the Kernel before boot."""
        if self._state != KernelState.CREATED:
            raise KernelStateError("Cannot register new engines after Kernel boot sequence has started.")

        name = engine.name
        if name in self._engines:
            raise ResourceAlreadyExistsError(f"Engine '{name}' is already registered in Kernel.")

        self._engines[name] = engine
        self._registry_engine.register_engine(name, engine, description=f"KORTEX {name.title()} Engine")
        self._container.register_instance(f"engine.{name}", engine)
        self._container.register_type(type(engine), engine)
        self._logger.info("Registered engine: '%s'", name)

    def get_engine(self, engine_name: str) -> BaseEngine:
        """Fetch a registered System Engine instance by name."""
        if engine_name not in self._engines:
            raise ResourceNotFoundError(f"Engine '{engine_name}' is not registered in Kernel.")
        return self._engines[engine_name]

    def get_all_engines(self) -> Dict[str, BaseEngine]:
        """Return dictionary of all registered engines."""
        return dict(self._engines)

    # -- Boot & Lifecycle Orchestration --------------------------------------

    async def boot(self) -> None:
        """Boot the KORTEX Kernel runtime and all registered system engines."""
        if self._state != KernelState.CREATED:
            raise KernelStateError(f"Kernel.boot() called in invalid state: {self._state}")

        self._state = KernelState.BOOTING
        self._logger.info("Initializing KORTEX Kernel Runtime boot sequence...")

        try:
            # Initialize database persistence layer
            await self._db_manager.connect()
            await self._db_manager.create_all_tables()

            # Execute boot engine sequence
            await self._boot_engine.boot_system(self)

            self._state = KernelState.RUNNING
            self._logger.info("KORTEX Kernel Runtime is RUNNING.")

            # Publish system startup event
            await self.publish_event(
                "system.started",
                payload={"version": self._config_engine.settings.version, "environment": self._config_engine.settings.environment},
                sender="kernel",
            )
        except Exception as e:
            self._state = KernelState.FAILED
            self._logger.critical("Kernel boot failed: %s", e, exc_info=True)
            raise

    async def shutdown(self) -> None:
        """Gracefully shut down the KORTEX Kernel runtime and all registered engines."""
        if self._state not in (KernelState.RUNNING, KernelState.BOOTING, KernelState.FAILED):
            return

        self._state = KernelState.SHUTTING_DOWN
        self._logger.info("Shutting down KORTEX Kernel Runtime...")

        # Publish system stopping event
        try:
            await self.publish_event("system.stopping", payload={}, sender="kernel")
        except Exception as e:
            self._logger.warning("Failed to publish system.stopping event during shutdown: %s", e)

        # Reverse shutdown engines
        await self._boot_engine.shutdown_system(self)

        # Disconnect database
        await self._db_manager.disconnect()

        self._state = KernelState.STOPPED
        self._logger.info("KORTEX Kernel Runtime stopped cleanly.")

    async def health_check(self) -> Dict[str, Any]:
        """Perform system-wide health checks."""
        reports = await self._boot_engine.run_system_health_checks(self)
        return {
            "kernel_state": self._state.value,
            "db_dialect": self._db_manager.dialect.value,
            "db_connected": self._db_manager.is_connected,
            "system_health": reports,
        }

    # -- Event Delegation APIs ----------------------------------------------

    async def publish_event(
        self,
        topic: str,
        payload: Optional[Dict[str, Any]] = None,
        sender: str = "system",
        priority: EventPriority = EventPriority.NORMAL,
    ) -> EventDeliveryResult:
        """Publish an asynchronous event via the Kernel Event Engine."""
        return await self._event_engine.publish(
            topic=topic,
            payload=payload,
            sender=sender,
            priority=priority,
        )

    def subscribe_event(
        self,
        topic: str,
        handler: Callable[..., Any],
        priority: EventPriority = EventPriority.NORMAL,
        subscriber_name: str = "anonymous",
    ) -> str:
        """Subscribe to an event topic via the Kernel Event Engine."""
        return self._event_engine.subscribe(
            topic=topic,
            handler=handler,
            priority=priority,
            subscriber_name=subscriber_name,
        )

    def unsubscribe_event(self, subscription_id: str) -> bool:
        """Unsubscribe an event handler."""
        return self._event_engine.unsubscribe(subscription_id)

    # -- Service Discovery & Capability Lookup APIs --------------------------

    def register_capability(
        self,
        name: str,
        description: str,
        provider: str,
        handler: Optional[Callable[..., Any]] = None,
        parameters_schema: Optional[Dict[str, Any]] = None,
        returns_schema: Optional[Dict[str, Any]] = None,
    ) -> CapabilityDescriptor:
        """Register a capability with the Registry Engine."""
        return self._registry_engine.register_capability(
            name=name,
            description=description,
            provider=provider,
            handler=handler,
            parameters_schema=parameters_schema,
            returns_schema=returns_schema,
        )

    def get_capability(self, name: str) -> CapabilityDescriptor:
        """Look up a capability descriptor."""
        return self._registry_engine.get_capability(name)

    def list_capabilities(self) -> List[CapabilityDescriptor]:
        """List all registered capabilities."""
        return self._registry_engine.list_capabilities()

    def register_module(self, name: str, instance: Any, description: str = "") -> ResourceMetadata:
        """Register a business module."""
        return self._registry_engine.register_module(name, instance, description=description)

    def get_module(self, name: str) -> Any:
        """Look up a business module."""
        return self._registry_engine.get_module(name)

    def register_connector(self, name: str, instance: Any, description: str = "") -> ResourceMetadata:
        """Register a connector."""
        return self._registry_engine.register_connector(name, instance, description=description)

    def get_connector(self, name: str) -> Any:
        """Look up a connector."""
        return self._registry_engine.get_connector(name)

    def register_recipe(self, name: str, recipe_def: Any, description: str = "") -> ResourceMetadata:
        """Register a recipe."""
        return self._registry_engine.register_recipe(name, recipe_def, description=description)

    def get_recipe(self, name: str) -> Any:
        """Look up a recipe."""
        return self._registry_engine.get_recipe(name)

    def register_template(self, name: str, template_def: Any, description: str = "") -> ResourceMetadata:
        """Register a template."""
        return self._registry_engine.register_template(name, template_def, description=description)

    def get_template(self, name: str) -> Any:
        """Look up a template."""
        return self._registry_engine.get_template(name)

    # -- Configuration Delegation APIs -------------------------------------

    def get_config(self, key: str, default: Any = None) -> Any:
        """Fetch a configuration value."""
        return self._config_engine.get(key, default=default)

    def set_config(self, key: str, value: Any) -> None:
        """Set a runtime configuration value."""
        self._config_engine.set(key, value)
