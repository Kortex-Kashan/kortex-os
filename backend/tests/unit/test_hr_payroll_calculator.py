"""Unit tests for HR & Payroll mathematical calculation logic and policies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from kortex.modules.hr_payroll.calculator import (
    calculate_daily_rate,
    calculate_hourly_rate,
    calculate_overtime_pay,
    calculate_payroll_entry,
    calculate_unpaid_leave_deduction,
    calculate_worked_hours,
    round_money,
)


def test_round_money_bankers_rounding() -> None:
    assert round_money(Decimal("10.004")) == Decimal("10.00")
    assert round_money(Decimal("10.005")) == Decimal("10.01")
    assert round_money(Decimal("10.006")) == Decimal("10.01")


def test_daily_rate_30_day_divisor() -> None:
    # 3000 / 30 = 100.00
    assert calculate_daily_rate(Decimal("3000.00")) == Decimal("100.00")

    # 4550 / 30 = 151.6666... -> 151.67
    assert calculate_daily_rate(Decimal("4550.00")) == Decimal("151.67")

    # Zero or negative salary returns 0.00
    assert calculate_daily_rate(Decimal("0.00")) == Decimal("0.00")
    assert calculate_daily_rate(Decimal("-100.00")) == Decimal("0.00")


def test_hourly_rate_8_hour_day() -> None:
    # 100 / 8 = 12.50
    assert calculate_hourly_rate(Decimal("100.00")) == Decimal("12.50")

    # 151.67 / 8 = 18.95875 -> 18.96
    assert calculate_hourly_rate(Decimal("151.67")) == Decimal("18.96")

    assert calculate_hourly_rate(Decimal("0.00")) == Decimal("0.00")


def test_overtime_pay_1_5x_multiplier() -> None:
    hourly = Decimal("12.50")
    # 0 hours -> 0.00
    assert calculate_overtime_pay(Decimal("0.00"), hourly) == Decimal("0.00")

    # 4 hours * 12.50 * 1.5 = 75.00
    assert calculate_overtime_pay(Decimal("4.00"), hourly) == Decimal("75.00")

    # 2.5 hours * 12.50 * 1.5 = 46.875 -> 46.88
    assert calculate_overtime_pay(Decimal("2.50"), hourly) == Decimal("46.88")


def test_unpaid_leave_deduction() -> None:
    daily = Decimal("100.00")
    # 0 days -> 0.00
    assert calculate_unpaid_leave_deduction(Decimal("0.0"), daily) == Decimal("0.00")

    # 2.5 days * 100.00 = 250.00
    assert calculate_unpaid_leave_deduction(Decimal("2.5"), daily) == Decimal("250.00")


def test_worked_hours_calculation() -> None:
    start = datetime(2026, 9, 3, 9, 0, 0, tzinfo=UTC)

    # Standard 8 hours (9am to 5pm)
    end_standard = start + timedelta(hours=8)
    tot, ot = calculate_worked_hours(start, end_standard)
    assert tot == Decimal("8.00")
    assert ot == Decimal("0.00")

    # Overtime 10.5 hours (9am to 7:30pm)
    end_ot = start + timedelta(hours=10, minutes=30)
    tot_ot, ot_ot = calculate_worked_hours(start, end_ot)
    assert tot_ot == Decimal("10.50")
    assert ot_ot == Decimal("2.50")

    # Zero-hour check-in/out
    tot_zero, ot_zero = calculate_worked_hours(start, start)
    assert tot_zero == Decimal("0.00")
    assert ot_zero == Decimal("0.00")

    # Invalid check-out earlier than check-in
    with pytest.raises(ValueError):
        calculate_worked_hours(start, start - timedelta(minutes=10))


def test_full_payroll_entry_calculation() -> None:
    # Base: 3000.00, daily: 100.00, hourly: 12.50
    # Overtime: 4.00h -> 75.00
    # Unpaid leave: 2.0d -> 200.00
    # Allowances: 200.00, Deductions: 50.00
    result = calculate_payroll_entry(
        base_salary=Decimal("3000.00"),
        worked_days=20,
        unpaid_leave_days=Decimal("2.0"),
        overtime_hours=Decimal("4.00"),
        allowances=Decimal("200.00"),
        deductions=Decimal("50.00"),
    )

    assert result["daily_rate"] == Decimal("100.00")
    assert result["hourly_rate"] == Decimal("12.50")
    assert result["overtime_pay"] == Decimal("75.00")
    assert result["unpaid_leave_deduction"] == Decimal("200.00")
    assert result["gross_salary"] == Decimal("3275.00")  # 3000 + 75 + 200
    assert result["total_deductions"] == Decimal("250.00")  # 200 + 50
    assert result["net_salary"] == Decimal("3025.00")  # 3275 - 250
