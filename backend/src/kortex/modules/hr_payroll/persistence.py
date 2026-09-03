"""SQLAlchemy ORM persistence models for the KORTEX HR & Payroll business module.

Defines the six canonical HR & Payroll relational tables:
- `hr_employees`
- `hr_attendance_records`
- `hr_leave_balances`
- `hr_leave_requests`
- `hr_payroll_runs`
- `hr_payroll_entries`

All models inherit from `kortex.core.db.BaseModel` and enforce strict tenant scoping.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kortex.core.db import BaseModel


class HREmployeeRow(BaseModel):
    """Employee master record scoped by tenant."""

    __tablename__ = "hr_employees"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False)
    first_name: Mapped[str] = mapped_column(String(128), nullable=False)
    last_name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    joined_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    base_salary: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    __table_args__ = (UniqueConstraint("tenant_id", "employee_code", name="uq_hr_employee_tenant_code"),)


class HRAttendanceRow(BaseModel):
    """Daily attendance and time tracking record for an employee."""

    __tablename__ = "hr_attendance_records"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    work_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    check_in: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_hours: Mapped[Decimal] = mapped_column(Numeric(precision=6, scale=2), nullable=False, default=Decimal("0.00"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "employee_id", "work_date", name="uq_hr_attendance_employee_date"),)


class HRLeaveBalanceRow(BaseModel):
    """Leave balance and quota tracking for an employee per year."""

    __tablename__ = "hr_leave_balances"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    leave_type: Mapped[str] = mapped_column(String(32), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    allocated_days: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=1), nullable=False, default=Decimal("0.0")
    )
    used_days: Mapped[Decimal] = mapped_column(Numeric(precision=5, scale=1), nullable=False, default=Decimal("0.0"))

    __table_args__ = (
        UniqueConstraint("tenant_id", "employee_id", "leave_type", "year", name="uq_hr_leave_balance_emp_type_year"),
    )


class HRLeaveRequestRow(BaseModel):
    """Leave application submitted by an employee."""

    __tablename__ = "hr_leave_requests"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    leave_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    days_count: Mapped[Decimal] = mapped_column(Numeric(precision=5, scale=1), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class HRPayrollRunRow(BaseModel):
    """Payroll batch calculation run for a specific calendar period."""

    __tablename__ = "hr_payroll_runs"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    period_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_gross: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0.00")
    )
    total_deductions: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0.00")
    )
    total_net: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False, default=Decimal("0.00"))
    employee_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    finalized_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", "period_start", "period_end", name="uq_hr_payroll_run_period"),)


class HRPayrollEntryRow(BaseModel):
    """Itemized individual compensation record within a payroll run."""

    __tablename__ = "hr_payroll_entries"

    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    payroll_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    base_salary: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    worked_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unpaid_leave_days: Mapped[Decimal] = mapped_column(
        Numeric(precision=5, scale=1), nullable=False, default=Decimal("0.0")
    )
    overtime_hours: Mapped[Decimal] = mapped_column(
        Numeric(precision=6, scale=2), nullable=False, default=Decimal("0.00")
    )
    overtime_pay: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0.00")
    )
    allowances: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False, default=Decimal("0.00"))
    deductions: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False, default=Decimal("0.00"))
    gross_salary: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    total_deductions: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    net_salary: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("payroll_run_id", "employee_id", name="uq_hr_payroll_entry_run_employee"),)


__all__ = [
    "HRAttendanceRow",
    "HREmployeeRow",
    "HRLeaveBalanceRow",
    "HRLeaveRequestRow",
    "HRPayrollEntryRow",
    "HRPayrollRunRow",
]
