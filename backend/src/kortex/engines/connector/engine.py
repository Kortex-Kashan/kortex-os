"""KORTEX OS Connector Engine Core Facade Implementation.

This module implements ConnectorEngine, extending BaseEngine and conforming to IEngineDiagnostics.
It serves as the public entry point orchestrating ConnectorDriverRegistry, ConnectorProfileManager,
TokenBucketRateLimiter, ConnectorPipeline, ConnectorDiagnostics, and system events.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.connector.diagnostics import ConnectorDiagnostics
from kortex.engines.connector.events import (
    ConnectorActionCompletedEvent,
    ConnectorActionFailedEvent,
    ConnectorActionStartedEvent,
    ConnectorBaseEvent,
    ConnectorDriverRegisteredEvent,
)
from kortex.engines.connector.interfaces import (
    IBaseConnectorDriver,
    IEngineDiagnostics,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorProfile,
    DriverMetadata,
)
from kortex.engines.connector.pipeline import ConnectorPipeline
from kortex.engines.connector.profiles import ConnectorProfileManager
from kortex.engines.connector.rate_limiter import TokenBucketRateLimiter
from kortex.engines.connector.registry import ConnectorDriverRegistry

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

logger = logging.getLogger("kortex.engines.connector")


class ConnectorEngine(BaseEngine, IEngineDiagnostics):
    """Core runtime facade and orchestrator for KORTEX OS Connector Engine."""

    def __init__(
        self,
        registry: ConnectorDriverRegistry | None = None,
        profile_manager: ConnectorProfileManager | None = None,
        rate_limiter: TokenBucketRateLimiter | None = None,
        pipeline: ConnectorPipeline | None = None,
        secret_resolver: Callable[[str], Awaitable[str]] | None = None,
        diagnostics: ConnectorDiagnostics | None = None,
    ) -> None:
        """Initialize ConnectorEngine with component dependencies.

        Args:
            registry: Optional ConnectorDriverRegistry instance.
            profile_manager: Optional ConnectorProfileManager instance.
            rate_limiter: Optional TokenBucketRateLimiter instance.
            pipeline: Optional ConnectorPipeline instance.
            secret_resolver: Optional credential resolution async callback.
            diagnostics: Optional ConnectorDiagnostics instance.
        """
        super().__init__()
        self._registry = registry if registry is not None else ConnectorDriverRegistry()
        self._profile_manager = (
            profile_manager if profile_manager is not None else ConnectorProfileManager()
        )
        self._rate_limiter = (
            rate_limiter if rate_limiter is not None else TokenBucketRateLimiter()
        )
        self._secret_resolver = secret_resolver
        self._pipeline = (
            pipeline
            if pipeline is not None
            else ConnectorPipeline(
                registry=self._registry,
                rate_limiter=self._rate_limiter,
                secret_resolver=self._secret_resolver,
            )
        )
        self._diagnostics = (
            diagnostics
            if diagnostics is not None
            else ConnectorDiagnostics(
                registry=self._registry,
                profile_manager=self._profile_manager,
                rate_limiter=self._rate_limiter,
            )
        )
        self._kernel: Kernel | None = None

    @property
    def name(self) -> str:
        """Unique engine identifier name (BaseEngine abstract property)."""
        return "connector"

    @property
    def dependencies(self) -> list[str]:
        """Prerequisite foundation engines for Kernel boot sequence."""
        return ["configuration", "registry", "event", "storage"]

    @property
    def registry(self) -> ConnectorDriverRegistry:
        """Access the driver registry subsystem."""
        return self._registry

    @property
    def profile_manager(self) -> ConnectorProfileManager:
        """Access the profile manager subsystem."""
        return self._profile_manager

    @property
    def rate_limiter(self) -> TokenBucketRateLimiter:
        """Access the rate limiter subsystem."""
        return self._rate_limiter

    @property
    def pipeline(self) -> ConnectorPipeline:
        """Access the connector execution pipeline."""
        return self._pipeline

    # -- BaseEngine Lifecycle Implementations ---------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Initialize engine resources and register capabilities with Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Connector Engine...")

        try:
            self._kernel = kernel

            # Register canonical Kernel capabilities
            kernel.register_capability(
                name="kortex.connector.action.execute",
                description="Execute an action against an external integration via profile",
                provider=self.name,
                handler=self.execute_action,
            )
            kernel.register_capability(
                name="kortex.connector.driver.register",
                description="Register a connector driver in the engine registry",
                provider=self.name,
                handler=self.register_driver,
            )
            kernel.register_capability(
                name="kortex.connector.driver.list",
                description="List metadata of all registered connector drivers",
                provider=self.name,
                handler=self.list_drivers,
            )
            kernel.register_capability(
                name="kortex.connector.profile.get",
                description="Retrieve a connector profile by ID",
                provider=self.name,
                handler=self.get_profile,
            )

            self._set_state(EngineState.READY)
            self.logger.info("Connector Engine initialized successfully.")
        except Exception as e:
            self._set_state(EngineState.FAILED)
            self.logger.error("Failed to initialize Connector Engine: %s", e, exc_info=True)
            raise

    async def start(self) -> None:
        """Start active background services."""
        self.ensure_state(EngineState.READY, EngineState.STOPPED)
        self._set_state(EngineState.RUNNING)
        self.logger.info("Connector Engine is RUNNING.")

    async def health_check(self) -> dict[str, Any]:
        """Return diagnostic health information (BaseEngine async contract)."""
        return self._diagnostics.health()

    async def stop(self) -> None:
        """Gracefully shut down active background tasks and release resources."""
        self.ensure_state(EngineState.RUNNING, EngineState.READY)
        self._set_state(EngineState.STOPPING)
        self._set_state(EngineState.STOPPED)
        self.logger.info("Connector Engine stopped.")

    # -- Diagnostics Delegation (IEngineDiagnostics Protocol) ----------------

    def health(self) -> dict[str, Any]:
        """Return operational health status and component checks."""
        return self._diagnostics.health()

    def metrics(self) -> dict[str, Any]:
        """Return runtime performance and state metrics."""
        return self._diagnostics.metrics()

    def diagnostics(self) -> dict[str, Any]:
        """Return detailed technical diagnostics and capability lists."""
        return self._diagnostics.diagnostics()

    def status(self) -> str:
        """Return current engine state name string."""
        return self._state.value

    def version(self) -> str:
        """Return engine semantic version string."""
        return "1.0.0"

    def capabilities(self) -> list[str]:
        """Return canonical capability strings declared by the engine."""
        return self._diagnostics.capabilities()

    # -- Engine Facade Capability Handlers ----------------------------------

    async def execute_action(self, request: ActionRequest) -> ActionResult:
        """Execute an action against an external driver via profile and pipeline."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)

        action_type_str = (
            request.action_type.value if hasattr(request.action_type, "value") else str(request.action_type)
        )

        # 1. Emit action started event
        started_evt = ConnectorActionStartedEvent(
            request_id=request.request_id,
            profile_id=request.profile_id,
            action_type=action_type_str,
            correlation_id=request.correlation_id,
        )
        await self._publish_event(started_evt)

        start_time = time.perf_counter()

        try:
            # 2. Resolve profile
            profile = await self._profile_manager.get_profile(request.profile_id)

            # 3. Execute action through pipeline
            result = await self._pipeline.execute(
                request=request,
                profile=profile,
            )

            # 4. Emit completed or failed event based on pipeline result status
            exec_time_ms = (time.perf_counter() - start_time) * 1000.0
            if result.status == "SUCCESS":
                completed_evt = ConnectorActionCompletedEvent(
                    request_id=request.request_id,
                    profile_id=request.profile_id,
                    action_type=action_type_str,
                    status=result.status,
                    execution_time_ms=result.execution_time_ms or exec_time_ms,
                    correlation_id=request.correlation_id,
                )
                await self._publish_event(completed_evt)
            else:
                err_msg = "Action execution failed"
                if result.error_details and isinstance(result.error_details, dict):
                    err_msg = result.error_details.get("error", err_msg)
                failed_evt = ConnectorActionFailedEvent(
                    request_id=request.request_id,
                    profile_id=request.profile_id,
                    action_type=action_type_str,
                    error_message=err_msg,
                    execution_time_ms=result.execution_time_ms or exec_time_ms,
                    correlation_id=request.correlation_id,
                )
                await self._publish_event(failed_evt)

            return result

        except Exception as err:
            exec_time_ms = (time.perf_counter() - start_time) * 1000.0
            failed_evt = ConnectorActionFailedEvent(
                request_id=request.request_id,
                profile_id=request.profile_id,
                action_type=action_type_str,
                error_message="Connector action execution error",
                execution_time_ms=exec_time_ms,
                correlation_id=request.correlation_id,
            )
            await self._publish_event(failed_evt)
            raise

    def register_driver(self, driver: IBaseConnectorDriver) -> None:
        """Register a connector driver in the engine registry."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        self._registry.register_driver(driver)

        meta = driver.metadata
        evt = ConnectorDriverRegisteredEvent(
            driver_id=meta.driver_id,
            driver_name=meta.display_name,
            version=meta.version,
            supported_actions=tuple(
                act.value if hasattr(act, "value") else str(act) for act in meta.supported_actions
            ),
        )
        self._safe_schedule_event(evt)

    def list_drivers(self) -> list[DriverMetadata]:
        """List metadata of all registered connector drivers."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        return self._registry.list_drivers()

    async def get_profile(self, profile_id: str) -> ConnectorProfile:
        """Retrieve a connector profile by ID."""
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        return await self._profile_manager.get_profile(profile_id)

    # -- Internal Helper Methods --------------------------------------------

    async def _publish_event(self, event: ConnectorBaseEvent) -> None:
        """Asynchronously publish a system event through Kernel Event Engine with error isolation."""
        if self._kernel is not None:
            try:
                await self._kernel.publish_event(
                    topic=event.event_type,
                    payload=event.model_dump(),
                    sender=self.name,
                )
            except Exception:
                self.logger.warning(
                    "Failed to publish system event '%s'.",
                    event.event_type,
                )

    def _safe_schedule_event(self, event: ConnectorBaseEvent) -> None:
        """Safely schedule event publication from a synchronous context if an active loop exists."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._publish_event(event))
        except RuntimeError:
            self.logger.debug(
                "No active event loop running to dispatch event '%s'", event.event_type
            )


__all__ = ["ConnectorEngine"]
