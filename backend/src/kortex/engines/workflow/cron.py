"""
KORTEX Pure-Python Deterministic Cron Parser & Next-Run Calculator.

Provides standard 5-field cron parsing (minute, hour, day-of-month, month, day-of-week)
with zero external dependencies. Calculates deterministic next execution timestamps in UTC.
"""

from __future__ import annotations

import datetime
from datetime import UTC, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kortex.engines.workflow.exceptions import WorkflowValidationError


def _parse_field(field_str: str, min_val: int, max_val: int, field_name: str) -> set[int]:
    """Parse a single cron field expression into a set of matching integer values."""
    field_str = field_str.strip()
    if not field_str:
        raise WorkflowValidationError(f"Empty cron field for '{field_name}'.")

    results: set[int] = set()

    for part in field_str.split(","):
        part = part.strip()
        if not part:
            raise WorkflowValidationError(f"Empty sub-expression in cron field '{field_name}'.")

        step = 1
        if "/" in part:
            subparts = part.split("/")
            if len(subparts) != 2:
                raise WorkflowValidationError(f"Invalid step syntax in cron field '{field_name}': '{part}'")
            part, step_str = subparts[0], subparts[1]
            try:
                step = int(step_str)
                if step <= 0:
                    raise ValueError
            except ValueError as err:
                raise WorkflowValidationError(
                    f"Invalid step value in cron field '{field_name}': '{step_str}'. Must be a positive integer."
                ) from err

        if part == "*":
            start, end = min_val, max_val
        elif "-" in part:
            range_parts = part.split("-")
            if len(range_parts) != 2:
                raise WorkflowValidationError(f"Invalid range syntax in cron field '{field_name}': '{part}'")
            try:
                start = int(range_parts[0])
                end = int(range_parts[1])
            except ValueError as err:
                raise WorkflowValidationError(
                    f"Invalid range values in cron field '{field_name}': '{part}'. Must be integers."
                ) from err
            if start > end or start < min_val or end > max_val:
                raise WorkflowValidationError(
                    f"Range '{part}' out of bounds for '{field_name}' [{min_val}-{max_val}]."
                )
        else:
            try:
                val = int(part)
            except ValueError as err:
                raise WorkflowValidationError(
                    f"Invalid value in cron field '{field_name}': '{part}'. Must be an integer."
                ) from err
            if val < min_val or val > max_val:
                # Support 7 as Sunday in DOW
                if field_name == "day_of_week" and val == 7:
                    val = 0
                else:
                    raise WorkflowValidationError(
                        f"Value '{val}' out of bounds for '{field_name}' [{min_val}-{max_val}]."
                    )
            start, end = val, val

        for val in range(start, end + 1, step):
            if field_name == "day_of_week" and val == 7:
                results.add(0)
            else:
                results.add(val)

    return results


class CronScheduleSpec:
    """Parsed cron schedule representation."""

    def __init__(self, cron_expr: str) -> None:
        self.raw_expression = cron_expr.strip()
        parts = self.raw_expression.split()
        if len(parts) != 5:
            raise WorkflowValidationError(
                f"Cron expression must contain exactly 5 fields, got {len(parts)}: '{cron_expr}'"
            )

        self.minutes = _parse_field(parts[0], 0, 59, "minute")
        self.hours = _parse_field(parts[1], 0, 23, "hour")
        self.days_of_month = _parse_field(parts[2], 1, 31, "day_of_month")
        self.months = _parse_field(parts[3], 1, 12, "month")
        self.days_of_week = _parse_field(parts[4], 0, 6, "day_of_week")

        self.dom_restricted = parts[2] != "*"
        self.dow_restricted = parts[4] != "*"

    @classmethod
    def parse(cls, cron_expr: str) -> CronScheduleSpec:
        """Parse a cron expression string into a CronScheduleSpec instance."""
        return cls(cron_expr)


def validate_cron_expression(cron_expr: str) -> None:
    """Validate a 5-field cron expression syntax.

    Raises:
        WorkflowValidationError: If syntax is invalid or values are out of range.
    """
    CronScheduleSpec(cron_expr)



def compute_next_cron_run(
    cron_expr: str,
    after_dt: datetime.datetime | None = None,
    max_search_days: int = 366,
    timezone: str = "UTC",
) -> datetime.datetime:
    """Compute the next UTC datetime matching the cron expression strictly after `after_dt`.

    Args:
        cron_expr: 5-field cron expression string.
        after_dt: Base datetime (UTC or any timezone-aware value; treated as UTC if naive).
            Defaults to current UTC time.
        max_search_days: Maximum days to search forward before giving up.
        timezone: IANA timezone name (e.g. "America/New_York") the cron fields are
            interpreted against (M5-A5). A cron expression's minute/hour/day-of-month/
            day-of-week fields are inherently a *local wall-clock* specification —
            "0 9 * * *" means "9am in this schedule's own timezone", not "9am UTC".
            Field matching below is performed entirely in `timezone`'s local time; the
            result is converted back to UTC (the format every caller and persisted
            `next_run_at` column already expects) only at the very end. Defaults to
            "UTC", which — since UTC has no DST transitions — reproduces the exact
            prior UTC-only behavior for every existing caller that doesn't pass this.

    Returns:
        The next matching datetime, in UTC.

    Raises:
        WorkflowValidationError: If expression or timezone is invalid, or no matching
            time is found within horizon.
    """
    spec = CronScheduleSpec(cron_expr)
    base_utc = after_dt or datetime.datetime.now(UTC)
    if base_utc.tzinfo is None:
        base_utc = base_utc.replace(tzinfo=UTC)

    try:
        tz = UTC if timezone in ("UTC", "utc") else ZoneInfo(timezone)
    except ZoneInfoNotFoundError as err:
        raise WorkflowValidationError(f"Unknown IANA timezone name: '{timezone}'.") from err

    base = base_utc.astimezone(tz)

    # Start searching from the next full minute (seconds and microseconds
    # truncated), in the schedule's own local time — cron fields are matched
    # against local wall-clock components (month/day/weekday/hour/minute)
    # throughout this loop, so DST transitions are handled the same way any
    # timezone-aware `datetime` arithmetic handles them (an hour is skipped
    # or repeated in local time exactly as the zone defines).
    current = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end_limit = base + timedelta(days=max_search_days)

    while current < end_limit:
        # Check month
        if current.month not in spec.months:
            # Advance to start of next month
            if current.month == 12:
                current = datetime.datetime(current.year + 1, 1, 1, 0, 0, tzinfo=tz)
            else:
                current = datetime.datetime(current.year, current.month + 1, 1, 0, 0, tzinfo=tz)
            continue

        # Check day of month & day of week
        # Python weekday: Monday is 0, Sunday is 6. Standard cron dow: Sunday is 0, Saturday is 6.
        cron_dow = (current.weekday() + 1) % 7
        dom_matches = current.day in spec.days_of_month
        dow_matches = cron_dow in spec.days_of_week

        # Standard cron semantics: if both DOM and DOW are specified, match if EITHER matches.
        # If only one is specified, match that one.
        if spec.dom_restricted and spec.dow_restricted:
            day_matches = dom_matches or dow_matches
        elif spec.dom_restricted:
            day_matches = dom_matches
        elif spec.dow_restricted:
            day_matches = dow_matches
        else:
            day_matches = True

        if not day_matches:
            # Advance to next day at 00:00
            current = (current + timedelta(days=1)).replace(hour=0, minute=0)
            continue

        # Check hour
        if current.hour not in spec.hours:
            current = (current + timedelta(hours=1)).replace(minute=0)
            continue

        # Check minute
        if current.minute not in spec.minutes:
            current = current + timedelta(minutes=1)
            continue

        # All matched! Convert the local wall-clock match back to UTC.
        return current.astimezone(UTC)

    raise WorkflowValidationError(
        f"No matching execution time found for cron expression '{cron_expr}' within {max_search_days} days."
    )
