"""create hr_payroll tables

Revision ID: c7d8e9f1a2b3
Revises: b4e89f123c5a
Create Date: 2026-09-03 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f1a2b3"
down_revision: str | None = "b4e89f123c5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. hr_employees
    op.create_table(
        "hr_employees",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("employee_code", sa.String(length=64), nullable=False),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("last_name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("department", sa.String(length=128), nullable=True),
        sa.Column("position", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("joined_date", sa.Date(), nullable=False),
        sa.Column("base_salary", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
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
        sa.UniqueConstraint("tenant_id", "employee_code", name="uq_hr_employee_tenant_code"),
    )
    op.create_index(op.f("ix_hr_employees_tenant_id"), "hr_employees", ["tenant_id"], unique=False)

    # 2. hr_attendance_records
    op.create_table(
        "hr_attendance_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_hours", sa.Numeric(precision=6, scale=2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "employee_id", "work_date", name="uq_hr_attendance_employee_date"),
    )
    op.create_index(op.f("ix_hr_attendance_records_tenant_id"), "hr_attendance_records", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_hr_attendance_records_employee_id"), "hr_attendance_records", ["employee_id"], unique=False
    )

    # 3. hr_leave_balances
    op.create_table(
        "hr_leave_balances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=False),
        sa.Column("leave_type", sa.String(length=32), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("allocated_days", sa.Numeric(precision=5, scale=1), nullable=False, server_default="0.0"),
        sa.Column("used_days", sa.Numeric(precision=5, scale=1), nullable=False, server_default="0.0"),
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
        sa.UniqueConstraint("tenant_id", "employee_id", "leave_type", "year", name="uq_hr_leave_balance_emp_type_year"),
    )
    op.create_index(op.f("ix_hr_leave_balances_tenant_id"), "hr_leave_balances", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_hr_leave_balances_employee_id"), "hr_leave_balances", ["employee_id"], unique=False)

    # 4. hr_leave_requests
    op.create_table(
        "hr_leave_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=False),
        sa.Column("leave_type", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("days_count", sa.Numeric(precision=5, scale=1), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.String(length=64), nullable=True),
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
    op.create_index(op.f("ix_hr_leave_requests_tenant_id"), "hr_leave_requests", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_hr_leave_requests_employee_id"), "hr_leave_requests", ["employee_id"], unique=False)

    # 5. hr_payroll_runs
    op.create_table(
        "hr_payroll_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_gross", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0.00"),
        sa.Column("total_deductions", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0.00"),
        sa.Column("total_net", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0.00"),
        sa.Column("employee_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "period_start", "period_end", name="uq_hr_payroll_run_period"),
    )
    op.create_index(op.f("ix_hr_payroll_runs_tenant_id"), "hr_payroll_runs", ["tenant_id"], unique=False)

    # 6. hr_payroll_entries
    op.create_table(
        "hr_payroll_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("payroll_run_id", sa.String(length=36), nullable=False),
        sa.Column("employee_id", sa.String(length=36), nullable=False),
        sa.Column("base_salary", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("worked_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unpaid_leave_days", sa.Numeric(precision=5, scale=1), nullable=False, server_default="0.0"),
        sa.Column("overtime_hours", sa.Numeric(precision=6, scale=2), nullable=False, server_default="0.00"),
        sa.Column("overtime_pay", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0.00"),
        sa.Column("allowances", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0.00"),
        sa.Column("deductions", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0.00"),
        sa.Column("gross_salary", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("total_deductions", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("net_salary", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("payroll_run_id", "employee_id", name="uq_hr_payroll_entry_run_employee"),
    )
    op.create_index(op.f("ix_hr_payroll_entries_tenant_id"), "hr_payroll_entries", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_hr_payroll_entries_payroll_run_id"), "hr_payroll_entries", ["payroll_run_id"], unique=False
    )
    op.create_index(op.f("ix_hr_payroll_entries_employee_id"), "hr_payroll_entries", ["employee_id"], unique=False)


def downgrade() -> None:
    # Drop entries
    op.drop_index(op.f("ix_hr_payroll_entries_employee_id"), table_name="hr_payroll_entries")
    op.drop_index(op.f("ix_hr_payroll_entries_payroll_run_id"), table_name="hr_payroll_entries")
    op.drop_index(op.f("ix_hr_payroll_entries_tenant_id"), table_name="hr_payroll_entries")
    op.drop_table("hr_payroll_entries")

    # Drop payroll runs
    op.drop_index(op.f("ix_hr_payroll_runs_tenant_id"), table_name="hr_payroll_runs")
    op.drop_table("hr_payroll_runs")

    # Drop leave requests
    op.drop_index(op.f("ix_hr_leave_requests_employee_id"), table_name="hr_leave_requests")
    op.drop_index(op.f("ix_hr_leave_requests_tenant_id"), table_name="hr_leave_requests")
    op.drop_table("hr_leave_requests")

    # Drop leave balances
    op.drop_index(op.f("ix_hr_leave_balances_employee_id"), table_name="hr_leave_balances")
    op.drop_index(op.f("ix_hr_leave_balances_tenant_id"), table_name="hr_leave_balances")
    op.drop_table("hr_leave_balances")

    # Drop attendance records
    op.drop_index(op.f("ix_hr_attendance_records_employee_id"), table_name="hr_attendance_records")
    op.drop_index(op.f("ix_hr_attendance_records_tenant_id"), table_name="hr_attendance_records")
    op.drop_table("hr_attendance_records")

    # Drop employees
    op.drop_index(op.f("ix_hr_employees_tenant_id"), table_name="hr_employees")
    op.drop_table("hr_employees")
