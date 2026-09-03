"""
KORTEX Central Kernel Runtime.

The Kernel is the central orchestrator responsible for lifecycle management, engine loading,
event dispatching, service discovery, capability registration, and dependency resolution.

Design Principle: The Kernel MUST NOT contain business logic.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from kortex.core.outbox import OutboxStore

from kortex.core.base_engine import BaseEngine
from kortex.core.container import Container
from kortex.core.db import DatabaseEngineManager
from kortex.core.dispatch import CapabilityDispatcher, CapabilityRequest
from kortex.core.exceptions import KernelStateError, ResourceAlreadyExistsError, ResourceNotFoundError
from kortex.engines.boot.engine import BootEngine
from kortex.engines.configuration.engine import ConfigurationEngine
from kortex.engines.event.engine import EventDeliveryResult, EventEngine, EventPriority
from kortex.engines.registry.engine import CapabilityDescriptor, RegistryEngine, ResourceMetadata

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
        self._engines: dict[str, BaseEngine] = {}

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

        # Capability enforcement boundary — a plain coordinating object, not
        # a BaseEngine, not lifecycle-managed. See `kortex.core.dispatch`.
        self._dispatcher = CapabilityDispatcher(self)
        self._outbox_store: OutboxStore | None = None

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

    @property
    def outbox(self) -> OutboxStore:
        """Access the Transactional Outbox store (Milestone M5.2)."""
        if self._outbox_store is None:
            from kortex.core.outbox import OutboxStore
            from kortex.engines.storage.stores.data_store import RelationalDataStore

            self._outbox_store = OutboxStore(RelationalDataStore(self._db_manager))
        return self._outbox_store

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

    def get_all_engines(self) -> dict[str, BaseEngine]:
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
                payload={
                    "version": self._config_engine.settings.version,
                    "environment": self._config_engine.settings.environment,
                },
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

    async def health_check(self) -> dict[str, Any]:
        """Perform system-wide health checks.

        `bootstrap_required` (Milestone M7.1) rides along on this existing,
        already-unauthenticated `/health` surface rather than becoming a new
        capability — the desktop app must be able to tell "no principal
        exists yet, offer first-run setup" apart from "backend still
        starting" before any session token can possibly exist, which is
        exactly what `/health` is already for. Best-effort: if the Security
        Engine isn't registered/ready yet (a transient state during boot,
        not the steady-state this flag matters for), this defaults to
        `False` rather than raising — an unreachable/booting backend is
        already fully described by the surrounding health report, and a
        client should not offer first-run setup while it cannot even be sure
        one is warranted.
        """
        reports = await self._boot_engine.run_system_health_checks(self)
        bootstrap_required = False
        try:
            security_engine: Any = self.get_engine("security")
            if security_engine is not None and security_engine.state.value in ("READY", "RUNNING"):
                bootstrap_required = await security_engine.is_bootstrap_required()
        except Exception as exc:
            self._logger.debug("Bootstrap-required check unavailable: %s", exc)
        return {
            "kernel_state": self._state.value,
            "db_dialect": self._db_manager.dialect.value,
            "db_connected": self._db_manager.is_connected,
            "system_health": reports,
            "bootstrap_required": bootstrap_required,
        }

    # -- Event Delegation APIs ----------------------------------------------

    async def publish_event(
        self,
        topic: str,
        payload: dict[str, Any] | None = None,
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
        handler: Callable[..., Any] | None = None,
        parameters_schema: dict[str, Any] | None = None,
        returns_schema: dict[str, Any] | None = None,
        required_permissions: list[str] | None = None,
        requires_authentication: bool = True,
        security_classification: str = "INTERNAL",
        requires_execution_context: bool = False,
        legacy_principal_bridge: bool = False,
    ) -> CapabilityDescriptor:
        """Register a capability with the Registry Engine.

        Only permitted while the Kernel is at `CREATED` or `BOOTING`.
        Capability registration happens inside each engine's `initialize()`,
        which itself runs while the Kernel is `BOOTING` (see `boot()` below)
        — so this gate must allow both states, unlike `register_engine`,
        which only ever runs before `boot()` starts and therefore only
        needs to allow `CREATED`.
        """
        if self._state not in (KernelState.CREATED, KernelState.BOOTING):
            raise KernelStateError(
                f"Cannot register capability '{name}' after Kernel boot sequence has completed "
                f"(state={self._state.value})."
            )
        return self._registry_engine.register_capability(
            name=name,
            description=description,
            provider=provider,
            handler=handler,
            parameters_schema=parameters_schema,
            returns_schema=returns_schema,
            required_permissions=required_permissions,
            requires_authentication=requires_authentication,
            security_classification=security_classification,
            requires_execution_context=requires_execution_context,
            legacy_principal_bridge=legacy_principal_bridge,
        )

    def get_capability(self, name: str) -> CapabilityDescriptor:
        """Look up a capability descriptor."""
        return self._registry_engine.get_capability(name)

    async def invoke_capability(self, request: CapabilityRequest) -> Any:
        """Sanctioned execution path for a capability request.

        Resolves the capability, authenticates and authorizes the caller
        against the resolved `CapabilityDescriptor`'s own metadata (never
        from `request` itself), and only then invokes the handler. Decision
        logic lives entirely in Security Engine's unmodified
        `AuthenticationManager`/`AuthorizationEngine` — this method only
        coordinates calling them in order, consistent with the Kernel's
        "execution coordination, no business logic" mandate (see module
        docstring).
        """
        return await self._dispatcher.dispatch(request)

    def list_capabilities(self) -> list[CapabilityDescriptor]:
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
