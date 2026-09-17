"""create Python Action and execution-lineage tables (Python + Desktop Automation)

Revision ID: a7f3c19d4b20
Revises: 4d094027c916
Create Date: 2026-09-16 00:00:00.000000

Additive migration for the Python + Desktop Automation milestone. Adds exactly three new
tables and alters, drops, or rewrites nothing that already exists.

`python_actions` holds the stable identity of a tenant-owned Python Action; `python_action_versions`
holds its immutable published versions; `python_execution_records` holds sanitized execution
lineage.

Two unique constraints carry real semantics rather than being incidental indexes:

* `uq_python_actions_tenant_action` makes `(tenant_id, action_id)` the tenant-scoped natural key,
  so two tenants may independently use the same `action_id` while one tenant can never hold two
  actions under the same id.
* `uq_python_action_versions_identity` makes `(tenant_id, action_id, version)` unique, which is the
  database-level half of the immutability guarantee: `PythonActionManager` only ever INSERTs into
  this table, and a re-publish of an existing version therefore fails as an integrity error rather
  than silently overwriting a version some workflow has already pinned.

`python_execution_records` deliberately stores no stdout payload and no environment: the structured
result is returned to the workflow and audited through the existing `AuditManager`, and this table
is the durable, queryable execution lineage rather than a second artifact store.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7f3c19d4b20"
down_revision: str | None = "4d094027c916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "python_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("action_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("latest_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "action_id", name="uq_python_actions_tenant_action"),
    )
    op.create_index("ix_python_actions_tenant_id", "python_actions", ["tenant_id"])
    op.create_index("ix_python_actions_action_id", "python_actions", ["action_id"])

    op.create_table(
        "python_action_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("action_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("entrypoint", sa.String(length=128), server_default="main", nullable=False),
        sa.Column("trust_level", sa.String(length=16), nullable=False),
        sa.Column("allowed_capabilities_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("network_policy", sa.String(length=16), server_default="DENY", nullable=False),
        sa.Column("network_allow_list_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("limits_json", sa.Text(), server_default="{}", nullable=False),
        sa.Column("requirements_lock_json", sa.Text(), server_default="[]", nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "action_id", "version", name="uq_python_action_versions_identity"),
    )
    op.create_index("ix_python_action_versions_tenant_id", "python_action_versions", ["tenant_id"])
    op.create_index("ix_python_action_versions_action_id", "python_action_versions", ["action_id"])

    op.create_table(
        "python_execution_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("action_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trust_level", sa.String(length=16), nullable=False),
        sa.Column("boundary", sa.String(length=48), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("capability_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("workflow_id", sa.String(length=64), nullable=True),
        sa.Column("execution_id", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("principal_id", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
    op.create_index("ix_python_execution_records_tenant_id", "python_execution_records", ["tenant_id"])
    op.create_index("ix_python_execution_records_action_id", "python_execution_records", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_python_execution_records_action_id", table_name="python_execution_records")
    op.drop_index("ix_python_execution_records_tenant_id", table_name="python_execution_records")
    op.drop_table("python_execution_records")

    op.drop_index("ix_python_action_versions_action_id", table_name="python_action_versions")
    op.drop_index("ix_python_action_versions_tenant_id", table_name="python_action_versions")
    op.drop_table("python_action_versions")

    op.drop_index("ix_python_actions_action_id", table_name="python_actions")
    op.drop_index("ix_python_actions_tenant_id", table_name="python_actions")
    op.drop_table("python_actions")
