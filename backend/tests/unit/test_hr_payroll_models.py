"""Unit tests for HR & Payroll Pydantic domain models and validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from kortex.modules.hr_payroll.models import (
    ApplyLeaveRequest,
    CalculatePayrollRequest,
    CreateEmployeeRequest,
    DecideLeaveRequest,
    LeaveType,
    ListAttendanceRequest,
)


def test_create_employee_valid() -> None:
    req = CreateEmployeeRequest(
        employee_code="EMP-001",
        first_name="Alice",
        last_name="Smith",
        email="alice@example.com",
        department="Engineering",
        position="Senior Engineer",
        joined_date=date(2026, 1, 15),
        base_salary=Decimal("5000.00"),
        currency="USD",
    )
    assert req.employee_code == "EMP-001"
    assert req.first_name == "Alice"
    assert req.base_salary == Decimal("5000.00")
    assert req.currency == "USD"


def test_create_employee_whitespace_stripping() -> None:
    req = CreateEmployeeRequest(
        employee_code="  EMP-002  ",
        first_name="  Bob  ",
        last_name="  Jones  ",
        joined_date=date(2026, 2, 1),
        base_salary=Decimal("4200.00"),
    )
    assert req.employee_code == "EMP-002"
    assert req.first_name == "Bob"
    assert req.last_name == "Jones"


def test_create_employee_empty_code_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        CreateEmployeeRequest(
            employee_code="   ",
            first_name="Alice",
            last_name="Smith",
            joined_date=date(2026, 1, 1),
            base_salary=Decimal("5000.00"),
        )
    assert "employee_code" in str(exc.value)


def test_create_employee_empty_names_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateEmployeeRequest(
            employee_code="EMP-001",
            first_name="   ",
            last_name="Smith",
            joined_date=date(2026, 1, 1),
            base_salary=Decimal("5000.00"),
        )


def test_create_employee_negative_or_zero_salary_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateEmployeeRequest(
            employee_code="EMP-001",
            first_name="Alice",
            last_name="Smith",
            joined_date=date(2026, 1, 1),
            base_salary=Decimal("0.00"),
        )
    with pytest.raises(ValidationError):
        CreateEmployeeRequest(
            employee_code="EMP-001",
            first_name="Alice",
            last_name="Smith",
            joined_date=date(2026, 1, 1),
            base_salary=Decimal("-150.00"),
        )


def test_create_employee_invalid_currency_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateEmployeeRequest(
            employee_code="EMP-001",
            first_name="Alice",
            last_name="Smith",
            joined_date=date(2026, 1, 1),
            base_salary=Decimal("5000.00"),
            currency="US",
        )
    with pytest.raises(ValidationError):
        CreateEmployeeRequest(
            employee_code="EMP-001",
            first_name="Alice",
            last_name="Smith",
            joined_date=date(2026, 1, 1),
            base_salary=Decimal("5000.00"),
            currency="US1",
        )


def test_list_attendance_date_validation() -> None:
    # valid range
    req = ListAttendanceRequest(
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )
    assert req.start_date == date(2026, 9, 1)

    # invalid range: end before start
    with pytest.raises(ValidationError) as exc:
        ListAttendanceRequest(
            start_date=date(2026, 9, 30),
            end_date=date(2026, 9, 1),
        )
    assert "cannot precede start_date" in str(exc.value)


def test_apply_leave_date_validation() -> None:
    # valid application
    req = ApplyLeaveRequest(
        employee_id="emp-123",
        leave_type=LeaveType.ANNUAL,
        start_date=date(2026, 10, 5),
        end_date=date(2026, 10, 9),
        reason="Family vacation",
    )
    assert req.start_date == date(2026, 10, 5)

    # invalid application: end before start
    with pytest.raises(ValidationError) as exc:
        ApplyLeaveRequest(
            employee_id="emp-123",
            leave_type=LeaveType.ANNUAL,
            start_date=date(2026, 10, 9),
            end_date=date(2026, 10, 5),
            reason="Family vacation",
        )
    assert "cannot precede start_date" in str(exc.value)


def test_apply_leave_empty_reason_rejected() -> None:
    with pytest.raises(ValidationError):
        ApplyLeaveRequest(
            employee_id="emp-123",
            leave_type=LeaveType.SICK,
            start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 5),
            reason="   ",
        )


def test_decide_leave_decision_enum() -> None:
    req = DecideLeaveRequest(leave_id="leave-1", decision="APPROVE")
    assert req.decision == "APPROVE"

    req2 = DecideLeaveRequest(leave_id="leave-1", decision="REJECT")
    assert req2.decision == "REJECT"

    with pytest.raises(ValidationError):
        DecideLeaveRequest(leave_id="leave-1", decision="MAYBE")


def test_calculate_payroll_period_validation() -> None:
    req = CalculatePayrollRequest(
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
    )
    assert req.period_start == date(2026, 9, 1)

    with pytest.raises(ValidationError) as exc:
        CalculatePayrollRequest(
            period_start=date(2026, 9, 30),
            period_end=date(2026, 9, 1),
        )
    assert "cannot precede period_start" in str(exc.value)
