"""add user-scoped index to ai_conversation_turns

Revision ID: a1b2c9d3e4f5
Revises: c3d9e7a1f2b4
Create Date: 2026-09-19 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c9d3e4f5"
down_revision: str | None = "c3d9e7a1f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ai_conversation_turn_user_lookup",
        "ai_conversation_turns",
        ["tenant_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_conversation_turn_user_lookup", table_name="ai_conversation_turns")
