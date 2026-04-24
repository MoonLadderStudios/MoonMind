"""Cron parsing helpers for recurring task schedules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class CronExpressionError(ValueError):
    """Raised when a cron expression or timezone value is invalid."""

@dataclass(frozen=True, slots=True)
class _CronField:
    values: frozenset[int]
    wildcard: bool

@dataclass(frozen=True, slots=True)
class CronSpec:
    minute: _CronField
    hour: _CronField
    day: _CronField
    month: _CronField
    weekday: _CronField

def _parse_int(value: str, *, field_name: str) -> int:
    text = str(value or "").strip()
    if not text:
        raise CronExpressionError(f"{field_name} contains an empty value")
    if not text.isdigit():
        raise CronExpressionError(f"{field_name} contains invalid token '{text}'")
    return int(text)

def _parse_field(
    token: str,
    *,
    min_value: int,
    max_value: int,
    field_name: str,
    allow_sunday_7: bool = False,
) -> _CronField:
    normalized = str(token or "").strip()
    if not normalized:
        raise CronExpressionError(f"{field_name} is required")

    if normalized == "*":
        return _CronField(
            values=frozenset(range(min_value, max_value + 1)),
            wildcard=True,
        )

    values: set[int] = set()

    for raw_part in normalized.split(","):
        part = raw_part.strip()
        if not part:
            raise CronExpressionError(f"{field_name} contains an empty list item")

        if "/" in part:
            base_part, step_part = part.split("/", 1)
            step = _parse_int(step_part, field_name=field_name)
            if step < 1:
                raise CronExpressionError(f"{field_name} step must be >= 1")
        else:
            base_part = part
            step = 1

        base = base_part.strip() or "*"

        if base == "*":
            start = min_value
            end = max_value
        elif "-" in base:
            start_part, end_part = base.split("-", 1)
            start = _parse_int(start_part, field_name=field_name)
            end = _parse_int(end_part, field_name=field_name)
            if end < start:
                raise CronExpressionError(f"{field_name} range start must be <= end")
        else:
            start = _parse_int(base, field_name=field_name)
            end = start

        for candidate in range(start, end + 1, step):
            value = candidate
            if allow_sunday_7 and value == 7:
                value = 0
            if value < min_value or value > max_value:
                raise CronExpressionError(
                    f"{field_name} value {candidate} is out of range"
                )
            values.add(value)

    if not values:
        raise CronExpressionError(f"{field_name} does not include any values")

    return _CronField(values=frozenset(values), wildcard=False)

def parse_cron_expression(expression: str) -> CronSpec:
    """Parse a standard 5-field cron expression."""

    normalized = str(expression or "").strip()
    parts = [part for part in normalized.split() if part]
    if len(parts) != 5:
        raise CronExpressionError("cron must contain exactly 5 fields")

    minute = _parse_field(parts[0], min_value=0, max_value=59, field_name="minute")
    hour = _parse_field(parts[1], min_value=0, max_value=23, field_name="hour")
    day = _parse_field(parts[2], min_value=1, max_value=31, field_name="day")
    month = _parse_field(parts[3], min_value=1, max_value=12, field_name="month")
    weekday = _parse_field(
        parts[4],
        min_value=0,
        max_value=6,
        field_name="weekday",
        allow_sunday_7=True,
    )
    return CronSpec(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        weekday=weekday,
    )

def validate_timezone_name(timezone_name: str) -> str:
    """Validate and normalize an IANA timezone name."""

    normalized = str(timezone_name or "").strip()
    if not normalized:
        raise CronExpressionError("timezone is required")

    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise CronExpressionError(f"timezone '{normalized}' is invalid") from exc

    return normalized

def _matches_day(spec: CronSpec, candidate: datetime) -> bool:
    day_match = candidate.day in spec.day.values
    # Python Monday=0..Sunday=6; cron Sunday=0.
    weekday_value = (candidate.weekday() + 1) % 7
    weekday_match = weekday_value in spec.weekday.values

    if spec.day.wildcard and spec.weekday.wildcard:
        return True
    if spec.day.wildcard:
        return weekday_match
    if spec.weekday.wildcard:
        return day_match
    return day_match or weekday_match

def _matches(spec: CronSpec, candidate: datetime) -> bool:
    return (
        candidate.minute in spec.minute.values
        and candidate.hour in spec.hour.values
        and candidate.month in spec.month.values
        and _matches_day(spec, candidate)
    )

def compute_next_occurrence(
    *,
    cron: str,
    timezone_name: str,
    after: datetime,
    max_minutes_to_scan: int = 60 * 24 * 366 * 5,
) -> datetime:
    """Return the next scheduled fire time in UTC after ``after``."""

    if after.tzinfo is None:
        raise CronExpressionError("'after' must include timezone information")

    tz_name = validate_timezone_name(timezone_name)
    spec = parse_cron_expression(cron)
    tz = ZoneInfo(tz_name)

    cursor = after.astimezone(tz).replace(second=0, microsecond=0)
    cursor += timedelta(minutes=1)

    for _ in range(max(1, int(max_minutes_to_scan))):
        if _matches(spec, cursor):
            return cursor.astimezone(UTC)
        cursor += timedelta(minutes=1)

    raise CronExpressionError("unable to compute next occurrence for cron schedule")

__all__ = [
    "CronExpressionError",
    "CronSpec",
    "compute_next_occurrence",
    "parse_cron_expression",
    "validate_timezone_name",
]
