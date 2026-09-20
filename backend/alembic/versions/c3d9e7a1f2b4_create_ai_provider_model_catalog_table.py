"""create ai_provider_model_catalog table

Revision ID: c3d9e7a1f2b4
Revises: f5a1b2c3d4e5
Create Date: 2026-09-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d9e7a1f2b4"
down_revision: str | None = "f5a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_provider_model_catalog",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("provider_id", sa.String(length=128), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("provider_display_name", sa.String(length=255), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider_id", "model_id", name="uq_ai_provider_model_catalog_entry"),
    )
    op.create_index(
        "ix_ai_provider_model_catalog_lookup",
        "ai_provider_model_catalog",
        ["tenant_id", "provider_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_provider_model_catalog_lookup", table_name="ai_provider_model_catalog")
    op.drop_table("ai_provider_model_catalog")
