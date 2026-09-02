"""
Unit tests for deterministic 5-field Cron parser and next-run calculator (Milestone M5.4).
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest

from kortex.engines.workflow.cron import (
    CronScheduleSpec,
    compute_next_cron_run,
    validate_cron_expression,
)
from kortex.engines.workflow.exceptions import WorkflowValidationError


def test_cron_spec_parse_wildcard() -> None:
    spec = CronScheduleSpec.parse("* * * * *")
    assert len(spec.minutes) == 60
    assert len(spec.hours) == 24
    assert len(spec.days_of_month) == 31
    assert len(spec.months) == 12
    assert len(spec.days_of_week) == 7


def test_cron_spec_parse_specific_values() -> None:
    spec = CronScheduleSpec.parse("15 10 1 6 3")
    assert spec.minutes == {15}
    assert spec.hours == {10}
    assert spec.days_of_month == {1}
    assert spec.months == {6}
    assert spec.days_of_week == {3}


def test_cron_spec_parse_ranges_and_steps() -> None:
    spec = CronScheduleSpec.parse("*/15 9-17 * * 1-5")
    assert spec.minutes == {0, 15, 30, 45}
    assert spec.hours == {9, 10, 11, 12, 13, 14, 15, 16, 17}
    assert spec.days_of_week == {1, 2, 3, 4, 5}


def test_cron_spec_parse_comma_lists() -> None:
    spec = CronScheduleSpec.parse("0,30 8,12,18 * * *")
    assert spec.minutes == {0, 30}
    assert spec.hours == {8, 12, 18}


def test_cron_validation_invalid_field_count() -> None:
    with pytest.raises(WorkflowValidationError, match="must contain exactly 5 fields"):
        validate_cron_expression("* * * *")

    with pytest.raises(WorkflowValidationError, match="must contain exactly 5 fields"):
        validate_cron_expression("* * * * * *")


def test_cron_validation_out_of_range() -> None:
    with pytest.raises(WorkflowValidationError, match="out of bounds"):
        validate_cron_expression("60 * * * *")

    with pytest.raises(WorkflowValidationError, match="out of bounds"):
        validate_cron_expression("* 24 * * *")

    with pytest.raises(WorkflowValidationError, match="out of bounds"):
        validate_cron_expression("* * 32 * *")

    with pytest.raises(WorkflowValidationError, match="out of bounds"):
        validate_cron_expression("* * * 13 *")

    with pytest.raises(WorkflowValidationError, match="out of bounds"):
        validate_cron_expression("* * * * 8")


def test_compute_next_cron_run_every_minute() -> None:
    base = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    next_run = compute_next_cron_run("* * * * *", after_dt=base)
    assert next_run == datetime.datetime(2026, 8, 29, 12, 1, 0, tzinfo=UTC)


def test_compute_next_cron_run_hourly_top_of_hour() -> None:
    base = datetime.datetime(2026, 8, 29, 12, 15, 0, tzinfo=UTC)
    next_run = compute_next_cron_run("0 * * * *", after_dt=base)
    assert next_run == datetime.datetime(2026, 8, 29, 13, 0, 0, tzinfo=UTC)


def test_compute_next_cron_run_daily_midnight() -> None:
    base = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    next_run = compute_next_cron_run("0 0 * * *", after_dt=base)
    assert next_run == datetime.datetime(2026, 8, 30, 0, 0, 0, tzinfo=UTC)


def test_compute_next_cron_run_day_of_week() -> None:
    # 2026-08-29 is a Saturday (weekday 5)
    base = datetime.datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)
    # Next Monday (weekday 0 in Python, 1 in cron) at 09:00 -> 2026-08-31
    next_run = compute_next_cron_run("0 9 * * 1", after_dt=base)
    assert next_run == datetime.datetime(2026, 8, 31, 9, 0, 0, tzinfo=UTC)
    assert next_run.weekday() == 0


def test_compute_next_cron_run_year_rollover() -> None:
    base = datetime.datetime(2026, 12, 31, 23, 50, 0, tzinfo=UTC)
    next_run = compute_next_cron_run("0 0 1 1 *", after_dt=base)
    assert next_run == datetime.datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
