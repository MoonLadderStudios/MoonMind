"""Unit tests for recurring cron helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.workflows.recurring_tasks.cron import (
    CronExpressionError,
    compute_next_occurrence,
    parse_cron_expression,
)

def test_parse_cron_expression_rejects_invalid_field_count() -> None:
    with pytest.raises(CronExpressionError, match="exactly 5 fields"):
        parse_cron_expression("0 0 * *")

def test_parse_cron_expression_accepts_lists_ranges_and_steps() -> None:
    spec = parse_cron_expression("*/15 9-17 * * 1,2,3")
    assert 0 in spec.minute.values
    assert 45 in spec.minute.values
    assert 9 in spec.hour.values
    assert 17 in spec.hour.values

def test_compute_next_occurrence_handles_dst_gap() -> None:
    next_run = compute_next_occurrence(
        cron="30 2 * * *",
        timezone_name="America/New_York",
        after=datetime(2026, 3, 8, 6, 0, tzinfo=UTC),
    )

    # During spring-forward gaps the scheduler should still return a valid UTC timestamp.
    assert next_run == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
