"""Pure mathematical calculations and compensation rules for KORTEX HR & Payroll.

Enforces exact Decimal arithmetic and ratified Phase 6 policies:
- DAILY RATE DIVISOR = 30 DAYS
- STANDARD WORK DAY = 8 HOURS
- OVERTIME MULTIPLIER = 1.5x
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

_TWO_PLACES = Decimal("0.01")
_DAYS_DIVISOR = Decimal("30")
_HOURS_PER_DAY = Decimal("8")
_OVERTIME_MULTIPLIER = Decimal("1.5")


def round_money(amount: Decimal) -> Decimal:
    """Quantize to two decimal places using standard bankers rounding."""
    return amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_daily_rate(base_salary: Decimal) -> Decimal:
    """Calculate standard daily compensation rate based on 30-day month."""
    if base_salary <= 0:
        return Decimal("0.00")
    return round_money(base_salary / _DAYS_DIVISOR)


def calculate_hourly_rate(daily_rate: Decimal) -> Decimal:
    """Calculate hourly rate based on standard 8-hour workday."""
    if daily_rate <= 0:
        return Decimal("0.00")
    return round_money(daily_rate / _HOURS_PER_DAY)


def calculate_overtime_pay(
    overtime_hours: Decimal,
    hourly_rate: Decimal,
    multiplier: Decimal = _OVERTIME_MULTIPLIER,
) -> Decimal:
    """Calculate overtime earnings from overtime hours and hourly rate."""
    if overtime_hours <= 0 or hourly_rate <= 0:
        return Decimal("0.00")
    return round_money(overtime_hours * hourly_rate * multiplier)


def calculate_unpaid_leave_deduction(
    unpaid_days: Decimal,
    daily_rate: Decimal,
) -> Decimal:
    """Calculate salary deduction for approved unpaid leave days."""
    if unpaid_days <= 0 or daily_rate <= 0:
        return Decimal("0.00")
    return round_money(unpaid_days * daily_rate)


def calculate_worked_hours(
    check_in: datetime,
    check_out: datetime,
) -> tuple[Decimal, Decimal]:
    """Calculate total hours and overtime hours from check-in/out timestamps.

    Returns:
        tuple[Decimal, Decimal]: (total_hours, overtime_hours)
    """
    if check_out < check_in:
        raise ValueError(f"check_out ({check_out}) cannot precede check_in ({check_in}).")

    duration_seconds = Decimal(str((check_out - check_in).total_seconds()))
    hours_raw = duration_seconds / Decimal("3600")
    total_hours = round_money(hours_raw)
    overtime_hours = max(Decimal("0.00"), total_hours - _HOURS_PER_DAY)
    return total_hours, overtime_hours


def calculate_payroll_entry(
    base_salary: Decimal,
    worked_days: int = 0,
    unpaid_leave_days: Decimal = Decimal("0.0"),
    overtime_hours: Decimal = Decimal("0.00"),
    allowances: Decimal = Decimal("0.00"),
    deductions: Decimal = Decimal("0.00"),
) -> dict[str, Decimal]:
    """Calculate itemized payroll figures for a single employee entry.

    Returns a dict containing all intermediate and final figures.
    """
    daily_rate = calculate_daily_rate(base_salary)
    hourly_rate = calculate_hourly_rate(daily_rate)
    overtime_pay = calculate_overtime_pay(overtime_hours, hourly_rate)
    unpaid_leave_deduction = calculate_unpaid_leave_deduction(unpaid_leave_days, daily_rate)

    gross_salary = round_money(base_salary + overtime_pay + allowances)
    total_deductions = round_money(unpaid_leave_deduction + deductions)
    net_salary = round_money(gross_salary - total_deductions)

    return {
        "daily_rate": daily_rate,
        "hourly_rate": hourly_rate,
        "overtime_pay": overtime_pay,
        "unpaid_leave_deduction": unpaid_leave_deduction,
        "gross_salary": gross_salary,
        "total_deductions": total_deductions,
        "net_salary": net_salary,
    }


__all__ = [
    "calculate_daily_rate",
    "calculate_hourly_rate",
    "calculate_overtime_pay",
    "calculate_payroll_entry",
    "calculate_unpaid_leave_deduction",
    "calculate_worked_hours",
    "round_money",
]
