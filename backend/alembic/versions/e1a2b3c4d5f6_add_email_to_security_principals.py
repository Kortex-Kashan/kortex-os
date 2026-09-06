"""add email column to security_principals

Revision ID: e1a2b3c4d5f6
Revises: 4c99c2ff7376
Create Date: 2026-09-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5f6"
down_revision: str | None = "4c99c2ff7376"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("security_principals", sa.Column("email", sa.String(length=320), nullable=True))
    op.create_index(op.f("ix_security_principals_email"), "security_principals", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_security_principals_email"), table_name="security_principals")
    op.drop_column("security_principals", "email")
