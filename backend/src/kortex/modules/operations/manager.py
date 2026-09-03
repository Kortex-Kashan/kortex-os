"""Domain manager orchestrating persistence and transactions for KORTEX Operations.

All mutations execute within `IDataStore.execute_in_transaction`.
All SQL statements enforce tenant isolation using the authoritative `tenant_id`.
Cross-tenant entity access raises domain `NotFoundError` for enumeration resistance.
Events are published strictly post-commit via `EventEngine`.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kortex.engines.event.engine import EventEngine
from kortex.engines.storage.interfaces import IDataStore
from kortex.modules.operations.exceptions import (
    OpsIncidentAlreadyClosedError,
    OpsIncidentConflictError,
    OpsIncidentNotFoundError,
    OpsIncidentValidationError,
    OpsTrackingRecordValidationError,
    OpsVehicleConflictError,
    OpsVehicleNotFoundError,
    OpsVehicleValidationError,
)
from kortex.modules.operations.models import (
    AssignDriverRequest,
    CloseIncidentRequest,
    CreateVehicleRequest,
    GetVehicleTrackingHistoryRequest,
    IncidentResponse,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    ListIncidentsRequest,
    ListVehiclesRequest,
    RecordVehicleTrackingRequest,
    ReportIncidentRequest,
    ResolveIncidentRequest,
    UnassignDriverRequest,
    UpdateVehicleStatusRequest,
    VehicleResponse,
    VehicleStatus,
    VehicleTrackingRecordResponse,
    VehicleType,
)
from kortex.modules.operations.persistence import (
    OpsIncidentRow,
    OpsVehicleRow,
    OpsVehicleTrackingRow,
)

logger = logging.getLogger("kortex.modules.operations.manager")

_INCIDENT_NUM_PATTERN = re.compile(r"^INC-(\d{4})-(\d{4,})$")


class OperationsManager:
    """Orchestrates Operations business operations and transactional persistence."""

    def __init__(self, data_store: IDataStore, event_engine: EventEngine | None = None) -> None:
        self._data_store = data_store
        self._event_engine = event_engine

    async def _emit_event(self, topic: str, payload: dict[str, object]) -> None:
        if self._event_engine is not None:
            try:
                await self._event_engine.publish(
                    topic=topic,
                    payload=payload,
                    sender="operations",
                )
            except Exception as exc:
                logger.warning("Failed to publish event '%s': %s", topic, exc)

    # -- Vehicle Operations ---------------------------------------------------

    async def create_vehicle(self, request: CreateVehicleRequest, tenant_id: str) -> VehicleResponse:
        """Register a new vehicle into the fleet master."""
        vehicle_id = str(uuid.uuid4())

        async def _action(session: AsyncSession) -> OpsVehicleRow:
            # Check unique license plate within tenant
            stmt_plate = select(OpsVehicleRow).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.license_plate == request.license_plate,
            )
            existing_plate = (await session.execute(stmt_plate)).scalar_one_or_none()
            if existing_plate is not None:
                raise OpsVehicleConflictError(
                    f"Vehicle with license plate '{request.license_plate}' already exists for tenant."
                )

            # Check unique VIN if supplied
            if request.vin is not None:
                stmt_vin = select(OpsVehicleRow).where(
                    OpsVehicleRow.tenant_id == tenant_id,
                    OpsVehicleRow.vin == request.vin,
                )
                existing_vin = (await session.execute(stmt_vin)).scalar_one_or_none()
                if existing_vin is not None:
                    raise OpsVehicleConflictError(f"Vehicle with VIN '{request.vin}' already exists for tenant.")

            row = OpsVehicleRow(
                id=vehicle_id,
                tenant_id=tenant_id,
                license_plate=request.license_plate,
                vin=request.vin,
                make=request.make,
                model=request.model,
                year=request.year,
                vehicle_type=request.vehicle_type.value,
                status=VehicleStatus.ACTIVE.value,
                current_odometer=request.initial_odometer,
                assigned_driver_id=None,
                assigned_at=None,
            )
            session.add(row)
            await session.flush()
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_vehicle_response(row)

    async def get_vehicle(self, vehicle_id: str, tenant_id: str) -> VehicleResponse:
        """Retrieve vehicle details by ID within tenant scope."""

        async def _action(session: AsyncSession) -> OpsVehicleRow:
            stmt = select(OpsVehicleRow).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.id == vehicle_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsVehicleNotFoundError(f"Vehicle '{vehicle_id}' not found.")
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_vehicle_response(row)

    async def list_vehicles(self, request: ListVehiclesRequest, tenant_id: str) -> list[VehicleResponse]:
        """Query and paginate fleet vehicles within tenant scope."""

        async def _action(session: AsyncSession) -> list[OpsVehicleRow]:
            stmt = select(OpsVehicleRow).where(OpsVehicleRow.tenant_id == tenant_id)
            if request.status is not None:
                stmt = stmt.where(OpsVehicleRow.status == request.status.value)
            stmt = (
                stmt.order_by(OpsVehicleRow.created_at.desc(), OpsVehicleRow.id.asc())
                .offset(request.offset)
                .limit(request.limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [self._to_vehicle_response(r) for r in rows]

    async def assign_driver(self, request: AssignDriverRequest, tenant_id: str) -> VehicleResponse:
        """Assign an active driver to a vehicle within tenant scope."""
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> OpsVehicleRow:
            stmt = select(OpsVehicleRow).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.id == request.vehicle_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsVehicleNotFoundError(f"Vehicle '{request.vehicle_id}' not found.")

            if row.status == VehicleStatus.DECOMMISSIONED.value:
                raise OpsVehicleValidationError("Cannot assign driver to a decommissioned vehicle.")

            row.assigned_driver_id = request.driver_id
            row.assigned_at = now
            await session.flush()
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_vehicle_response(row)

    async def unassign_driver(self, request: UnassignDriverRequest, tenant_id: str) -> VehicleResponse:
        """Release driver assignment from a vehicle within tenant scope."""

        async def _action(session: AsyncSession) -> OpsVehicleRow:
            stmt = select(OpsVehicleRow).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.id == request.vehicle_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsVehicleNotFoundError(f"Vehicle '{request.vehicle_id}' not found.")

            row.assigned_driver_id = None
            row.assigned_at = None
            await session.flush()
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_vehicle_response(row)

    async def update_vehicle_status(
        self, request: UpdateVehicleStatusRequest, tenant_id: str
    ) -> VehicleResponse:
        """Transition vehicle operational status and publish domain event."""
        old_status: str | None = None
        event_payload: dict[str, object] | None = None

        async def _action(session: AsyncSession) -> OpsVehicleRow:
            nonlocal old_status, event_payload
            stmt = select(OpsVehicleRow).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.id == request.vehicle_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsVehicleNotFoundError(f"Vehicle '{request.vehicle_id}' not found.")

            old_status = row.status
            new_status_val = request.new_status.value

            # If already in requested status, return immediately
            if old_status == new_status_val:
                return row

            # Validate state machine transitions
            # Permitted:
            # ACTIVE -> MAINTENANCE, ACTIVE -> DECOMMISSIONED
            # MAINTENANCE -> ACTIVE, MAINTENANCE -> DECOMMISSIONED
            # DECOMMISSIONED -> terminal (no transitions allowed)
            if old_status == VehicleStatus.DECOMMISSIONED.value:
                raise OpsVehicleValidationError("Cannot transition from DECOMMISSIONED state; it is terminal.")

            permitted = {
                VehicleStatus.ACTIVE.value: {VehicleStatus.MAINTENANCE.value, VehicleStatus.DECOMMISSIONED.value},
                VehicleStatus.MAINTENANCE.value: {VehicleStatus.ACTIVE.value, VehicleStatus.DECOMMISSIONED.value},
            }

            if new_status_val not in permitted.get(old_status, set()):
                raise OpsVehicleValidationError(
                    f"Invalid vehicle status transition from '{old_status}' to '{new_status_val}'."
                )

            # If decommissioning, automatically unassign driver
            if new_status_val == VehicleStatus.DECOMMISSIONED.value:
                row.assigned_driver_id = None
                row.assigned_at = None

            row.status = new_status_val
            await session.flush()

            event_payload = {
                "tenant_id": tenant_id,
                "vehicle_id": row.id,
                "license_plate": row.license_plate,
                "previous_status": old_status,
                "new_status": new_status_val,
                "reason": request.reason,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            return row

        row = await self._data_store.execute_in_transaction(_action)

        # Emit event strictly post-commit
        if event_payload is not None:
            await self._emit_event("kortex.event.operations.vehicle.status_changed", event_payload)

        return self._to_vehicle_response(row)

    async def record_tracking(
        self, request: RecordVehicleTrackingRequest, tenant_id: str, recorded_by: str
    ) -> VehicleTrackingRecordResponse:
        """Record an odometer reading and optional location check-in atomically."""
        tracking_id = str(uuid.uuid4())
        recorded_at = request.recorded_at or datetime.now(UTC)

        async def _action(session: AsyncSession) -> OpsVehicleTrackingRow:
            stmt = select(OpsVehicleRow).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.id == request.vehicle_id,
            )
            vehicle = (await session.execute(stmt)).scalar_one_or_none()
            if vehicle is None:
                raise OpsVehicleNotFoundError(f"Vehicle '{request.vehicle_id}' not found.")

            # Strict monotonicity enforcement
            if request.odometer_reading < vehicle.current_odometer:
                raise OpsTrackingRecordValidationError(
                    f"New odometer reading ({request.odometer_reading}) cannot be less than "
                    f"current vehicle odometer ({vehicle.current_odometer})."
                )

            tracking_row = OpsVehicleTrackingRow(
                id=tracking_id,
                tenant_id=tenant_id,
                vehicle_id=request.vehicle_id,
                recorded_at=recorded_at,
                odometer_reading=request.odometer_reading,
                location_name=request.location_name,
                driver_id=request.driver_id or vehicle.assigned_driver_id,
                notes=request.notes,
                recorded_by=recorded_by,
            )
            session.add(tracking_row)

            # Atomically advance vehicle current_odometer
            vehicle.current_odometer = request.odometer_reading
            await session.flush()
            return tracking_row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_tracking_response(row)

    async def get_tracking_history(
        self, request: GetVehicleTrackingHistoryRequest, tenant_id: str
    ) -> list[VehicleTrackingRecordResponse]:
        """Retrieve reverse-chronological tracking history for a vehicle."""

        async def _action(session: AsyncSession) -> list[OpsVehicleTrackingRow]:
            # Verify vehicle exists in tenant
            stmt_v = select(OpsVehicleRow.id).where(
                OpsVehicleRow.tenant_id == tenant_id,
                OpsVehicleRow.id == request.vehicle_id,
            )
            if (await session.execute(stmt_v)).scalar_one_or_none() is None:
                raise OpsVehicleNotFoundError(f"Vehicle '{request.vehicle_id}' not found.")

            stmt = (
                select(OpsVehicleTrackingRow)
                .where(
                    OpsVehicleTrackingRow.tenant_id == tenant_id,
                    OpsVehicleTrackingRow.vehicle_id == request.vehicle_id,
                )
                .order_by(
                    OpsVehicleTrackingRow.recorded_at.desc(),
                    OpsVehicleTrackingRow.id.desc(),
                )
                .offset(request.offset)
                .limit(request.limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [self._to_tracking_response(r) for r in rows]

    # -- Incident Operations --------------------------------------------------

    async def _generate_incident_number(self, session: AsyncSession, tenant_id: str, year: int) -> str:
        """Generate next sequence number for an incident within tenant scope."""
        prefix = f"INC-{year}-"
        stmt = select(OpsIncidentRow.incident_number).where(
            OpsIncidentRow.tenant_id == tenant_id,
            OpsIncidentRow.incident_number.like(f"{prefix}%"),
        )
        existing_numbers = (await session.execute(stmt)).scalars().all()
        max_seq = 0
        for num in existing_numbers:
            m = _INCIDENT_NUM_PATTERN.match(num)
            if m and int(m.group(1)) == year:
                seq_val = int(m.group(2))
                if seq_val > max_seq:
                    max_seq = seq_val
        next_seq = max_seq + 1
        return f"INC-{year}-{next_seq:04d}"

    async def report_incident(
        self, request: ReportIncidentRequest, tenant_id: str, reported_by_id: str
    ) -> IncidentResponse:
        """File a new operational incident report and publish domain event."""
        incident_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        year = request.occurred_at.year
        event_payload: dict[str, object] | None = None

        # Bounded collision retry for concurrency safety
        max_retries = 3

        for attempt in range(max_retries):
            try:

                async def _action(session: AsyncSession) -> OpsIncidentRow:
                    # Validate vehicle if supplied
                    if request.vehicle_id is not None:
                        stmt_v = select(OpsVehicleRow.id).where(
                            OpsVehicleRow.tenant_id == tenant_id,
                            OpsVehicleRow.id == request.vehicle_id,
                        )
                        if (await session.execute(stmt_v)).scalar_one_or_none() is None:
                            raise OpsVehicleNotFoundError(
                                f"Associated vehicle '{request.vehicle_id}' not found in tenant."
                            )

                    # Generate tenant-scoped sequential incident number
                    incident_number = await self._generate_incident_number(session, tenant_id, year)

                    row = OpsIncidentRow(
                        id=incident_id,
                        tenant_id=tenant_id,
                        incident_number=incident_number,
                        incident_type=request.incident_type.value,
                        severity=request.severity.value,
                        status=IncidentStatus.REPORTED.value,
                        title=request.title,
                        description=request.description,
                        occurred_at=request.occurred_at,
                        reported_at=now,
                        reported_by_id=reported_by_id,
                        vehicle_id=request.vehicle_id,
                        driver_id=request.driver_id,
                        location=request.location,
                        estimated_cost=request.estimated_cost,
                        resolution_notes=None,
                        resolved_at=None,
                        resolved_by=None,
                        closed_at=None,
                        closed_by=None,
                    )
                    session.add(row)
                    await session.flush()
                    return row

                incident_row = await self._data_store.execute_in_transaction(_action)

                event_payload = {
                    "tenant_id": tenant_id,
                    "incident_id": incident_row.id,
                    "incident_number": incident_row.incident_number,
                    "incident_type": incident_row.incident_type,
                    "severity": incident_row.severity,
                    "vehicle_id": incident_row.vehicle_id,
                    "driver_id": incident_row.driver_id,
                    "title": incident_row.title,
                    "occurred_at": incident_row.occurred_at.isoformat(),
                }
                break
            except OpsVehicleNotFoundError:
                raise
            except Exception as exc:
                if attempt == max_retries - 1:
                    logger.error("Failed to generate incident after %d attempts: %s", max_retries, exc)
                    raise OpsIncidentConflictError(
                        f"Failed to generate unique incident number after {max_retries} attempts: {exc}"
                    ) from exc

        # Emit event strictly post-commit
        if event_payload is not None:
            await self._emit_event("kortex.event.operations.incident.reported", event_payload)

        return self._to_incident_response(incident_row)

    async def get_incident(self, incident_id: str, tenant_id: str) -> IncidentResponse:
        """Retrieve an incident report by ID within tenant scope."""

        async def _action(session: AsyncSession) -> OpsIncidentRow:
            stmt = select(OpsIncidentRow).where(
                OpsIncidentRow.tenant_id == tenant_id,
                OpsIncidentRow.id == incident_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsIncidentNotFoundError(f"Incident '{incident_id}' not found.")
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_incident_response(row)

    async def list_incidents(self, request: ListIncidentsRequest, tenant_id: str) -> list[IncidentResponse]:
        """Query and paginate incident reports within tenant scope."""

        async def _action(session: AsyncSession) -> list[OpsIncidentRow]:
            stmt = select(OpsIncidentRow).where(OpsIncidentRow.tenant_id == tenant_id)
            if request.status is not None:
                stmt = stmt.where(OpsIncidentRow.status == request.status.value)
            if request.severity is not None:
                stmt = stmt.where(OpsIncidentRow.severity == request.severity.value)
            if request.vehicle_id is not None:
                stmt = stmt.where(OpsIncidentRow.vehicle_id == request.vehicle_id)

            stmt = (
                stmt.order_by(OpsIncidentRow.occurred_at.desc(), OpsIncidentRow.id.desc())
                .offset(request.offset)
                .limit(request.limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

        rows = await self._data_store.execute_in_transaction(_action)
        return [self._to_incident_response(r) for r in rows]

    async def resolve_incident(
        self, request: ResolveIncidentRequest, tenant_id: str, resolved_by: str
    ) -> IncidentResponse:
        """Record investigation findings and mark incident as RESOLVED."""
        now = datetime.now(UTC)

        async def _action(session: AsyncSession) -> OpsIncidentRow:
            stmt = select(OpsIncidentRow).where(
                OpsIncidentRow.tenant_id == tenant_id,
                OpsIncidentRow.id == request.incident_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsIncidentNotFoundError(f"Incident '{request.incident_id}' not found.")

            if row.status == IncidentStatus.CLOSED.value:
                raise OpsIncidentAlreadyClosedError(
                    f"Incident '{row.incident_number}' is CLOSED and cannot be modified."
                )

            # Permitted transitions to RESOLVED:
            # REPORTED -> RESOLVED, UNDER_INVESTIGATION -> RESOLVED, ACTION_REQUIRED -> RESOLVED
            permitted_prior = {
                IncidentStatus.REPORTED.value,
                IncidentStatus.UNDER_INVESTIGATION.value,
                IncidentStatus.ACTION_REQUIRED.value,
                IncidentStatus.RESOLVED.value,
            }

            if row.status not in permitted_prior:
                raise OpsIncidentValidationError(
                    f"Invalid transition to RESOLVED from current status '{row.status}'."
                )

            row.status = IncidentStatus.RESOLVED.value
            row.resolution_notes = request.resolution_notes
            row.resolved_at = now
            row.resolved_by = resolved_by
            await session.flush()
            return row

        row = await self._data_store.execute_in_transaction(_action)
        return self._to_incident_response(row)

    async def close_incident(
        self, request: CloseIncidentRequest, tenant_id: str, closed_by: str
    ) -> IncidentResponse:
        """Formally close and seal an incident record (terminal state)."""
        now = datetime.now(UTC)
        event_payload: dict[str, object] | None = None

        async def _action(session: AsyncSession) -> OpsIncidentRow:
            nonlocal event_payload
            stmt = select(OpsIncidentRow).where(
                OpsIncidentRow.tenant_id == tenant_id,
                OpsIncidentRow.id == request.incident_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise OpsIncidentNotFoundError(f"Incident '{request.incident_id}' not found.")

            if row.status == IncidentStatus.CLOSED.value:
                raise OpsIncidentAlreadyClosedError(
                    f"Incident '{row.incident_number}' is already CLOSED."
                )

            # Strict state machine: Only RESOLVED -> CLOSED is allowed
            if row.status != IncidentStatus.RESOLVED.value:
                raise OpsIncidentValidationError(
                    f"Incident must be in RESOLVED state before closing; current status is '{row.status}'."
                )

            row.status = IncidentStatus.CLOSED.value
            row.closed_at = now
            row.closed_by = closed_by

            if request.closing_notes:
                if row.resolution_notes:
                    row.resolution_notes = f"{row.resolution_notes}\n[Closing Notes]: {request.closing_notes}"
                else:
                    row.resolution_notes = f"[Closing Notes]: {request.closing_notes}"

            await session.flush()

            event_payload = {
                "tenant_id": tenant_id,
                "incident_id": row.id,
                "incident_number": row.incident_number,
                "closed_by": closed_by,
                "closed_at": row.closed_at.isoformat(),
            }
            return row

        row = await self._data_store.execute_in_transaction(_action)

        # Emit event strictly post-commit
        if event_payload is not None:
            await self._emit_event("kortex.event.operations.incident.closed", event_payload)

        return self._to_incident_response(row)

    # -- Projection Helpers ---------------------------------------------------

    @staticmethod
    def _to_vehicle_response(row: OpsVehicleRow) -> VehicleResponse:
        return VehicleResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            license_plate=row.license_plate,
            vin=row.vin,
            make=row.make,
            model=row.model,
            year=row.year,
            vehicle_type=VehicleType(row.vehicle_type),
            status=VehicleStatus(row.status),
            current_odometer=row.current_odometer,
            assigned_driver_id=row.assigned_driver_id,
            assigned_at=row.assigned_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_tracking_response(row: OpsVehicleTrackingRow) -> VehicleTrackingRecordResponse:
        return VehicleTrackingRecordResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            vehicle_id=row.vehicle_id,
            recorded_at=row.recorded_at,
            odometer_reading=row.odometer_reading,
            location_name=row.location_name,
            driver_id=row.driver_id,
            notes=row.notes,
            recorded_by=row.recorded_by,
            created_at=row.created_at,
        )

    @staticmethod
    def _to_incident_response(row: OpsIncidentRow) -> IncidentResponse:
        return IncidentResponse(
            id=row.id,
            tenant_id=row.tenant_id,
            incident_number=row.incident_number,
            incident_type=IncidentType(row.incident_type),
            severity=IncidentSeverity(row.severity),
            status=IncidentStatus(row.status),
            title=row.title,
            description=row.description,
            occurred_at=row.occurred_at,
            reported_at=row.reported_at,
            reported_by_id=row.reported_by_id,
            vehicle_id=row.vehicle_id,
            driver_id=row.driver_id,
            location=row.location,
            estimated_cost=row.estimated_cost,
            resolution_notes=row.resolution_notes,
            resolved_at=row.resolved_at,
            resolved_by=row.resolved_by,
            closed_at=row.closed_at,
            closed_by=row.closed_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


__all__ = ["OperationsManager"]
