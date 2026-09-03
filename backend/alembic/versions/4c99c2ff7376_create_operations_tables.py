"""create operations tables

Revision ID: 4c99c2ff7376
Revises: c7d8e9f1a2b3
Create Date: 2026-09-03 17:04:22.906518

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c99c2ff7376"
down_revision: str | None = "c7d8e9f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. ops_vehicles
    op.create_table(
        "ops_vehicles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("license_plate", sa.String(length=32), nullable=False),
        sa.Column("vin", sa.String(length=64), nullable=True),
        sa.Column("make", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("vehicle_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="ACTIVE", nullable=False),
        sa.Column("current_odometer", sa.Numeric(precision=12, scale=2), server_default="0.00", nullable=False),
        sa.Column("assigned_driver_id", sa.String(length=64), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "license_plate", name="uq_ops_vehicle_tenant_plate"),
    )
    op.create_index(op.f("ix_ops_vehicles_tenant_id"), "ops_vehicles", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ops_vehicles_vin"), "ops_vehicles", ["vin"], unique=False)
    op.create_index(op.f("ix_ops_vehicles_assigned_driver_id"), "ops_vehicles", ["assigned_driver_id"], unique=False)
    op.create_index("ix_ops_vehicles_tenant_status", "ops_vehicles", ["tenant_id", "status"], unique=False)

    # 2. ops_vehicle_tracking_records
    op.create_table(
        "ops_vehicle_tracking_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("vehicle_id", sa.String(length=36), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer_reading", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("location_name", sa.String(length=255), nullable=True),
        sa.Column("driver_id", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ops_vehicle_tracking_records_tenant_id"), "ops_vehicle_tracking_records", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_ops_vehicle_tracking_records_vehicle_id"),
        "ops_vehicle_tracking_records",
        ["vehicle_id"],
        unique=False,
    )
    op.create_index(
        "ix_ops_tracking_tenant_vehicle_recorded",
        "ops_vehicle_tracking_records",
        ["tenant_id", "vehicle_id", "recorded_at"],
        unique=False,
    )

    # 3. ops_incidents
    op.create_table(
        "ops_incidents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("incident_number", sa.String(length=64), nullable=False),
        sa.Column("incident_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="REPORTED", nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_by_id", sa.String(length=64), nullable=False),
        sa.Column("vehicle_id", sa.String(length=36), nullable=True),
        sa.Column("driver_id", sa.String(length=64), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=64), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "incident_number", name="uq_ops_incident_tenant_number"),
    )
    op.create_index(op.f("ix_ops_incidents_tenant_id"), "ops_incidents", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_ops_incidents_vehicle_id"), "ops_incidents", ["vehicle_id"], unique=False)
    op.create_index("ix_ops_incidents_tenant_status", "ops_incidents", ["tenant_id", "status"], unique=False)
    op.create_index("ix_ops_incidents_tenant_vehicle", "ops_incidents", ["tenant_id", "vehicle_id"], unique=False)


def downgrade() -> None:
    op.drop_table("ops_incidents")
    op.drop_table("ops_vehicle_tracking_records")
    op.drop_table("ops_vehicles")
