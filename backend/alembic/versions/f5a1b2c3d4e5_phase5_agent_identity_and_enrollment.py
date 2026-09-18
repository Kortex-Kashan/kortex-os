"""add agent machine installation binding and enrollment tokens (Phase 5)

Revision ID: f5a1b2c3d4e5
Revises: a7f3c19d4b20
Create Date: 2026-09-17 00:00:00.000000

Additive migration for the Phase 5 agent identity / enrollment milestone. Adds
exactly one column to an existing table and exactly one new table; it alters,
drops, or rewrites nothing else.

`security_principals.machine_installation_id` binds an `AGENT` principal to the
single Windows installation it was enrolled from. It is globally unique rather
than tenant-scoped, and that uniqueness is the load-bearing half of the
"one installation, one principal" guarantee: the enrollment service's own
pre-flight `SELECT` is only an optimization that produces a clean HTTP 409, and
two concurrent enrollments that both pass that pre-check are still separated
here, by the database, not by the application.

The constraint is materialized twice on purpose, mirroring what
`Base.metadata.create_all()` emits for the same model so the two schema-build
paths stay byte-comparable under
`test_create_all_and_alembic_schema_are_equivalent`: a named table-level
`UNIQUE` constraint (`uq_security_principals_machine_id`) and a unique index
(`ix_security_principals_machine_installation_id`). SQLite cannot add a named
table-level constraint via `ALTER TABLE`, so the column and constraint are
applied inside `batch_alter_table`, which rebuilds the table.

`security_agent_enrollment_tokens` stores only `SHA-256(raw_token)`. There is
deliberately no column capable of holding a raw token: the raw value is handed
to the administrator once at generation and never persisted anywhere.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a1b2c3d4e5"
down_revision: str | None = "a7f3c19d4b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("security_principals") as batch_op:
        batch_op.add_column(sa.Column("machine_installation_id", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint("uq_security_principals_machine_id", ["machine_installation_id"])
    op.create_index(
        op.f("ix_security_principals_machine_installation_id"),
        "security_principals",
        ["machine_installation_id"],
        unique=True,
    )

    op.create_table(
        "security_agent_enrollment_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrollment_principal_id", sa.String(length=255), nullable=True),
        sa.Column("enrollment_csr_hash", sa.String(length=64), nullable=True),
        sa.Column("enrollment_machine_installation_id", sa.String(length=64), nullable=True),
        sa.Column("issued_certificate", sa.LargeBinary(), nullable=True),
        sa.Column("certificate_fingerprint", sa.String(length=64), nullable=True),
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
        op.f("ix_security_agent_enrollment_tokens_tenant_id"),
        "security_agent_enrollment_tokens",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_security_agent_enrollment_tokens_token_hash"),
        "security_agent_enrollment_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_security_agent_enrollment_tokens_token_hash"),
        table_name="security_agent_enrollment_tokens",
    )
    op.drop_index(
        op.f("ix_security_agent_enrollment_tokens_tenant_id"),
        table_name="security_agent_enrollment_tokens",
    )
    op.drop_table("security_agent_enrollment_tokens")

    op.drop_index(op.f("ix_security_principals_machine_installation_id"), table_name="security_principals")
    with op.batch_alter_table("security_principals") as batch_op:
        batch_op.drop_constraint("uq_security_principals_machine_id", type_="unique")
        batch_op.drop_column("machine_installation_id")
