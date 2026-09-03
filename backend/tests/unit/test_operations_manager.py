"""Unit tests for OperationsManager domain logic and persistence.

Tests vehicle master operations, plate uniqueness within/across tenants,
driver assignment, status lifecycle transitions, monotonic odometer progression,
atomic tracking records, incident numbering, investigation notes, resolution,
and terminal incident closure immutability.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from kortex.core.db import DatabaseEngineManager
from kortex.core.kernel import Kernel
from kortex.engines.event.engine import Event, EventEngine
from kortex.engines.security.engine import SecurityEngine
from kortex.engines.storage.engine import StorageEngine
from kortex.modules.operations.exceptions import (
    OpsIncidentAlreadyClosedError,
    OpsIncidentValidationError,
    OpsTrackingRecordValidationError,
    OpsVehicleConflictError,
    OpsVehicleValidationError,
)
from kortex.modules.operations.manager import OperationsManager
from kortex.modules.operations.models import (
    AssignDriverRequest,
    CloseIncidentRequest,
    CreateVehicleRequest,
    GetVehicleTrackingHistoryRequest,
    IncidentSeverity,
    IncidentStatus,
    IncidentType,
    ListVehiclesRequest,
    RecordVehicleTrackingRequest,
    ReportIncidentRequest,
    ResolveIncidentRequest,
    UnassignDriverRequest,
    UpdateVehicleStatusRequest,
    VehicleStatus,
    VehicleType,
)

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32


async def _create_test_manager(tmp_path: Path) -> tuple[OperationsManager, Kernel, EventEngine]:
    """Provide an OperationsManager wired to an isolated SQLite data store and event engine via Kernel."""
    kernel = Kernel()
    kernel._db_manager = DatabaseEngineManager("sqlite+aiosqlite:///:memory:")
    storage_engine = StorageEngine(base_directory=str(tmp_path / "ops_test_storage"))
    security_engine = SecurityEngine(master_key=_TEST_MASTER_KEY, signing_private_key=_TEST_SIGNING_KEY)
    event_engine: EventEngine = kernel.get_engine("event")  # type: ignore[assignment]

    kernel.register_engine(storage_engine)
    kernel.register_engine(security_engine)
    await kernel.boot()

    data_store = storage_engine.data
    assert data_store is not None
    manager = OperationsManager(data_store=data_store, event_engine=event_engine)
    return manager, kernel, event_engine


class TestVehicleManagerOperations:
    """Validate vehicle domain logic and persistence."""

    @pytest.mark.asyncio
    async def test_create_and_get_vehicle(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            req = CreateVehicleRequest(
                license_plate="KOR-101",
                make="Toyota",
                model="Hilux",
                year=2024,
                vehicle_type=VehicleType.TRUCK,
                vin="1HGCR2F83HA123456",
                initial_odometer=Decimal("500.00"),
            )
            created = await mgr.create_vehicle(req, tenant_id=tenant_id)
            assert created.license_plate == "KOR-101"
            assert created.status == VehicleStatus.ACTIVE
            assert created.current_odometer == Decimal("500.00")
            assert created.tenant_id == tenant_id
            assert created.assigned_driver_id is None

            retrieved = await mgr.get_vehicle(created.id, tenant_id=tenant_id)
            assert retrieved.id == created.id
            assert retrieved.make == "Toyota"
            assert retrieved.model == "Hilux"
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_duplicate_plate_same_tenant_fails(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            req = CreateVehicleRequest(license_plate="DUP-001", make="Honda", model="Civic")
            await mgr.create_vehicle(req, tenant_id=tenant_id)

            with pytest.raises(OpsVehicleConflictError):
                await mgr.create_vehicle(req, tenant_id=tenant_id)
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_same_plate_different_tenants_succeeds(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            req = CreateVehicleRequest(license_plate="MULTI-TENANT-1", make="Nissan", model="Navara")

            v1 = await mgr.create_vehicle(req, tenant_id="tenant-1")
            v2 = await mgr.create_vehicle(req, tenant_id="tenant-2")
            assert v1.id != v2.id
            assert v1.tenant_id == "tenant-1"
            assert v2.tenant_id == "tenant-2"
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_driver_assignment_and_unassignment(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            v = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="DRV-001", make="Ford", model="Ranger"),
                tenant_id=tenant_id,
            )
            assert v.assigned_driver_id is None

            # Assign driver
            assigned = await mgr.assign_driver(
                AssignDriverRequest(vehicle_id=v.id, driver_id="driver-emp-99"),
                tenant_id=tenant_id,
            )
            assert assigned.assigned_driver_id == "driver-emp-99"
            assert assigned.assigned_at is not None

            # Unassign driver
            unassigned = await mgr.unassign_driver(
                UnassignDriverRequest(vehicle_id=v.id),
                tenant_id=tenant_id,
            )
            assert unassigned.assigned_driver_id is None
            assert unassigned.assigned_at is None
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_vehicle_status_state_machine(self, tmp_path: Path) -> None:
        mgr, kernel, event_engine = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            events: list[Event] = []

            async def _capture(event: Event) -> None:
                events.append(event)

            event_engine.subscribe("kortex.event.operations.vehicle.status_changed", _capture)

            v = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="STATE-01", make="Mazda", model="BT-50"),
                tenant_id=tenant_id,
            )
            assert v.status == VehicleStatus.ACTIVE

            # ACTIVE -> MAINTENANCE
            v_maint = await mgr.update_vehicle_status(
                UpdateVehicleStatusRequest(
                    vehicle_id=v.id,
                    new_status=VehicleStatus.MAINTENANCE,
                    reason="Brake inspection",
                ),
                tenant_id=tenant_id,
            )
            assert v_maint.status == VehicleStatus.MAINTENANCE
            assert len(events) == 1
            assert events[0].payload["previous_status"] == "ACTIVE"
            assert events[0].payload["new_status"] == "MAINTENANCE"

            # MAINTENANCE -> ACTIVE
            v_active = await mgr.update_vehicle_status(
                UpdateVehicleStatusRequest(vehicle_id=v.id, new_status=VehicleStatus.ACTIVE),
                tenant_id=tenant_id,
            )
            assert v_active.status == VehicleStatus.ACTIVE
            assert len(events) == 2

            # Assign driver before decommission
            await mgr.assign_driver(
                AssignDriverRequest(vehicle_id=v.id, driver_id="driver-1"),
                tenant_id=tenant_id,
            )

            # ACTIVE -> DECOMMISSIONED (terminal)
            v_decom = await mgr.update_vehicle_status(
                UpdateVehicleStatusRequest(vehicle_id=v.id, new_status=VehicleStatus.DECOMMISSIONED),
                tenant_id=tenant_id,
            )
            assert v_decom.status == VehicleStatus.DECOMMISSIONED
            assert v_decom.assigned_driver_id is None  # Driver automatically unassigned

            # Attempt transition out of DECOMMISSIONED must fail
            with pytest.raises(OpsVehicleValidationError):
                await mgr.update_vehicle_status(
                    UpdateVehicleStatusRequest(vehicle_id=v.id, new_status=VehicleStatus.ACTIVE),
                    tenant_id=tenant_id,
                )

            # Attempt driver assignment to decommissioned vehicle must fail
            with pytest.raises(OpsVehicleValidationError):
                await mgr.assign_driver(
                    AssignDriverRequest(vehicle_id=v.id, driver_id="driver-new"),
                    tenant_id=tenant_id,
                )
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_tracking_odometer_strict_monotonicity(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            v = await mgr.create_vehicle(
                CreateVehicleRequest(
                    license_plate="ODO-01",
                    make="Toyota",
                    model="Land Cruiser",
                    initial_odometer=Decimal("10000.00"),
                ),
                tenant_id=tenant_id,
            )

            # Log 1: advance to 10500.00
            t1 = await mgr.record_tracking(
                RecordVehicleTrackingRequest(
                    vehicle_id=v.id,
                    odometer_reading=Decimal("10500.00"),
                    location_name="Depot A",
                ),
                tenant_id=tenant_id,
                recorded_by="principal-user-1",
            )
            assert t1.odometer_reading == Decimal("10500.00")
            assert t1.location_name == "Depot A"

            v_updated = await mgr.get_vehicle(v.id, tenant_id=tenant_id)
            assert v_updated.current_odometer == Decimal("10500.00")

            # Log 2: attempt to decrease odometer to 10400.00 -> MUST FAIL
            with pytest.raises(OpsTrackingRecordValidationError):
                await mgr.record_tracking(
                    RecordVehicleTrackingRequest(
                        vehicle_id=v.id,
                        odometer_reading=Decimal("10400.00"),
                    ),
                    tenant_id=tenant_id,
                    recorded_by="principal-user-1",
                )

            # Verify vehicle odometer was not corrupted
            v_check = await mgr.get_vehicle(v.id, tenant_id=tenant_id)
            assert v_check.current_odometer == Decimal("10500.00")

            # Log 3: advance to 11000.00
            await mgr.record_tracking(
                RecordVehicleTrackingRequest(
                    vehicle_id=v.id,
                    odometer_reading=Decimal("11000.00"),
                    location_name="Client Site",
                ),
                tenant_id=tenant_id,
                recorded_by="principal-user-1",
            )

            history = await mgr.get_tracking_history(
                GetVehicleTrackingHistoryRequest(vehicle_id=v.id),
                tenant_id=tenant_id,
            )
            assert len(history) == 2
            assert history[0].odometer_reading == Decimal("11000.00")
            assert history[1].odometer_reading == Decimal("10500.00")
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_list_vehicles_pagination_and_filter(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            for i in range(5):
                await mgr.create_vehicle(
                    CreateVehicleRequest(license_plate=f"LIST-{i}", make="Make", model="Model"),
                    tenant_id=tenant_id,
                )

            res = await mgr.list_vehicles(ListVehiclesRequest(limit=3, offset=0), tenant_id=tenant_id)
            assert len(res) == 3

            res_page2 = await mgr.list_vehicles(ListVehiclesRequest(limit=3, offset=3), tenant_id=tenant_id)
            assert len(res_page2) == 2
        finally:
            await kernel.shutdown()


class TestIncidentManagerOperations:
    """Validate incident filing, sequence numbering, and lifecycle transitions."""

    @pytest.mark.asyncio
    async def test_report_and_get_incident(self, tmp_path: Path) -> None:
        mgr, kernel, event_engine = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            events: list[Event] = []

            async def _capture(event: Event) -> None:
                events.append(event)

            event_engine.subscribe("kortex.event.operations.incident.reported", _capture)

            v = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="INC-VEH-1", make="Toyota", model="Yaris"),
                tenant_id=tenant_id,
            )

            occurred = datetime.now(UTC) - timedelta(minutes=30)
            req = ReportIncidentRequest(
                incident_type=IncidentType.ACCIDENT,
                severity=IncidentSeverity.HIGH,
                title="Side-mirror impact with pillar",
                description="Driver grazed parking column while reversing.",
                occurred_at=occurred,
                vehicle_id=v.id,
                driver_id="driver-101",
                location="Underground Parking B2",
                estimated_cost=Decimal("450.00"),
            )
            inc = await mgr.report_incident(req, tenant_id=tenant_id, reported_by_id="principal-1")

            assert inc.incident_number.startswith(f"INC-{occurred.year}-")
            assert inc.status == IncidentStatus.REPORTED
            assert inc.severity == IncidentSeverity.HIGH
            assert inc.vehicle_id == v.id
            assert inc.driver_id == "driver-101"
            assert len(events) == 1
            assert events[0].payload["incident_id"] == inc.id

            retrieved = await mgr.get_incident(inc.id, tenant_id=tenant_id)
            assert retrieved.id == inc.id
            assert retrieved.title == "Side-mirror impact with pillar"
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_incident_sequential_numbering(self, tmp_path: Path) -> None:
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"
            year = datetime.now(UTC).year

            inc1 = await mgr.report_incident(
                ReportIncidentRequest(
                    incident_type=IncidentType.BREAKDOWN,
                    severity=IncidentSeverity.LOW,
                    title="Dead battery",
                    description="Battery flat in morning",
                    occurred_at=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
                reported_by_id="p1",
            )
            inc2 = await mgr.report_incident(
                ReportIncidentRequest(
                    incident_type=IncidentType.TRAFFIC_VIOLATION,
                    severity=IncidentSeverity.LOW,
                    title="Parking ticket",
                    description="Expired meter",
                    occurred_at=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
                reported_by_id="p1",
            )
            assert inc1.incident_number == f"INC-{year}-0001"
            assert inc2.incident_number == f"INC-{year}-0002"
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_incident_lifecycle_resolve_and_close(self, tmp_path: Path) -> None:
        mgr, kernel, event_engine = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"

            close_events: list[Event] = []

            async def _capture_close(event: Event) -> None:
                close_events.append(event)

            event_engine.subscribe("kortex.event.operations.incident.closed", _capture_close)

            inc = await mgr.report_incident(
                ReportIncidentRequest(
                    incident_type=IncidentType.PROPERTY_DAMAGE,
                    severity=IncidentSeverity.MEDIUM,
                    title="Broken windshield",
                    description="Stone chip spread across driver side.",
                    occurred_at=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
                reported_by_id="p1",
            )
            assert inc.status == IncidentStatus.REPORTED

            # Cannot directly CLOSE a REPORTED incident (must be RESOLVED first)
            with pytest.raises(OpsIncidentValidationError):
                await mgr.close_incident(
                    CloseIncidentRequest(incident_id=inc.id),
                    tenant_id=tenant_id,
                    closed_by="manager-1",
                )

            # RESOLVE the incident
            resolved = await mgr.resolve_incident(
                ResolveIncidentRequest(
                    incident_id=inc.id,
                    resolution_notes="Windshield replaced by Safelite autoglass.",
                ),
                tenant_id=tenant_id,
                resolved_by="investigator-1",
            )
            assert resolved.status == IncidentStatus.RESOLVED
            assert resolved.resolution_notes == "Windshield replaced by Safelite autoglass."
            assert resolved.resolved_by == "investigator-1"
            assert resolved.resolved_at is not None

            # CLOSE the incident (terminal state)
            closed = await mgr.close_incident(
                CloseIncidentRequest(incident_id=inc.id, closing_notes="Invoice settled."),
                tenant_id=tenant_id,
                closed_by="manager-supervisor-1",
            )
            assert closed.status == IncidentStatus.CLOSED
            assert closed.closed_by == "manager-supervisor-1"
            assert closed.closed_at is not None
            assert "[Closing Notes]: Invoice settled." in (closed.resolution_notes or "")
            assert len(close_events) == 1
            assert close_events[0].payload["incident_number"] == inc.incident_number

            # Modifying a CLOSED incident must raise OpsIncidentAlreadyClosedError
            with pytest.raises(OpsIncidentAlreadyClosedError):
                await mgr.resolve_incident(
                    ResolveIncidentRequest(incident_id=inc.id, resolution_notes="New notes"),
                    tenant_id=tenant_id,
                    resolved_by="investigator-1",
                )

            with pytest.raises(OpsIncidentAlreadyClosedError):
                await mgr.close_incident(
                    CloseIncidentRequest(incident_id=inc.id),
                    tenant_id=tenant_id,
                    closed_by="manager-1",
                )
        finally:
            await kernel.shutdown()
