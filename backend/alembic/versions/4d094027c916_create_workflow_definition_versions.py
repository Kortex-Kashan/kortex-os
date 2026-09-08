"""create workflow_definition_versions table (Milestone F4)

Revision ID: 4d094027c916
Revises: e1a2b3c4d5f6
Create Date: 2026-09-08 00:00:00.000000

Additive migration for Milestone F4 -- Workflow Definition Lifecycle & Authoring Foundation. Adds
exactly one new table (D2/D20's ratified "exactly ONE additive persistence structure"):
`workflow_definition_versions`. Does not alter, drop, or rewrite any existing table -- in
particular `workflow_definitions` and its `steps_json` semantics are untouched; that table
continues to serve its pre-F4 role as the flat projection the unmodified executor reads, now kept
in sync transactionally on publish (see `WorkflowStore.publish_draft`) rather than freely writable.

At most one row per `(tenant_id, definition_id)` holds `version='0.0.0-draft'` -- the single mutable
DRAFT/ARCHIVED control row -- enforced by `uq_workflow_definition_version_tenant_def_version`; any
number of rows hold a real SemVer `version` with `status='PUBLISHED'`, each an immutable snapshot.
No foreign key to `workflow_definitions.id`: a brand-new F4-authored definition may exist here as a
DRAFT before any corresponding `workflow_definitions` row exists (see `models.py`'s
`WorkflowDefinitionVersion` docstring for the "Git-like" rationale).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4d094027c916"
down_revision: str | None = "e1a2b3c4d5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_definition_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("definition_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), server_default="default", nullable=False),
        sa.Column("version", sa.String(length=32), server_default="0.0.0-draft", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="DRAFT", nullable=False),
        sa.Column("name", sa.String(length=128), server_default="", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("trigger_type", sa.String(length=32), server_default="MANUAL", nullable=False),
        sa.Column("priority", sa.String(length=32), server_default="NORMAL", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="3600", nullable=False),
        sa.Column("steps_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("graph_json", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), server_default="SYSTEM", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "tenant_id", "definition_id", "version", name="uq_workflow_definition_version_tenant_def_version"
        ),
    )
    op.create_index(
        op.f("ix_workflow_definition_versions_definition_id"),
        "workflow_definition_versions",
        ["definition_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflow_definition_versions_tenant_id"), "workflow_definition_versions", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_workflow_definition_versions_status"), "workflow_definition_versions", ["status"], unique=False
    )
    op.create_index(
        "ix_workflow_definition_versions_lookup",
        "workflow_definition_versions",
        ["tenant_id", "definition_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("workflow_definition_versions")
