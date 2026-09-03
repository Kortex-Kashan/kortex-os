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
    OpsIncidentConflictError,
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
    UpdateIncidentStatusRequest,
    UpdateVehicleStatusRequest,
    VehicleStatus,
    VehicleType,
)

_TEST_MASTER_KEY = b"\x55" * 32
_TEST_SIGNING_KEY = b"\x66" * 32


async def _create_test_manager(tmp_path: Path) -> tuple[OperationsManager, Kernel, EventEngine]:
    """Provide an OperationsManager wired to an isolated SQLite data store and event engine via Kernel."""
    kernel = Kernel()
    db_file = tmp_path / "ops_test.db"
    kernel._db_manager = DatabaseEngineManager(f"sqlite+aiosqlite:///{db_file}")
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

            # Invariant: attempting to decommission while driver is assigned must be rejected
            with pytest.raises(OpsVehicleValidationError, match=r"while driver .* is assigned"):
                await mgr.update_vehicle_status(
                    UpdateVehicleStatusRequest(vehicle_id=v.id, new_status=VehicleStatus.DECOMMISSIONED),
                    tenant_id=tenant_id,
                )

            # Explicit unassignment required before decommissioning
            await mgr.unassign_driver(
                UnassignDriverRequest(vehicle_id=v.id),
                tenant_id=tenant_id,
            )

            # ACTIVE -> DECOMMISSIONED (terminal) succeeds after unassignment
            v_decom = await mgr.update_vehicle_status(
                UpdateVehicleStatusRequest(vehicle_id=v.id, new_status=VehicleStatus.DECOMMISSIONED),
                tenant_id=tenant_id,
            )
            assert v_decom.status == VehicleStatus.DECOMMISSIONED
            assert v_decom.assigned_driver_id is None

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

            # Attempt driver unassignment from decommissioned vehicle must fail
            with pytest.raises(OpsVehicleValidationError, match="Cannot unassign driver from a decommissioned vehicle"):
                await mgr.unassign_driver(
                    UnassignDriverRequest(vehicle_id=v.id),
                    tenant_id=tenant_id,
                )
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_driver_assignment_restrictions(self, tmp_path: Path) -> None:
        """Driver assignment is permitted only to ACTIVE vehicles and rejects already-assigned vehicles."""
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"
            v = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="RESTR-01", make="Toyota", model="Yaris"),
                tenant_id=tenant_id,
            )

            # Assign to ACTIVE vehicle succeeds
            await mgr.assign_driver(
                AssignDriverRequest(vehicle_id=v.id, driver_id="driver-a"),
                tenant_id=tenant_id,
            )

            # Assigning an already-assigned vehicle must fail with OpsVehicleConflictError
            with pytest.raises(OpsVehicleConflictError):
                await mgr.assign_driver(
                    AssignDriverRequest(vehicle_id=v.id, driver_id="driver-b"),
                    tenant_id=tenant_id,
                )

            # Unassign
            await mgr.unassign_driver(UnassignDriverRequest(vehicle_id=v.id), tenant_id=tenant_id)

            # Transition to MAINTENANCE
            await mgr.update_vehicle_status(
                UpdateVehicleStatusRequest(vehicle_id=v.id, new_status=VehicleStatus.MAINTENANCE),
                tenant_id=tenant_id,
            )

            # Assignment to MAINTENANCE vehicle must fail
            with pytest.raises(OpsVehicleValidationError, match="permitted only for ACTIVE vehicles"):
                await mgr.assign_driver(
                    AssignDriverRequest(vehicle_id=v.id, driver_id="driver-c"),
                    tenant_id=tenant_id,
                )
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_vehicle_vin_uniqueness_and_null_semantics(self, tmp_path: Path) -> None:
        """VIN uniqueness is enforced per tenant, while empty/null VINs do not conflict."""
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_1 = "tenant-alpha"
            tenant_2 = "tenant-beta"
            shared_vin = "1HGCR2F83HA999999"

            # Create vehicle with VIN in tenant 1
            v1 = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="VIN-001", make="Honda", model="Civic", vin=shared_vin),
                tenant_id=tenant_1,
            )
            assert v1.vin == shared_vin

            # Duplicate VIN in SAME tenant must be rejected
            with pytest.raises(OpsVehicleConflictError):
                await mgr.create_vehicle(
                    CreateVehicleRequest(license_plate="VIN-002", make="Honda", model="Accord", vin=shared_vin),
                    tenant_id=tenant_1,
                )

            # Same VIN in DIFFERENT tenant is permitted
            v2 = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="VIN-001", make="Honda", model="Civic", vin=shared_vin),
                tenant_id=tenant_2,
            )
            assert v2.vin == shared_vin

            # Multiple vehicles with empty/None VIN in the same tenant do not collide
            v_null1 = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="NVIN-001", make="Ford", model="Transit", vin=None),
                tenant_id=tenant_1,
            )
            v_null2 = await mgr.create_vehicle(
                CreateVehicleRequest(license_plate="NVIN-002", make="Ford", model="Transit", vin=None),
                tenant_id=tenant_1,
            )
            assert v_null1.vin is None
            assert v_null2.vin is None
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

            # Cannot directly RESOLVE a MEDIUM severity incident from REPORTED (must go through investigation)
            with pytest.raises(OpsIncidentValidationError, match="Resolution is permitted from ACTION_REQUIRED"):
                await mgr.resolve_incident(
                    ResolveIncidentRequest(
                        incident_id=inc.id,
                        resolution_notes="Direct resolve attempt",
                    ),
                    tenant_id=tenant_id,
                    resolved_by="investigator-1",
                )

            # Advance REPORTED -> UNDER_INVESTIGATION
            under_inv = await mgr.update_incident_status(
                UpdateIncidentStatusRequest(
                    incident_id=inc.id,
                    status=IncidentStatus.UNDER_INVESTIGATION,
                ),
                tenant_id=tenant_id,
            )
            assert under_inv.status == IncidentStatus.UNDER_INVESTIGATION

            # Advance UNDER_INVESTIGATION -> ACTION_REQUIRED
            act_req = await mgr.update_incident_status(
                UpdateIncidentStatusRequest(
                    incident_id=inc.id,
                    status=IncidentStatus.ACTION_REQUIRED,
                ),
                tenant_id=tenant_id,
            )
            assert act_req.status == IncidentStatus.ACTION_REQUIRED

            # RESOLVE the incident from ACTION_REQUIRED
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

            # Attempting to re-resolve an already RESOLVED incident must fail
            with pytest.raises(OpsIncidentValidationError, match="already RESOLVED"):
                await mgr.resolve_incident(
                    ResolveIncidentRequest(incident_id=inc.id, resolution_notes="Duplicate resolution"),
                    tenant_id=tenant_id,
                    resolved_by="investigator-1",
                )

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

            with pytest.raises(OpsIncidentAlreadyClosedError):
                await mgr.update_incident_status(
                    UpdateIncidentStatusRequest(incident_id=inc.id, status=IncidentStatus.UNDER_INVESTIGATION),
                    tenant_id=tenant_id,
                )
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_incident_low_severity_fast_path(self, tmp_path: Path) -> None:
        """LOW severity incidents may transition directly from REPORTED to RESOLVED."""
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"
            inc = await mgr.report_incident(
                ReportIncidentRequest(
                    incident_type=IncidentType.OTHER,
                    severity=IncidentSeverity.LOW,
                    title="Lost fuel cap",
                    description="Driver misplaced fuel cap during refueling.",
                    occurred_at=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
                reported_by_id="p1",
            )
            assert inc.status == IncidentStatus.REPORTED

            # Direct resolution permitted for LOW severity
            resolved = await mgr.resolve_incident(
                ResolveIncidentRequest(incident_id=inc.id, resolution_notes="Replacement cap purchased for $15."),
                tenant_id=tenant_id,
                resolved_by="fleet-lead",
            )
            assert resolved.status == IncidentStatus.RESOLVED
            assert resolved.resolved_by == "fleet-lead"
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_incident_status_update_invalid_transitions(self, tmp_path: Path) -> None:
        """Assert fail-closed validation for forbidden intermediate status transitions."""
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"
            inc = await mgr.report_incident(
                ReportIncidentRequest(
                    incident_type=IncidentType.ACCIDENT,
                    severity=IncidentSeverity.HIGH,
                    title="Fender bender",
                    description="Hit parking post.",
                    occurred_at=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
                reported_by_id="p1",
            )

            # Forbidden: Skipping directly from REPORTED to ACTION_REQUIRED via status_update
            with pytest.raises(OpsIncidentValidationError):
                await mgr.update_incident_status(
                    UpdateIncidentStatusRequest(incident_id=inc.id, status=IncidentStatus.ACTION_REQUIRED),
                    tenant_id=tenant_id,
                )

            # Forbidden: Setting to RESOLVED via status_update (must use resolve_incident)
            with pytest.raises(OpsIncidentValidationError):
                await mgr.update_incident_status(
                    UpdateIncidentStatusRequest(incident_id=inc.id, status=IncidentStatus.RESOLVED),
                    tenant_id=tenant_id,
                )

            # Advance to UNDER_INVESTIGATION
            await mgr.update_incident_status(
                UpdateIncidentStatusRequest(incident_id=inc.id, status=IncidentStatus.UNDER_INVESTIGATION),
                tenant_id=tenant_id,
            )

            # Forbidden: Backward transition from UNDER_INVESTIGATION to REPORTED
            with pytest.raises(OpsIncidentValidationError):
                await mgr.update_incident_status(
                    UpdateIncidentStatusRequest(incident_id=inc.id, status=IncidentStatus.REPORTED),
                    tenant_id=tenant_id,
                )
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_incident_numbering_concurrency_and_retry(self, tmp_path: Path) -> None:
        """Simulate collision during incident creation to verify retry logic succeeds."""
        import asyncio

        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"
            year = datetime.now(UTC).year

            # Concurrent incident creation
            async def _create_one(title: str) -> str:
                res = await mgr.report_incident(
                    ReportIncidentRequest(
                        incident_type=IncidentType.BREAKDOWN,
                        severity=IncidentSeverity.LOW,
                        title=title,
                        description="Test breakdown description",
                        occurred_at=datetime.now(UTC),
                    ),
                    tenant_id=tenant_id,
                    reported_by_id="p1",
                )
                return res.incident_number

            results = await asyncio.gather(
                _create_one("Concurrent Incident 1"),
                _create_one("Concurrent Incident 2"),
                _create_one("Concurrent Incident 3"),
            )

            # Distinct sequence numbers must be assigned
            assert len(set(results)) == 3
            assert all(r.startswith(f"INC-{year}-") for r in results)
        finally:
            await kernel.shutdown()

    @pytest.mark.asyncio
    async def test_incident_numbering_exhaustion_raises_conflict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When retries are exhausted due to continuous collisions, OpsIncidentConflictError is raised."""
        mgr, kernel, _ = await _create_test_manager(tmp_path)
        try:
            tenant_id = "tenant-alpha"
            # Seed an existing incident
            await mgr.report_incident(
                ReportIncidentRequest(
                    incident_type=IncidentType.BREAKDOWN,
                    severity=IncidentSeverity.LOW,
                    title="Seed",
                    description="Seed incident",
                    occurred_at=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
                reported_by_id="p1",
            )
            # Force _generate_incident_number to return colliding number
            async def _mock_gen(*args: object, **kwargs: object) -> str:
                return f"INC-{datetime.now(UTC).year}-0001"

            monkeypatch.setattr(mgr, "_generate_incident_number", _mock_gen)

            with pytest.raises(OpsIncidentConflictError):
                await mgr.report_incident(
                    ReportIncidentRequest(
                        incident_type=IncidentType.BREAKDOWN,
                        severity=IncidentSeverity.LOW,
                        title="Colliding",
                        description="Collision description",
                        occurred_at=datetime.now(UTC),
                    ),
                    tenant_id=tenant_id,
                    reported_by_id="p1",
                )
        finally:
            await kernel.shutdown()
