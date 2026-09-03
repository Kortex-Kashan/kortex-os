"""KORTEX Operations Business Module Facade (`OperationsModule`).

Implements Phase 6 canonical business capabilities for:
- Vehicle Tracking & Driver Assignment
- Chronological Odometer and Location Tracking Logs
- Incident Reporting, Classification, Investigation Resolution & Terminal Closure

Inherits from `kortex.core.base_module.BaseModule` and participates in the
established platform module lifecycle.
All capability handlers declare `execution_context: CapabilityExecutionContext`
and register with `requires_execution_context=True` and `requires_authentication=True`,
guaranteeing strict tenant isolation and authenticated identity propagation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kortex.core.base_module import BaseModule, ModuleState
from kortex.core.dispatch import CapabilityExecutionContext
from kortex.core.exceptions import KortexError
from kortex.engines.event.engine import EventEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.operations.manager import OperationsManager
from kortex.modules.operations.models import (
    AssignDriverRequest,
    CloseIncidentRequest,
    CreateVehicleRequest,
    GetVehicleTrackingHistoryRequest,
    IncidentResponse,
    ListIncidentsRequest,
    ListVehiclesRequest,
    RecordVehicleTrackingRequest,
    ReportIncidentRequest,
    ResolveIncidentRequest,
    UnassignDriverRequest,
    UpdateVehicleStatusRequest,
    VehicleResponse,
    VehicleTrackingRecordResponse,
)

if TYPE_CHECKING:
    from kortex.core.kernel import Kernel

_REGISTERED_CAPABILITIES: list[str] = [
    "kortex.operations.vehicle.create",
    "kortex.operations.vehicle.get",
    "kortex.operations.vehicle.list",
    "kortex.operations.vehicle.assign",
    "kortex.operations.vehicle.unassign",
    "kortex.operations.vehicle.status_update",
    "kortex.operations.vehicle.tracking_record",
    "kortex.operations.vehicle.tracking_history",
    "kortex.operations.incident.report",
    "kortex.operations.incident.get",
    "kortex.operations.incident.list",
    "kortex.operations.incident.resolve",
    "kortex.operations.incident.close",
]


class OperationsModule(BaseModule):
    """KORTEX Operations Business Module Facade."""

    def __init__(self) -> None:
        super().__init__()
        self._manager: OperationsManager | None = None

    @property
    def name(self) -> str:
        return "operations"

    @property
    def namespace(self) -> str:
        return "kortex.operations"

    @property
    def dependencies(self) -> list[str]:
        return ["storage", "security"]

    # -- Lifecycle Implementation ---------------------------------------------

    async def initialize(self, kernel: Kernel) -> None:
        """Resolve storage and security engines and register capabilities."""
        self.ensure_state(ModuleState.UNINITIALIZED)
        self._set_state(ModuleState.INITIALIZING)
        self.logger.info("Initializing KORTEX Operations Module...")

        try:
            storage_engine = kernel.get_engine("storage")
            data_store: IDataStore | None = getattr(storage_engine, "data", None)
            if data_store is None:
                raise KortexError("Storage Engine did not provide an IDataStore instance.")

            event_engine: EventEngine | None = None
            try:
                ev = kernel.get_engine("event")
                if isinstance(ev, EventEngine):
                    event_engine = ev
            except Exception:
                self.logger.debug("Event engine not available; events will not be emitted.")

            self._manager = OperationsManager(data_store=data_store, event_engine=event_engine)

            # 1. Vehicle capabilities (8)
            kernel.register_capability(
                name="kortex.operations.vehicle.create",
                description="Register a new vehicle into the fleet master.",
                provider=self.name,
                handler=self.create_vehicle,
                requires_authentication=True,
                required_permissions=["operations:vehicle:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.get",
                description="Retrieve vehicle details by ID.",
                provider=self.name,
                handler=self.get_vehicle,
                requires_authentication=True,
                required_permissions=["operations:vehicle:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.list",
                description="Query and paginate fleet vehicles with optional status filter.",
                provider=self.name,
                handler=self.list_vehicles,
                requires_authentication=True,
                required_permissions=["operations:vehicle:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.assign",
                description="Assign a designated driver to a vehicle.",
                provider=self.name,
                handler=self.assign_driver,
                requires_authentication=True,
                required_permissions=["operations:vehicle:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.unassign",
                description="Release driver assignment from a vehicle.",
                provider=self.name,
                handler=self.unassign_driver,
                requires_authentication=True,
                required_permissions=["operations:vehicle:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.status_update",
                description="Transition vehicle operational lifecycle status.",
                provider=self.name,
                handler=self.update_vehicle_status,
                requires_authentication=True,
                required_permissions=["operations:vehicle:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.tracking_record",
                description="Record an odometer log and optional location check-in.",
                provider=self.name,
                handler=self.record_tracking,
                requires_authentication=True,
                required_permissions=["operations:vehicle:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.vehicle.tracking_history",
                description="Retrieve reverse-chronological tracking history for a vehicle.",
                provider=self.name,
                handler=self.get_tracking_history,
                requires_authentication=True,
                required_permissions=["operations:vehicle:read"],
                requires_execution_context=True,
            )

            # 2. Incident capabilities (5)
            kernel.register_capability(
                name="kortex.operations.incident.report",
                description="File a new operational incident report.",
                provider=self.name,
                handler=self.report_incident,
                requires_authentication=True,
                required_permissions=["operations:incident:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.incident.get",
                description="Retrieve an incident report by ID.",
                provider=self.name,
                handler=self.get_incident,
                requires_authentication=True,
                required_permissions=["operations:incident:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.incident.list",
                description="Query and paginate incident reports.",
                provider=self.name,
                handler=self.list_incidents,
                requires_authentication=True,
                required_permissions=["operations:incident:read"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.incident.resolve",
                description="Record investigation findings and mark incident as RESOLVED.",
                provider=self.name,
                handler=self.resolve_incident,
                requires_authentication=True,
                required_permissions=["operations:incident:write"],
                requires_execution_context=True,
            )
            kernel.register_capability(
                name="kortex.operations.incident.close",
                description="Formally close and seal an incident record (terminal state).",
                provider=self.name,
                handler=self.close_incident,
                requires_authentication=True,
                required_permissions=["operations:incident:manage"],
                requires_execution_context=True,
            )

            self._set_state(ModuleState.ACTIVE)
            self.logger.info("Operations Module initialized successfully.")
        except Exception as exc:
            self._set_state(ModuleState.FAILED)
            self.logger.error("Failed to initialize Operations Module: %s", exc, exc_info=True)
            raise

    async def start(self) -> None:
        self.ensure_state(ModuleState.ACTIVE)

    async def stop(self) -> None:
        self.ensure_state(ModuleState.ACTIVE)
        self._set_state(ModuleState.STOPPING)
        self._set_state(ModuleState.STOPPED)
        self.logger.info("Operations Module stopped.")

    async def health_check(self) -> dict[str, Any]:
        return {
            "module": self.name,
            "status": "healthy" if self._state == ModuleState.ACTIVE else "unhealthy",
            "state": self._state.value,
        }

    def capabilities(self) -> list[str]:
        """Return list of capability names registered by this module."""
        return list(_REGISTERED_CAPABILITIES)

    def metrics(self) -> dict[str, Any]:
        return {}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self._state.value,
            "capabilities": self.capabilities(),
        }

    # -- Capability Handlers --------------------------------------------------

    def _require_principal(self, execution_context: CapabilityExecutionContext, cap_name: str) -> str:
        """Verify that execution context has an authenticated principal and return principal_id."""
        if execution_context.principal is None:
            raise KortexError(f"{cap_name} requires a verified principal; none was provided.")
        return execution_context.principal.principal_id

    async def create_vehicle(
        self,
        request: CreateVehicleRequest,
        execution_context: CapabilityExecutionContext,
    ) -> VehicleResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.create")
        assert self._manager is not None
        return await self._manager.create_vehicle(request, tenant_id=execution_context.tenant_id)

    async def get_vehicle(
        self,
        vehicle_id: str,
        execution_context: CapabilityExecutionContext,
    ) -> VehicleResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.get")
        assert self._manager is not None
        return await self._manager.get_vehicle(vehicle_id, tenant_id=execution_context.tenant_id)

    async def list_vehicles(
        self,
        request: ListVehiclesRequest,
        execution_context: CapabilityExecutionContext,
    ) -> list[VehicleResponse]:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.list")
        assert self._manager is not None
        return await self._manager.list_vehicles(request, tenant_id=execution_context.tenant_id)

    async def assign_driver(
        self,
        request: AssignDriverRequest,
        execution_context: CapabilityExecutionContext,
    ) -> VehicleResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.assign")
        assert self._manager is not None
        return await self._manager.assign_driver(request, tenant_id=execution_context.tenant_id)

    async def unassign_driver(
        self,
        request: UnassignDriverRequest,
        execution_context: CapabilityExecutionContext,
    ) -> VehicleResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.unassign")
        assert self._manager is not None
        return await self._manager.unassign_driver(request, tenant_id=execution_context.tenant_id)

    async def update_vehicle_status(
        self,
        request: UpdateVehicleStatusRequest,
        execution_context: CapabilityExecutionContext,
    ) -> VehicleResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.status_update")
        assert self._manager is not None
        return await self._manager.update_vehicle_status(request, tenant_id=execution_context.tenant_id)

    async def record_tracking(
        self,
        request: RecordVehicleTrackingRequest,
        execution_context: CapabilityExecutionContext,
    ) -> VehicleTrackingRecordResponse:
        self.ensure_state(ModuleState.ACTIVE)
        principal_id = self._require_principal(execution_context, "kortex.operations.vehicle.tracking_record")
        assert self._manager is not None
        return await self._manager.record_tracking(
            request, tenant_id=execution_context.tenant_id, recorded_by=principal_id
        )

    async def get_tracking_history(
        self,
        request: GetVehicleTrackingHistoryRequest,
        execution_context: CapabilityExecutionContext,
    ) -> list[VehicleTrackingRecordResponse]:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.vehicle.tracking_history")
        assert self._manager is not None
        return await self._manager.get_tracking_history(request, tenant_id=execution_context.tenant_id)

    async def report_incident(
        self,
        request: ReportIncidentRequest,
        execution_context: CapabilityExecutionContext,
    ) -> IncidentResponse:
        self.ensure_state(ModuleState.ACTIVE)
        principal_id = self._require_principal(execution_context, "kortex.operations.incident.report")
        assert self._manager is not None
        return await self._manager.report_incident(
            request, tenant_id=execution_context.tenant_id, reported_by_id=principal_id
        )

    async def get_incident(
        self,
        incident_id: str,
        execution_context: CapabilityExecutionContext,
    ) -> IncidentResponse:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.incident.get")
        assert self._manager is not None
        return await self._manager.get_incident(incident_id, tenant_id=execution_context.tenant_id)

    async def list_incidents(
        self,
        request: ListIncidentsRequest,
        execution_context: CapabilityExecutionContext,
    ) -> list[IncidentResponse]:
        self.ensure_state(ModuleState.ACTIVE)
        self._require_principal(execution_context, "kortex.operations.incident.list")
        assert self._manager is not None
        return await self._manager.list_incidents(request, tenant_id=execution_context.tenant_id)

    async def resolve_incident(
        self,
        request: ResolveIncidentRequest,
        execution_context: CapabilityExecutionContext,
    ) -> IncidentResponse:
        self.ensure_state(ModuleState.ACTIVE)
        principal_id = self._require_principal(execution_context, "kortex.operations.incident.resolve")
        assert self._manager is not None
        return await self._manager.resolve_incident(
            request, tenant_id=execution_context.tenant_id, resolved_by=principal_id
        )

    async def close_incident(
        self,
        request: CloseIncidentRequest,
        execution_context: CapabilityExecutionContext,
    ) -> IncidentResponse:
        self.ensure_state(ModuleState.ACTIVE)
        principal_id = self._require_principal(execution_context, "kortex.operations.incident.close")
        assert self._manager is not None
        return await self._manager.close_incident(
            request, tenant_id=execution_context.tenant_id, closed_by=principal_id
        )


__all__ = ["OperationsModule"]
