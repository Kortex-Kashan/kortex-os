"""add browser_execution_json column to ai_agent_tasks

Revision ID: b6c1d2e3f4a5
Revises: a1b2c9d3e4f5
Create Date: 2026-09-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6c1d2e3f4a5"
down_revision: str | None = "a1b2c9d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_agent_tasks", sa.Column("browser_execution_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_agent_tasks", "browser_execution_json")
