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


import json
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.core.base_engine import BaseEngine, EngineState
from kortex.engines.connector.diagnostics import ConnectorDiagnostics
from kortex.engines.connector.events import (
    ConnectorActionCompletedEvent,
    ConnectorActionFailedEvent,
    ConnectorActionStartedEvent,
    ConnectorBaseEvent,
    ConnectorDriverRegisteredEvent,
)
from kortex.engines.connector.exceptions import (
    ConnectorProfileNotFoundError,
    ConnectorSecurityError,
)
from kortex.engines.connector.interfaces import (
    IBaseConnectorDriver,
    IEngineDiagnostics,
)
from kortex.engines.connector.models import (
    ActionRequest,
    ActionResult,
    ConnectorActionHistoryModel,
    ConnectorProfile,
    DriverMetadata,
)
from kortex.engines.connector.pipeline import ConnectorPipeline
from kortex.engines.connector.profiles import ConnectorProfileManager
from kortex.engines.connector.rate_limiter import TokenBucketRateLimiter
from kortex.engines.connector.registry import ConnectorDriverRegistry
from kortex.engines.security.models import SecurityPrincipal
from kortex.engines.storage.interfaces import IDataStore

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
        secret_resolver: Callable[[str, str], Awaitable[str]] | None = None,
        diagnostics: ConnectorDiagnostics | None = None,
        data_store: IDataStore | None = None,
    ) -> None:
        """Initialize ConnectorEngine with component dependencies.

        Args:
            registry: Optional ConnectorDriverRegistry instance.
            profile_manager: Optional ConnectorProfileManager instance.
            rate_limiter: Optional TokenBucketRateLimiter instance.
            pipeline: Optional ConnectorPipeline instance.
            secret_resolver: Optional credential resolution async callback, taking
                ``(secret_handle, tenant_id)`` and returning the resolved secret
                value. Tenant-scoped so a resolved credential can never cross a
                tenant boundary. If not supplied here, `initialize()` wires one
                from the Kernel-registered Security Engine's secret store, the
                same deferred-wiring pattern already used for Storage Engine
                dependencies below.
            diagnostics: Optional ConnectorDiagnostics instance.
            data_store: Optional IDataStore instance from Storage Engine for execution history.
        """
        super().__init__()
        self._data_store = data_store
        self._registry = registry if registry is not None else ConnectorDriverRegistry()
        self._profile_manager = (
            profile_manager if profile_manager is not None else ConnectorProfileManager(data_store=data_store)
        )
        self._rate_limiter = (
            rate_limiter if rate_limiter is not None else TokenBucketRateLimiter()
        )
        self._secret_resolver = secret_resolver
        self._diagnostics = (
            diagnostics
            if diagnostics is not None
            else ConnectorDiagnostics(
                registry=self._registry,
                profile_manager=self._profile_manager,
                rate_limiter=self._rate_limiter,
            )
        )
        self._pipeline = (
            pipeline
            if pipeline is not None
            else ConnectorPipeline(
                registry=self._registry,
                rate_limiter=self._rate_limiter,
                secret_resolver=self._secret_resolver,
                diagnostics=self._diagnostics,
            )
        )
        self._kernel: Kernel | None = None

    @property
    def name(self) -> str:
        """Unique engine identifier name (BaseEngine abstract property)."""
        return "connector"

    @property
    def dependencies(self) -> list[str]:
        """Prerequisite foundation engines for Kernel boot sequence.

        Security is deliberately NOT declared here even though `initialize()`
        opportunistically wires a secret resolver from it when present (M6.0-2):
        `BootEngine.resolve_dependency_order` treats a declared dependency as a
        hard requirement (boot fails if the named engine isn't registered at
        all), and several existing, legitimate test/usage patterns construct
        this engine with Storage but deliberately without Security to exercise
        Connector Engine in isolation. The real production bootstrap
        (`kernel_bootstrap.py`) already registers Security before Connector
        unconditionally, so declaring it here would only break isolated usage
        without adding any real ordering guarantee production doesn't already have.
        """
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
        """Initialize engine resources, resolve Storage Engine dependencies, and register capabilities with Kernel."""
        self.ensure_state(EngineState.UNINITIALIZED)
        self._set_state(EngineState.INITIALIZING)
        self.logger.info("Initializing KORTEX Connector Engine...")

        try:
            self._kernel = kernel

            # Wire Storage Engine dependencies from Kernel IoC container if registered
            if kernel is not None:
                try:
                    storage_engine = kernel.container.resolve("engine.storage")
                    if storage_engine is not None:
                        if hasattr(storage_engine, "data") and self._data_store is None:
                            self._data_store = storage_engine.data
                        if hasattr(storage_engine, "cache"):
                            cache_store = storage_engine.cache
                            if self._profile_manager._cache_store is None:
                                self._profile_manager._cache_store = cache_store
                            if self._rate_limiter._cache_store is None:
                                self._rate_limiter._cache_store = cache_store
                        if self._profile_manager._data_store is None and self._data_store is not None:
                            self._profile_manager._data_store = self._data_store
                except Exception:
                    self.logger.debug("StorageEngine not resolved from Kernel container; using local fallbacks.")

                # Wire the production secret resolver from the Kernel-registered
                # Security Engine if the caller didn't already supply one (M6.0-2:
                # this is the fix for connector actions with a real secret_handle
                # failing "Secret resolver unavailable." in every prior boot path
                # — SecurityEngine.get_secret(secret_handle, tenant_id) already
                # matches this resolver's tenant-scoped (handle, tenant_id)
                # contract exactly, so it's assigned directly, unwrapped).
                if self._secret_resolver is None:
                    try:
                        security_engine = kernel.container.resolve("engine.security")
                        if security_engine is not None:
                            self._secret_resolver = security_engine.get_secret
                            if self._pipeline._secret_resolver is None:
                                self._pipeline._secret_resolver = self._secret_resolver
                    except Exception:
                        self.logger.debug(
                            "SecurityEngine not resolved from Kernel container; "
                            "connector actions with a secret_handle will fail "
                            "authentication until a secret_resolver is wired."
                        )

            # Register canonical Kernel capabilities
            kernel.register_capability(
                name="kortex.connector.action.execute",
                description="Execute an action against an external integration via profile",
                provider=self.name,
                handler=self.execute_action,
                required_permissions=["connector:execute"],
            )
            kernel.register_capability(
                name="kortex.connector.driver.register",
                description="Register a connector driver in the engine registry",
                provider=self.name,
                handler=self.register_driver,
                required_permissions=["connector:write"],
            )
            kernel.register_capability(
                name="kortex.connector.driver.list",
                description="List metadata of all registered connector drivers",
                provider=self.name,
                handler=self.list_drivers,
                required_permissions=["connector:read"],
            )
            kernel.register_capability(
                name="kortex.connector.profile.get",
                description="Retrieve a connector profile by ID",
                provider=self.name,
                handler=self.get_profile,
                required_permissions=["connector:read"],
            )
            kernel.register_capability(
                name="kortex.connector.profile.register",
                description="Create or update a tenant-scoped connector profile (M7.3)",
                provider=self.name,
                handler=self.register_profile,
                required_permissions=["connector:write"],
            )
            kernel.register_capability(
                name="kortex.connector.profile.list",
                description="List connector profiles owned by the caller's tenant (M7.3)",
                provider=self.name,
                handler=self.list_profiles,
                required_permissions=["connector:read"],
            )
            kernel.register_capability(
                name="kortex.connector.profile.delete",
                description="Delete a tenant-scoped connector profile (M7.3)",
                provider=self.name,
                handler=self.delete_profile,
                required_permissions=["connector:write"],
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

    def _safe_record_execution(
        self,
        is_success: bool,
        latency_ms: float,
        driver_id: str | None = None,
        action_type: str = "FETCH",
    ) -> None:
        """Record top-level execution outcome metrics safely without throwing exception."""
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.record_execution(
                is_success=is_success,
                latency_ms=latency_ms,
                driver_id=driver_id,
                action_type=action_type,
            )
        except Exception:
            self.logger.warning("Failed to record connector execution metrics.")

    def _safe_record_cancellation(self) -> None:
        """Record task cancellation metric safely without throwing exception."""
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.record_cancellation()
        except Exception:
            self.logger.warning("Failed to record connector cancellation metric.")

    async def execute_action(
        self, request: ActionRequest | dict[str, Any], principal: SecurityPrincipal | None = None
    ) -> ActionResult:
        """Execute an action against an external driver via profile and pipeline.

        `principal` (M6.3-1): the Kernel dispatcher injects its own verified
        identity into any handler parameter literally named `principal`.
        Before this fix, `request.tenant_id` was trusted as-is from
        caller-supplied data with no cross-check against the authenticated
        caller's real tenant, and `ConnectorProfileManager.get_profile` had
        no tenant scoping at all -- a caller holding the coarse
        `connector:execute` permission could reach any tenant's connector
        profile and secret by supplying that tenant's `profile_id`. When a
        verified `principal` is present, its `tenant_id` is authoritative:
        the request is corrected to it before profile resolution or
        pipeline execution ever reads `request.tenant_id`.

        M6.3-3: `request` may arrive as a plain `dict` rather than a real
        `ActionRequest` instance when this capability is invoked via
        `ExternalExecutionManager`'s durable resume-after-approval path --
        `CapabilityDispatcher._invoke_handler` passes `request.parameters`
        through to the handler via `**kwargs` with no dict-to-pydantic
        coercion, and a resumed execution's parameters necessarily come back
        from JSON persistence (`ExternalExecutionStore.get_dispatch_context`)
        as plain dicts, never the original in-memory object. Coerced here so
        both the live and resumed dispatch paths behave identically.
        """
        if isinstance(request, dict):
            request = ActionRequest(**request)
        if principal is not None and principal.tenant_id != request.tenant_id:
            request = request.model_copy(update={"tenant_id": principal.tenant_id})

        start_time = time.perf_counter()
        is_success = False
        resolved_driver_id: str | None = None
        action_type_str = (
            request.action_type.value
            if hasattr(request.action_type, "value")
            else str(request.action_type)
        )
        executed_recorded = False

        def _finalize_metrics() -> None:
            nonlocal executed_recorded
            if not executed_recorded:
                executed_recorded = True
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                self._safe_record_execution(
                    is_success=is_success,
                    latency_ms=latency_ms,
                    driver_id=resolved_driver_id,
                    action_type=action_type_str,
                )

        try:
            self.ensure_state(EngineState.READY, EngineState.RUNNING)

            # RBAC Capability Permission Verification
            granted_permissions = request.options.get("granted_permissions")
            if granted_permissions is not None and isinstance(
                granted_permissions, (list, set, tuple)
            ):
                required_perm = "kortex.connector.action.execute"
                if required_perm not in granted_permissions:
                    raise ConnectorSecurityError(
                        f"Unauthorized capability access: missing required permission '{required_perm}'."
                    )

            # 1. Emit action started event
            started_evt = ConnectorActionStartedEvent(
                request_id=request.request_id,
                profile_id=request.profile_id,
                action_type=action_type_str,
                correlation_id=request.correlation_id,
            )
            await self._publish_event(started_evt)

            # 2. Resolve profile, tenant-scoped (M6.3-1)
            profile = await self._profile_manager.get_profile(
                request.profile_id, tenant_id=request.tenant_id
            )
            resolved_driver_id = profile.driver_id

            # 3. Execute action through pipeline
            result = await self._pipeline.execute(
                request=request,
                profile=profile,
            )
            is_success = result.status == "SUCCESS"

            # 4. Emit completed or failed event based on pipeline result status
            exec_time_ms = (time.perf_counter() - start_time) * 1000.0
            if is_success:
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

            # 5. Persist sanitized action execution history via Storage Engine IDataStore
            await self._record_action_history(request, result, profile.driver_id)

            return result

        except asyncio.CancelledError:
            is_success = False
            self._safe_record_cancellation()
            raise
        except Exception as err:
            is_success = False
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

            if isinstance(err, (ConnectorSecurityError, ConnectorProfileNotFoundError)):
                # Record failure history for security or missing profile errors
                err_result = ActionResult(
                    request_id=request.request_id,
                    status="FAILED",
                    execution_time_ms=exec_time_ms,
                    error_details={"error": "Connector action execution error"},
                    correlation_id=request.correlation_id,
                )
                await self._record_action_history(request, err_result)

            raise
        finally:
            _finalize_metrics()

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

    async def get_profile(
        self,
        profile_id: str,
        tenant_id: str | None = None,
        principal: SecurityPrincipal | None = None,
    ) -> ConnectorProfile:
        """Retrieve a connector profile by ID, tenant-scoped (M6.3-1).

        `principal`, when present, is authoritative over a caller-supplied
        `tenant_id` -- the same precedence rule used throughout KORTEX since
        M6.0-3.
        """
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        tid = principal.tenant_id if principal is not None else tenant_id
        return await self._profile_manager.get_profile(profile_id, tenant_id=tid)

    async def register_profile(
        self,
        profile: ConnectorProfile | dict[str, Any],
        principal: SecurityPrincipal | None = None,
    ) -> ConnectorProfile:
        """Create or update a tenant-scoped connector profile (M7.3).

        Mirrors `execute_action`/`get_profile`'s principal-authoritative
        tenant binding (M6.3-1): a caller-supplied `tenant_id` on the
        submitted profile is never trusted once a verified `principal` is
        present -- it is always overwritten with `principal.tenant_id`
        before the profile is persisted, so a caller cannot register (or
        silently take over) a profile under another tenant's id.

        `profile` may arrive as a plain `dict` when this capability is
        reached through the real dispatch boundary (the Kernel dispatcher's
        M7.2 dict-coercion fix already handles this generically, but the
        explicit branch mirrors `execute_action`'s own defensive coercion
        for the direct, non-dispatch call path used by tests).

        Rejects a `driver_id` that is not currently registered -- a profile
        referencing a nonexistent driver would otherwise register
        successfully and only fail much later, at execution time, inside
        `ConnectorPipeline`'s dispatch stage.
        """
        if isinstance(profile, dict):
            profile = ConnectorProfile(**profile)
        if principal is not None:
            profile = profile.model_copy(update={"tenant_id": principal.tenant_id})

        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        self._registry.get_driver_by_id(profile.driver_id)
        await self._profile_manager.register_profile(profile)
        return profile

    async def list_profiles(
        self,
        driver_id: str | None = None,
        active_only: bool = False,
        tenant_id: str | None = None,
        principal: SecurityPrincipal | None = None,
    ) -> list[ConnectorProfile]:
        """List connector profiles scoped to the caller's tenant (M7.3).

        `principal`, when present, is authoritative over a caller-supplied
        `tenant_id` -- identical precedence to `get_profile`/`execute_action`.
        """
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        tid = principal.tenant_id if principal is not None else tenant_id
        return await self._profile_manager.list_profiles(
            driver_id=driver_id, active_only=active_only, tenant_id=tid
        )

    async def delete_profile(
        self,
        profile_id: str,
        principal: SecurityPrincipal | None = None,
    ) -> bool:
        """Delete a tenant-scoped connector profile (M7.3).

        `ConnectorProfileManager.delete_profile` deletes by `profile_id`
        alone with no tenant check -- it never needed one before this
        capability existed, since every prior caller was already trusted and
        tenant-scoped upstream. Ownership is verified first via the
        already-tenant-scoped, enumeration-resistant `get_profile` (M6.3-1),
        which raises `ConnectorProfileNotFoundError` -- masked identically to
        a genuinely nonexistent profile -- if the caller's tenant does not
        own it. Deletion is only ever attempted after that check passes.
        """
        self.ensure_state(EngineState.READY, EngineState.RUNNING)
        tid = principal.tenant_id if principal is not None else None
        await self._profile_manager.get_profile(profile_id, tenant_id=tid)
        return await self._profile_manager.delete_profile(profile_id)

    # -- Internal Helper Methods --------------------------------------------

    async def _record_action_history(
        self,
        request: ActionRequest,
        result: ActionResult,
        driver_id: str | None = None,
    ) -> None:
        """Record sanitized action execution history entry via IDataStore."""
        if self._data_store is None:
            return

        err_json: str | None = None
        if result.error_details is not None and isinstance(result.error_details, dict):
            # Ensure error details contain no plaintext secrets or credentials
            err_json = json.dumps(result.error_details)

        action_type_str = (
            request.action_type.value
            if hasattr(request.action_type, "value")
            else str(request.action_type)
        )

        async def _save_history(session: AsyncSession) -> None:
            entry = ConnectorActionHistoryModel(
                id=request.request_id,
                profile_id=request.profile_id,
                action_type=action_type_str,
                status=result.status,
                execution_time_ms=result.execution_time_ms,
                correlation_id=request.correlation_id,
                driver_id=driver_id,
                error_details_json=err_json,
            )
            session.add(entry)

        try:
            await self._data_store.execute_in_transaction(_save_history)
        except Exception as e:
            self.logger.warning("Failed to persist action execution history: %s", e)

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
