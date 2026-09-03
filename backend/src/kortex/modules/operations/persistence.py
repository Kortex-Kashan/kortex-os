"""SQLAlchemy ORM persistence models for the KORTEX Operations business module.

Defines the three canonical Operations relational tables:
- `ops_vehicles`
- `ops_vehicle_tracking_records`
- `ops_incidents`

All models inherit from `kortex.core.db.BaseModel` and enforce strict tenant scoping.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel


class OpsVehicleRow(BaseModel):
    """Fleet vehicle master record scoped by tenant."""

    __tablename__ = "ops_vehicles"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    license_plate: Mapped[str] = mapped_column(String(32), nullable=False)
    vin: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    make: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vehicle_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    current_odometer: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=2), nullable=False, default=Decimal("0.00")
    )
    assigned_driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    assigned_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "license_plate", name="uq_ops_vehicle_tenant_plate"),
        UniqueConstraint("tenant_id", "vin", name="uq_ops_vehicle_tenant_vin"),
        Index("ix_ops_vehicles_tenant_status", "tenant_id", "status"),
    )


class OpsVehicleTrackingRow(BaseModel):
    """Point-in-time vehicle tracking log and odometer reading."""

    __tablename__ = "ops_vehicle_tracking_records"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    vehicle_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    recorded_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odometer_reading: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        Index(
            "ix_ops_tracking_tenant_vehicle_recorded",
            "tenant_id",
            "vehicle_id",
            "recorded_at",
        ),
    )


class OpsIncidentRow(BaseModel):
    """Operational incident report and resolution record."""

    __tablename__ = "ops_incidents"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    incident_number: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="REPORTED")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vehicle_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    driver_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(precision=18, scale=2), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "incident_number", name="uq_ops_incident_tenant_number"),
        Index("ix_ops_incidents_tenant_status", "tenant_id", "status"),
        Index("ix_ops_incidents_tenant_vehicle", "tenant_id", "vehicle_id"),
    )


__all__ = [
    "OpsIncidentRow",
    "OpsVehicleRow",
    "OpsVehicleTrackingRow",
]
