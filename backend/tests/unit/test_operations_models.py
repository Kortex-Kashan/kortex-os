"""Unit tests for KORTEX Operations domain models and validation rules.

Validates schema constraints, enum values, sanitization, and strictly asserts
that request models never expose `tenant_id`, `principal`, or `execution_context`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from kortex.modules.operations.models import (
    AssignDriverRequest,
    CloseIncidentRequest,
    CreateVehicleRequest,
    GetVehicleTrackingHistoryRequest,
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
    VehicleStatus,
    VehicleType,
)


class TestOperationsEnums:
    """Validate completeness and serialization of all domain enums."""

    def test_vehicle_type_members(self) -> None:
        assert set(VehicleType) == {
            VehicleType.SEDAN,
            VehicleType.SUV,
            VehicleType.TRUCK,
            VehicleType.VAN,
            VehicleType.MOTORCYCLE,
            VehicleType.HEAVY_EQUIPMENT,
            VehicleType.OTHER,
        }

    def test_vehicle_status_members(self) -> None:
        assert set(VehicleStatus) == {
            VehicleStatus.ACTIVE,
            VehicleStatus.MAINTENANCE,
            VehicleStatus.DECOMMISSIONED,
        }

    def test_incident_type_members(self) -> None:
        assert set(IncidentType) == {
            IncidentType.ACCIDENT,
            IncidentType.BREAKDOWN,
            IncidentType.TRAFFIC_VIOLATION,
            IncidentType.THEFT_VANDALISM,
            IncidentType.PROPERTY_DAMAGE,
            IncidentType.NEAR_MISS,
            IncidentType.OTHER,
        }

    def test_incident_severity_members(self) -> None:
        assert set(IncidentSeverity) == {
            IncidentSeverity.LOW,
            IncidentSeverity.MEDIUM,
            IncidentSeverity.HIGH,
            IncidentSeverity.CRITICAL,
        }

    def test_incident_status_members(self) -> None:
        assert set(IncidentStatus) == {
            IncidentStatus.REPORTED,
            IncidentStatus.UNDER_INVESTIGATION,
            IncidentStatus.ACTION_REQUIRED,
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,
        }


class TestVehicleRequestValidation:
    """Validate request model constraints for vehicles."""

    def test_create_vehicle_valid(self) -> None:
        req = CreateVehicleRequest(
            license_plate="abc-1234",
            make="Toyota",
            model="Hilux",
            year=2023,
            vehicle_type=VehicleType.TRUCK,
            vin="1HGCR2F83HA000000",
            initial_odometer=Decimal("15000.50"),
        )
        assert req.license_plate == "ABC-1234"
        assert req.make == "Toyota"
        assert req.model == "Hilux"
        assert req.year == 2023
        assert req.vehicle_type == VehicleType.TRUCK
        assert req.vin == "1HGCR2F83HA000000"
        assert req.initial_odometer == Decimal("15000.50")

    def test_create_vehicle_defaults(self) -> None:
        req = CreateVehicleRequest(
            license_plate="XYZ-999",
            make="Ford",
            model="Transit",
        )
        assert req.license_plate == "XYZ-999"
        assert req.vehicle_type == VehicleType.SEDAN
        assert req.initial_odometer == Decimal("0.00")
        assert req.vin is None
        assert req.year is None

    def test_create_vehicle_blank_license_plate_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CreateVehicleRequest(
                license_plate="   ",
                make="Toyota",
                model="Corolla",
            )
        assert "license_plate" in str(exc_info.value)

    def test_create_vehicle_blank_make_or_model_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateVehicleRequest(license_plate="ABC", make=" ", model="Corolla")
        with pytest.raises(ValidationError):
            CreateVehicleRequest(license_plate="ABC", make="Toyota", model="")

    def test_create_vehicle_negative_odometer_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateVehicleRequest(
                license_plate="ABC",
                make="Toyota",
                model="Corolla",
                initial_odometer=Decimal("-10.00"),
            )

    def test_create_vehicle_invalid_vin_rejected(self) -> None:
        # Invalid characters: I, O, Q
        with pytest.raises(ValidationError):
            CreateVehicleRequest(
                license_plate="ABC",
                make="Toyota",
                model="Corolla",
                vin="1HGCR2F83HO000000",  # contains 'O'
            )
        # Invalid length
        with pytest.raises(ValidationError):
            CreateVehicleRequest(
                license_plate="ABC",
                make="Toyota",
                model="Corolla",
                vin="SHORTVIN",
            )

    def test_assign_driver_request_validation(self) -> None:
        req = AssignDriverRequest(vehicle_id="veh-123", driver_id="drv-456")
        assert req.vehicle_id == "veh-123"
        assert req.driver_id == "drv-456"

        with pytest.raises(ValidationError):
            AssignDriverRequest(vehicle_id="  ", driver_id="drv-456")
        with pytest.raises(ValidationError):
            AssignDriverRequest(vehicle_id="veh-123", driver_id=" ")

    def test_unassign_driver_request_validation(self) -> None:
        req = UnassignDriverRequest(vehicle_id="veh-123")
        assert req.vehicle_id == "veh-123"

        with pytest.raises(ValidationError):
            UnassignDriverRequest(vehicle_id="  ")

    def test_update_vehicle_status_request_validation(self) -> None:
        req = UpdateVehicleStatusRequest(
            vehicle_id="veh-123",
            new_status=VehicleStatus.MAINTENANCE,
            reason="Scheduled 50k service",
        )
        assert req.new_status == VehicleStatus.MAINTENANCE
        assert req.reason == "Scheduled 50k service"

    def test_record_tracking_request_validation(self) -> None:
        req = RecordVehicleTrackingRequest(
            vehicle_id="veh-123",
            odometer_reading=Decimal("12345.67"),
            location_name="North Depot",
        )
        assert req.odometer_reading == Decimal("12345.67")

        with pytest.raises(ValidationError):
            RecordVehicleTrackingRequest(
                vehicle_id="veh-123",
                odometer_reading=Decimal("-5.00"),
            )

    def test_record_tracking_future_timestamp_rejected(self) -> None:
        future_time = datetime.now(UTC) + timedelta(hours=2)
        with pytest.raises(ValidationError):
            RecordVehicleTrackingRequest(
                vehicle_id="veh-123",
                odometer_reading=Decimal("100"),
                recorded_at=future_time,
            )


class TestIncidentRequestValidation:
    """Validate request model constraints for operational incidents."""

    def test_report_incident_valid(self) -> None:
        occurred = datetime.now(UTC) - timedelta(hours=1)
        req = ReportIncidentRequest(
            incident_type=IncidentType.ACCIDENT,
            severity=IncidentSeverity.HIGH,
            title="Rear-end collision at traffic light",
            description="Vehicle was struck from behind while stopped at red signal.",
            occurred_at=occurred,
            vehicle_id="veh-100",
            driver_id="emp-200",
            location="Main St & 4th Ave",
            estimated_cost=Decimal("2500.00"),
        )
        assert req.incident_type == IncidentType.ACCIDENT
        assert req.severity == IncidentSeverity.HIGH
        assert req.title == "Rear-end collision at traffic light"
        assert req.estimated_cost == Decimal("2500.00")

    def test_report_incident_blank_title_or_description_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            ReportIncidentRequest(
                incident_type=IncidentType.BREAKDOWN,
                severity=IncidentSeverity.LOW,
                title="   ",
                description="Flat tire",
                occurred_at=now,
            )
        with pytest.raises(ValidationError):
            ReportIncidentRequest(
                incident_type=IncidentType.BREAKDOWN,
                severity=IncidentSeverity.LOW,
                title="Flat tire",
                description="  ",
                occurred_at=now,
            )

    def test_report_incident_future_occurred_at_rejected(self) -> None:
        future = datetime.now(UTC) + timedelta(days=1)
        with pytest.raises(ValidationError):
            ReportIncidentRequest(
                incident_type=IncidentType.ACCIDENT,
                severity=IncidentSeverity.CRITICAL,
                title="Accident",
                description="Accident description",
                occurred_at=future,
            )

    def test_report_incident_negative_cost_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            ReportIncidentRequest(
                incident_type=IncidentType.ACCIDENT,
                severity=IncidentSeverity.MEDIUM,
                title="Scratch",
                description="Door scratched",
                occurred_at=now,
                estimated_cost=Decimal("-100.00"),
            )

    def test_resolve_incident_request_validation(self) -> None:
        req = ResolveIncidentRequest(
            incident_id="inc-123",
            resolution_notes="Repairs completed at authorized service station.",
        )
        assert req.resolution_notes == "Repairs completed at authorized service station."

        with pytest.raises(ValidationError):
            ResolveIncidentRequest(incident_id="inc-123", resolution_notes="  ")

    def test_close_incident_request_validation(self) -> None:
        req = CloseIncidentRequest(incident_id="inc-123", closing_notes="Insurance claim paid.")
        assert req.incident_id == "inc-123"
        assert req.closing_notes == "Insurance claim paid."


_REQUEST_CLASSES = [
    CreateVehicleRequest,
    AssignDriverRequest,
    UnassignDriverRequest,
    UpdateVehicleStatusRequest,
    RecordVehicleTrackingRequest,
    GetVehicleTrackingHistoryRequest,
    ListVehiclesRequest,
    ReportIncidentRequest,
    ResolveIncidentRequest,
    CloseIncidentRequest,
    ListIncidentsRequest,
]


class TestSecurityIdentityModelIsolation:
    """Assert strictly that zero Operations request models expose identity fields."""

    @pytest.mark.parametrize("model_cls", _REQUEST_CLASSES)
    def test_request_models_strictly_omit_identity_fields(self, model_cls: type[BaseModel]) -> None:
        fields = model_cls.model_fields.keys()
        assert "tenant_id" not in fields, f"{model_cls.__name__} must not expose tenant_id"
        assert "principal" not in fields, f"{model_cls.__name__} must not expose principal"
        assert "execution_context" not in fields, f"{model_cls.__name__} must not expose execution_context"
