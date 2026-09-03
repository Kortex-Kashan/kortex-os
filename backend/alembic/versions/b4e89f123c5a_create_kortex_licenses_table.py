"""create kortex_licenses table

Revision ID: b4e89f123c5a
Revises: 81d6d64c51ba
Create Date: 2026-09-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e89f123c5a"
down_revision: str | None = "81d6d64c51ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kortex_licenses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("license_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("active_tenant_id", sa.String(length=64), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_token", sa.Text(), nullable=False),
        sa.Column("kid", sa.String(length=64), nullable=False),
        sa.Column("signature_hex", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_period_days", sa.Integer(), nullable=False),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("quotas_json", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_by", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("highest_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grace_event_emitted", sa.Boolean(), nullable=False, default=False),
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
        sa.UniqueConstraint("active_tenant_id", name="uq_active_license_per_tenant"),
    )
    op.create_index("idx_licenses_tenant_status", "kortex_licenses", ["tenant_id", "status"], unique=False)
    op.create_index(op.f("ix_kortex_licenses_license_id"), "kortex_licenses", ["license_id"], unique=True)
    op.create_index(op.f("ix_kortex_licenses_status"), "kortex_licenses", ["status"], unique=False)
    op.create_index(op.f("ix_kortex_licenses_tenant_id"), "kortex_licenses", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_kortex_licenses_tenant_id"), table_name="kortex_licenses")
    op.drop_index(op.f("ix_kortex_licenses_status"), table_name="kortex_licenses")
    op.drop_index(op.f("ix_kortex_licenses_license_id"), table_name="kortex_licenses")
    op.drop_index("idx_licenses_tenant_status", table_name="kortex_licenses")
    op.drop_table("kortex_licenses")
