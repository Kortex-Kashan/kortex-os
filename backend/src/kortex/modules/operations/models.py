"""Pydantic v2 domain schemas for the KORTEX Operations business module.

All request models deliberately omit `tenant_id`, `principal`, and `execution_context`
fields -- authoritative actor identity and tenant scope are derived exclusively by
the dispatcher and passed via `CapabilityExecutionContext`.
All monetary and odometer metrics use `Decimal` with strict validation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Standard 17-character VIN pattern (excluding I, O, Q to prevent character confusion)
_VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


# -- Domain Enums -------------------------------------------------------------


class VehicleType(str, Enum):
    """Categorization of a fleet vehicle."""

    SEDAN = "SEDAN"
    SUV = "SUV"
    TRUCK = "TRUCK"
    VAN = "VAN"
    MOTORCYCLE = "MOTORCYCLE"
    HEAVY_EQUIPMENT = "HEAVY_EQUIPMENT"
    OTHER = "OTHER"


class VehicleStatus(str, Enum):
    """Operational lifecycle state of a vehicle."""

    ACTIVE = "ACTIVE"
    MAINTENANCE = "MAINTENANCE"
    DECOMMISSIONED = "DECOMMISSIONED"


class IncidentType(str, Enum):
    """Classification of an operational incident."""

    ACCIDENT = "ACCIDENT"
    BREAKDOWN = "BREAKDOWN"
    TRAFFIC_VIOLATION = "TRAFFIC_VIOLATION"
    THEFT_VANDALISM = "THEFT_VANDALISM"
    PROPERTY_DAMAGE = "PROPERTY_DAMAGE"
    NEAR_MISS = "NEAR_MISS"
    OTHER = "OTHER"


class IncidentSeverity(str, Enum):
    """Severity tier for prioritizing operational incidents."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    """Workflow state of an incident resolution cycle."""

    REPORTED = "REPORTED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


# -- Vehicle Requests & Responses --------------------------------------------


class CreateVehicleRequest(BaseModel):
    """Request payload to register a new vehicle into the fleet master."""

    license_plate: str = Field(min_length=1, max_length=32, description="Vehicle license plate / registration number.")
    make: str = Field(min_length=1, max_length=64, description="Vehicle manufacturer (e.g. Toyota).")
    model: str = Field(min_length=1, max_length=64, description="Vehicle model name (e.g. Hilux).")
    year: int | None = Field(default=None, ge=1900, le=2100, description="Manufacturing year.")
    vehicle_type: VehicleType = Field(default=VehicleType.SEDAN, description="Vehicle body/equipment category.")
    vin: str | None = Field(default=None, description="Optional 17-character Vehicle Identification Number.")
    initial_odometer: Decimal = Field(
        default=Decimal("0.00"), ge=0, description="Initial odometer reading (must be non-negative)."
    )

    @field_validator("license_plate")
    @classmethod
    def _validate_license_plate(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("license_plate cannot be blank or whitespace-only.")
        return cleaned

    @field_validator("make", "model")
    @classmethod
    def _validate_non_blank(cls, value: str, info: object) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{getattr(info, 'field_name', 'field')} cannot be blank or whitespace-only.")
        return cleaned

    @field_validator("vin")
    @classmethod
    def _validate_vin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if not cleaned:
            return None
        if not _VIN_PATTERN.match(cleaned):
            raise ValueError("VIN must be a valid 17-character alphanumeric string (excluding I, O, Q).")
        return cleaned


class AssignDriverRequest(BaseModel):
    """Request payload to assign a driver to a vehicle."""

    vehicle_id: str = Field(min_length=1, description="Target vehicle unique ID.")
    driver_id: str = Field(min_length=1, max_length=64, description="Opaque driver / employee ID.")

    @field_validator("vehicle_id", "driver_id")
    @classmethod
    def _validate_id(cls, value: str, info: object) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{getattr(info, 'field_name', 'field')} cannot be blank.")
        return cleaned


class UnassignDriverRequest(BaseModel):
    """Request payload to release driver assignment from a vehicle."""

    vehicle_id: str = Field(min_length=1, description="Target vehicle unique ID.")

    @field_validator("vehicle_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("vehicle_id cannot be blank.")
        return cleaned


class UpdateVehicleStatusRequest(BaseModel):
    """Request payload to transition vehicle operational status."""

    vehicle_id: str = Field(min_length=1, description="Target vehicle unique ID.")
    new_status: VehicleStatus = Field(description="Target status.")
    reason: str | None = Field(default=None, max_length=500, description="Optional rationale for status change.")

    @field_validator("vehicle_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("vehicle_id cannot be blank.")
        return cleaned


class RecordVehicleTrackingRequest(BaseModel):
    """Request payload to record an odometer log and optional location check-in."""

    vehicle_id: str = Field(min_length=1, description="Target vehicle unique ID.")
    odometer_reading: Decimal = Field(ge=0, description="Odometer reading at time of log.")
    location_name: str | None = Field(default=None, max_length=255, description="Descriptive location name.")
    recorded_at: datetime | None = Field(default=None, description="Optional timestamp; defaults to UTC now.")
    driver_id: str | None = Field(default=None, max_length=64, description="Optional driver who logged the reading.")
    notes: str | None = Field(default=None, max_length=1000, description="Optional notes.")

    @field_validator("vehicle_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("vehicle_id cannot be blank.")
        return cleaned

    @field_validator("recorded_at")
    @classmethod
    def _validate_recorded_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        now = datetime.now(UTC)
        # Allow up to 60 seconds of clock skew
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if (value - now).total_seconds() > 60:
            raise ValueError("recorded_at cannot be in the future.")
        return value


class GetVehicleTrackingHistoryRequest(BaseModel):
    """Request to retrieve reverse-chronological tracking logs for a vehicle."""

    vehicle_id: str = Field(min_length=1, description="Target vehicle unique ID.")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum records to return.")
    offset: int = Field(default=0, ge=0, description="Pagination offset.")

    @field_validator("vehicle_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("vehicle_id cannot be blank.")
        return cleaned


class ListVehiclesRequest(BaseModel):
    """Request to query and paginate fleet vehicles."""

    status: VehicleStatus | None = Field(default=None, description="Optional filter by vehicle status.")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum vehicles to return.")
    offset: int = Field(default=0, ge=0, description="Pagination offset.")


class VehicleResponse(BaseModel):
    """Projection of a vehicle entity."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    license_plate: str
    vin: str | None
    make: str
    model: str
    year: int | None
    vehicle_type: VehicleType
    status: VehicleStatus
    current_odometer: Decimal
    assigned_driver_id: str | None
    assigned_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VehicleTrackingRecordResponse(BaseModel):
    """Projection of a vehicle tracking entry."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    vehicle_id: str
    recorded_at: datetime
    odometer_reading: Decimal
    location_name: str | None
    driver_id: str | None
    notes: str | None
    recorded_by: str
    created_at: datetime


# -- Incident Requests & Responses -------------------------------------------


class ReportIncidentRequest(BaseModel):
    """Request payload to file an operational incident report."""

    incident_type: IncidentType = Field(description="Categorization of the incident.")
    severity: IncidentSeverity = Field(description="Incident severity tier.")
    title: str = Field(min_length=1, max_length=255, description="Brief incident summary.")
    description: str = Field(min_length=1, description="Full detailed incident narrative.")
    occurred_at: datetime = Field(description="Timestamp when the incident occurred.")
    vehicle_id: str | None = Field(default=None, max_length=36, description="Associated vehicle ID if applicable.")
    driver_id: str | None = Field(default=None, max_length=64, description="Involved driver/personnel ID.")
    location: str | None = Field(default=None, max_length=255, description="Physical location or address.")
    estimated_cost: Decimal | None = Field(
        default=None, ge=0, description="Estimated financial impact; must be non-negative."
    )

    @field_validator("title", "description")
    @classmethod
    def _validate_non_blank_text(cls, value: str, info: object) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{getattr(info, 'field_name', 'field')} cannot be blank or whitespace-only.")
        return cleaned

    @field_validator("occurred_at")
    @classmethod
    def _validate_occurred_at(cls, value: datetime) -> datetime:
        now = datetime.now(UTC)
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        if (value - now).total_seconds() > 60:
            raise ValueError("occurred_at cannot be in the future.")
        return value


class ResolveIncidentRequest(BaseModel):
    """Request payload to mark an incident as RESOLVED with formal notes."""

    incident_id: str = Field(min_length=1, description="Target incident unique ID.")
    resolution_notes: str = Field(min_length=1, description="Investigation findings and resolution actions.")

    @field_validator("incident_id", "resolution_notes")
    @classmethod
    def _validate_non_blank(cls, value: str, info: object) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{getattr(info, 'field_name', 'field')} cannot be blank.")
        return cleaned


class CloseIncidentRequest(BaseModel):
    """Request payload to formally close and seal an incident record."""

    incident_id: str = Field(min_length=1, description="Target incident unique ID.")
    closing_notes: str | None = Field(default=None, max_length=1000, description="Optional closure summary notes.")

    @field_validator("incident_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("incident_id cannot be blank.")
        return cleaned


class UpdateIncidentStatusRequest(BaseModel):
    """Request payload to transition an incident to an intermediate status."""

    incident_id: str = Field(min_length=1, description="Target incident unique ID.")
    status: IncidentStatus = Field(description="Requested intermediate target status.")

    @field_validator("incident_id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("incident_id cannot be blank.")
        return cleaned


class ListIncidentsRequest(BaseModel):
    """Request to query and paginate incident reports."""

    status: IncidentStatus | None = Field(default=None, description="Optional status filter.")
    severity: IncidentSeverity | None = Field(default=None, description="Optional severity filter.")
    vehicle_id: str | None = Field(default=None, description="Optional vehicle ID filter.")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum incidents to return.")
    offset: int = Field(default=0, ge=0, description="Pagination offset.")


class IncidentResponse(BaseModel):
    """Projection of an incident record."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    incident_number: str
    incident_type: IncidentType
    severity: IncidentSeverity
    status: IncidentStatus
    title: str
    description: str
    occurred_at: datetime
    reported_at: datetime
    reported_by_id: str
    vehicle_id: str | None
    driver_id: str | None
    location: str | None
    estimated_cost: Decimal | None
    resolution_notes: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    closed_at: datetime | None
    closed_by: str | None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "AssignDriverRequest",
    "CloseIncidentRequest",
    "CreateVehicleRequest",
    "GetVehicleTrackingHistoryRequest",
    "IncidentResponse",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentType",
    "ListIncidentsRequest",
    "ListVehiclesRequest",
    "RecordVehicleTrackingRequest",
    "ReportIncidentRequest",
    "ResolveIncidentRequest",
    "UnassignDriverRequest",
    "UpdateIncidentStatusRequest",
    "UpdateVehicleStatusRequest",
    "VehicleResponse",
    "VehicleStatus",
    "VehicleTrackingRecordResponse",
    "VehicleType",
]
