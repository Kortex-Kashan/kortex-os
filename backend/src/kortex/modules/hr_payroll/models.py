"""Pydantic v2 domain schemas for the KORTEX HR & Payroll business module.

All request models deliberately omit `tenant_id` fields -- tenant identity is
exclusively derived by the dispatcher and passed via `CapabilityExecutionContext`.
All monetary amounts use `Decimal` with strict arithmetic precision.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ISO_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


# -- Domain Enums -------------------------------------------------------------


class EmployeeStatus(str, Enum):
    """Lifecycle state of an employee."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    TERMINATED = "TERMINATED"


class AttendanceStatus(str, Enum):
    """Daily attendance outcome for an employee."""

    PRESENT = "PRESENT"
    HALF_DAY = "HALF_DAY"
    ABSENT = "ABSENT"


class LeaveType(str, Enum):
    """Class of leave requested or tracked."""

    ANNUAL = "ANNUAL"
    SICK = "SICK"
    CASUAL = "CASUAL"
    UNPAID = "UNPAID"


class LeaveStatus(str, Enum):
    """Workflow state of a leave application."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PayrollRunStatus(str, Enum):
    """Lifecycle status of a monthly payroll run."""

    DRAFT = "DRAFT"
    CALCULATED = "CALCULATED"
    FINALIZED = "FINALIZED"


# -- Employee Domain Models ---------------------------------------------------


class CreateEmployeeRequest(BaseModel):
    """Request payload to register a new employee."""

    employee_code: str = Field(
        min_length=1, max_length=64, description="Unique organizational employee code, e.g. 'EMP-001'."
    )
    first_name: str = Field(min_length=1, max_length=128, description="Employee first name.")
    last_name: str = Field(min_length=1, max_length=128, description="Employee last name.")
    email: str | None = Field(default=None, max_length=255, description="Employee email address.")
    department: str | None = Field(default=None, max_length=128, description="Department name.")
    position: str | None = Field(default=None, max_length=128, description="Job title / position designation.")
    joined_date: date = Field(description="Date the employee joined.")
    base_salary: Decimal = Field(gt=0, description="Monthly base salary; must be strictly positive.")
    currency: str = Field(default="USD", description="3-letter ISO 4217 currency code.")
    initial_annual_leave_days: Decimal | None = Field(
        default=None, ge=0, description="Optional override for initial annual leave days."
    )
    initial_sick_leave_days: Decimal | None = Field(
        default=None, ge=0, description="Optional override for initial sick leave days."
    )
    initial_casual_leave_days: Decimal | None = Field(
        default=None, ge=0, description="Optional override for initial casual leave days."
    )

    @field_validator("employee_code", "first_name", "last_name")
    @classmethod
    def _strip_and_validate_non_empty(cls, value: str, info: object) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{getattr(info, 'field_name', 'field')} cannot be blank or whitespace-only.")
        return stripped

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _ISO_CURRENCY_PATTERN.match(normalized):
            raise ValueError(f"currency must be a 3-letter ISO 4217 code, got {value!r}.")
        return normalized


class EmployeeResponse(BaseModel):
    """Immutable snapshot of a persisted employee profile."""

    model_config = ConfigDict(frozen=True)

    employee_id: str
    tenant_id: str
    employee_code: str
    first_name: str
    last_name: str
    email: str | None
    department: str | None
    position: str | None
    status: EmployeeStatus
    joined_date: date
    base_salary: Decimal
    currency: str
    created_at: datetime
    updated_at: datetime


class ListEmployeesRequest(BaseModel):
    """Query parameters to filter and paginate employee profiles."""

    status: EmployeeStatus | None = None
    department: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


# -- Attendance Domain Models -------------------------------------------------


class CheckInRequest(BaseModel):
    """Request payload to record an employee's daily check-in."""

    employee_id: str = Field(min_length=1, description="Target employee ID.")
    work_date: date | None = Field(default=None, description="Calendar date for attendance; defaults to today UTC.")
    check_in_time: datetime | None = Field(
        default=None, description="Timestamp for check-in; defaults to current UTC time."
    )
    notes: str | None = Field(default=None, description="Optional check-in notes.")


class CheckOutRequest(BaseModel):
    """Request payload to record an employee's daily check-out."""

    employee_id: str = Field(min_length=1, description="Target employee ID.")
    work_date: date | None = Field(default=None, description="Calendar date for attendance; defaults to today UTC.")
    check_out_time: datetime | None = Field(
        default=None, description="Timestamp for check-out; defaults to current UTC time."
    )
    notes: str | None = Field(default=None, description="Optional check-out notes.")


class AttendanceResponse(BaseModel):
    """Immutable snapshot of an attendance record."""

    model_config = ConfigDict(frozen=True)

    record_id: str
    tenant_id: str
    employee_id: str
    work_date: date
    check_in: datetime
    check_out: datetime | None
    total_hours: Decimal
    overtime_hours: Decimal
    status: AttendanceStatus
    notes: str | None
    created_at: datetime


class ListAttendanceRequest(BaseModel):
    """Query parameters to filter attendance records by date interval."""

    employee_id: str | None = None
    start_date: date
    end_date: date
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_range(self) -> ListAttendanceRequest:
        if self.end_date < self.start_date:
            raise ValueError(f"end_date ({self.end_date}) cannot precede start_date ({self.start_date}).")
        return self


# -- Leave Domain Models ------------------------------------------------------


class ApplyLeaveRequest(BaseModel):
    """Request payload to submit an employee leave application."""

    employee_id: str = Field(min_length=1, description="Applicant employee ID.")
    leave_type: LeaveType = Field(description="Category of leave.")
    start_date: date = Field(description="Start date of the leave period (inclusive).")
    end_date: date = Field(description="End date of the leave period (inclusive).")
    reason: str = Field(min_length=1, description="Justification for leave.")

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason cannot be blank.")
        return stripped

    @model_validator(mode="after")
    def _validate_dates(self) -> ApplyLeaveRequest:
        if self.end_date < self.start_date:
            raise ValueError(f"end_date ({self.end_date}) cannot precede start_date ({self.start_date}).")
        return self


class DecideLeaveRequest(BaseModel):
    """Request payload to approve or reject a pending leave application."""

    leave_id: str = Field(min_length=1, description="Target leave request ID.")
    decision: str = Field(pattern="^(APPROVE|REJECT)$", description="Must be 'APPROVE' or 'REJECT'.")
    decision_reason: str | None = Field(default=None, description="Optional explanation for decision.")


class LeaveRequestResponse(BaseModel):
    """Immutable snapshot of a leave application."""

    model_config = ConfigDict(frozen=True)

    leave_id: str
    tenant_id: str
    employee_id: str
    leave_type: LeaveType
    start_date: date
    end_date: date
    days_count: Decimal
    reason: str
    status: LeaveStatus
    decision_reason: str | None
    decided_at: datetime | None
    decided_by: str | None
    created_at: datetime


class LeaveBalanceResponse(BaseModel):
    """Available and consumed balance for an employee's leave category."""

    model_config = ConfigDict(frozen=True)

    balance_id: str
    tenant_id: str
    employee_id: str
    leave_type: LeaveType
    year: int
    allocated_days: Decimal
    used_days: Decimal
    available_days: Decimal


# -- Payroll Domain Models ----------------------------------------------------


class CalculatePayrollRequest(BaseModel):
    """Request payload to trigger a payroll calculation for a period."""

    period_start: date = Field(description="Start date of the payroll period (inclusive).")
    period_end: date = Field(description="End date of the payroll period (inclusive).")
    finalize: bool = Field(default=False, description="If True, immediately locks the run into FINALIZED state.")

    @model_validator(mode="after")
    def _validate_period(self) -> CalculatePayrollRequest:
        if self.period_end < self.period_start:
            raise ValueError(f"period_end ({self.period_end}) cannot precede period_start ({self.period_start}).")
        return self


class PayrollEntryResponse(BaseModel):
    """Itemized salary and compensation calculation for a single employee in a run."""

    model_config = ConfigDict(frozen=True)

    entry_id: str
    tenant_id: str
    payroll_run_id: str
    employee_id: str
    employee_code: str
    employee_name: str
    base_salary: Decimal
    worked_days: int
    unpaid_leave_days: Decimal
    overtime_hours: Decimal
    overtime_pay: Decimal
    allowances: Decimal
    deductions: Decimal
    unpaid_leave_deduction: Decimal
    gross_salary: Decimal
    total_deductions: Decimal
    net_salary: Decimal
    currency: str
    details_json: str | None


class PayrollRunResponse(BaseModel):
    """Aggregated outcome of a monthly payroll run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    tenant_id: str
    period_start: date
    period_end: date
    currency: str
    total_gross: Decimal
    total_deductions: Decimal
    total_net: Decimal
    employee_count: int
    status: PayrollRunStatus
    finalized_at: datetime | None
    entries: list[PayrollEntryResponse] = Field(default_factory=list)
    created_at: datetime


class PayslipResponse(BaseModel):
    """Individual employee payslip projection."""

    model_config = ConfigDict(frozen=True)

    payslip_id: str
    tenant_id: str
    run_id: str
    period_start: date
    period_end: date
    employee_id: str
    employee_code: str
    employee_name: str
    department: str | None
    position: str | None
    base_salary: Decimal
    worked_days: int
    unpaid_leave_days: Decimal
    unpaid_leave_deduction: Decimal
    overtime_hours: Decimal
    overtime_pay: Decimal
    allowances: Decimal
    deductions: Decimal
    gross_salary: Decimal
    total_deductions: Decimal
    net_salary: Decimal
    currency: str
    generated_at: datetime


__all__ = [
    "ApplyLeaveRequest",
    "AttendanceResponse",
    "AttendanceStatus",
    "CalculatePayrollRequest",
    "CheckInRequest",
    "CheckOutRequest",
    "CreateEmployeeRequest",
    "DecideLeaveRequest",
    "EmployeeResponse",
    "EmployeeStatus",
    "LeaveBalanceResponse",
    "LeaveRequestResponse",
    "LeaveStatus",
    "LeaveType",
    "ListAttendanceRequest",
    "ListEmployeesRequest",
    "PayrollEntryResponse",
    "PayrollRunResponse",
    "PayrollRunStatus",
    "PayslipResponse",
]
