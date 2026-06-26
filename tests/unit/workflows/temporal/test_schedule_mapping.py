"""Unit tests for moonmind.workflows.temporal.schedule_mapping."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from temporalio.client import ScheduleOverlapPolicy

from moonmind.workflows.temporal.schedule_mapping import (
    build_schedule_policy,
    build_schedule_spec,
    build_schedule_state,
    make_schedule_id,
    make_workflow_id_template,
    map_catchup_window,
    map_overlap_policy,
)

# ---------------------------------------------------------------------------
# map_overlap_policy
# ---------------------------------------------------------------------------

class TestMapOverlapPolicy:
    """DOC-REQ-003: overlap policy mapping."""

    def test_skip(self) -> None:
        assert map_overlap_policy("skip") == ScheduleOverlapPolicy.SKIP

    def test_allow(self) -> None:
        assert map_overlap_policy("allow") == ScheduleOverlapPolicy.ALLOW_ALL

    def test_buffer_one(self) -> None:
        assert map_overlap_policy("buffer_one") == ScheduleOverlapPolicy.BUFFER_ONE

    def test_cancel_previous(self) -> None:
        assert map_overlap_policy("cancel_previous") == ScheduleOverlapPolicy.CANCEL_OTHER

    def test_normalises_whitespace_and_case(self) -> None:
        assert map_overlap_policy("  Skip  ") == ScheduleOverlapPolicy.SKIP

    def test_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown overlap mode"):
            map_overlap_policy("bogus")

# ---------------------------------------------------------------------------
# map_catchup_window
# ---------------------------------------------------------------------------

class TestMapCatchupWindow:
    """DOC-REQ-004: catchup policy mapping."""

    def test_none(self) -> None:
        assert map_catchup_window("none") == timedelta(0)

    def test_last(self) -> None:
        assert map_catchup_window("last") == timedelta(minutes=15)

    def test_all(self) -> None:
        assert map_catchup_window("all") == timedelta(days=365)

    def test_normalises_whitespace_and_case(self) -> None:
        assert map_catchup_window("  Last  ") == timedelta(minutes=15)

    def test_unknown_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown catchup mode"):
            map_catchup_window("bogus")

# ---------------------------------------------------------------------------
# build_schedule_spec
# ---------------------------------------------------------------------------

class TestBuildScheduleSpec:
    """DOC-REQ-006: jitter mapping + spec construction."""

    def test_basic(self) -> None:
        spec = build_schedule_spec("0 * * * *", timezone="America/New_York")
        assert spec.cron_expressions == ["0 * * * *"]
        assert spec.time_zone_name == "America/New_York"
        assert spec.jitter == timedelta(0)

    def test_jitter(self) -> None:
        spec = build_schedule_spec("0 0 * * *", jitter_seconds=30)
        assert spec.jitter == timedelta(seconds=30)

    def test_negative_jitter_clamped(self) -> None:
        spec = build_schedule_spec("0 0 * * *", jitter_seconds=-5)
        assert spec.jitter == timedelta(0)

    def test_timezone_preservation(self) -> None:
        """DOC-REQ-006: timezone preservation for schedule spec."""
        # US/Eastern
        spec_eastern = build_schedule_spec("30 2 * * *", timezone="US/Eastern")
        assert spec_eastern.time_zone_name == "US/Eastern"

        # Europe/London
        spec_london = build_schedule_spec("30 2 * * *", timezone="Europe/London")
        assert spec_london.time_zone_name == "Europe/London"

        # UTC
        spec_utc = build_schedule_spec("30 2 * * *", timezone="UTC")
        assert spec_utc.time_zone_name == "UTC"

# ---------------------------------------------------------------------------
# build_schedule_policy
# ---------------------------------------------------------------------------

class TestBuildSchedulePolicy:
    def test_defaults(self) -> None:
        policy = build_schedule_policy()
        assert policy.overlap == ScheduleOverlapPolicy.SKIP
        assert policy.catchup_window == timedelta(minutes=15)

    def test_custom(self) -> None:
        policy = build_schedule_policy(overlap_mode="allow", catchup_mode="all")
        assert policy.overlap == ScheduleOverlapPolicy.ALLOW_ALL
        assert policy.catchup_window == timedelta(days=365)

# ---------------------------------------------------------------------------
# build_schedule_state
# ---------------------------------------------------------------------------

class TestBuildScheduleState:
    def test_enabled(self) -> None:
        state = build_schedule_state(enabled=True, note="test")
        assert state.paused is False
        assert state.note == "test"

    def test_disabled(self) -> None:
        state = build_schedule_state(enabled=False)
        assert state.paused is True

# ---------------------------------------------------------------------------
# ID conventions
# ---------------------------------------------------------------------------

_TEST_UUID = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

class TestIdConventions:
    """DOC-REQ-005: schedule and workflow ID conventions."""

    def test_make_schedule_id(self) -> None:
        assert make_schedule_id(_TEST_UUID) == "mm-schedule:a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_make_workflow_id_template(self) -> None:
        template = make_workflow_id_template(_TEST_UUID)
        assert template.startswith("mm:a1b2c3d4-e5f6-7890-abcd-ef1234567890:")
        assert "{{.ScheduleTime}}" in template

class TestDSTBoundarySemantics:
    """DOC-REQ-006: DST Boundary tests for schedule spec semantics (5.1: US/Eastern, Europe/London, UTC)."""

    def test_spring_forward_eastern(self) -> None:
        """Test spring forward (e.g. 2:00 AM -> 3:00 AM in US/Eastern on Mar 10, 2030)."""
        # A schedule that runs at 2:30 AM every day
        spec = build_schedule_spec("30 2 * * *", timezone="US/Eastern")

        # When DST starts, 2:30 AM does not exist (clocks jump from 1:59 AM to 3:00 AM)
        # We verify the Temporal ScheduleSpec faithfully represents this configuration
        # (Temporal server handles the actual skip/forward logic)
        assert spec.cron_expressions == ["30 2 * * *"]
        assert spec.time_zone_name == "US/Eastern"

    def test_fall_back_eastern(self) -> None:
        """Test fall back (e.g. 2:00 AM -> 1:00 AM in US/Eastern on Nov 3, 2030)."""
        # A schedule that runs at 1:30 AM every day
        spec = build_schedule_spec("30 1 * * *", timezone="US/Eastern")

        # When DST ends, 1:30 AM happens twice.
        assert spec.cron_expressions == ["30 1 * * *"]
        assert spec.time_zone_name == "US/Eastern"

    def test_spring_forward_london(self) -> None:
        """Test spring forward in Europe/London (last Sunday in March: 1:00 AM -> 2:00 AM BST)."""
        # A schedule that runs at 1:30 AM every day
        spec = build_schedule_spec("30 1 * * *", timezone="Europe/London")

        # When BST begins, 1:30 AM does not exist (clocks jump from 0:59 AM to 2:00 AM)
        # Temporal server handles the actual skip/forward logic
        assert spec.cron_expressions == ["30 1 * * *"]
        assert spec.time_zone_name == "Europe/London"

    def test_fall_back_london(self) -> None:
        """Test fall back in Europe/London (last Sunday in October: 2:00 AM -> 1:00 AM GMT)."""
        # A schedule that runs at 1:30 AM every day
        spec = build_schedule_spec("30 1 * * *", timezone="Europe/London")

        # When BST ends, 1:30 AM happens twice (once in BST, once in GMT)
        assert spec.cron_expressions == ["30 1 * * *"]
        assert spec.time_zone_name == "Europe/London"

    def test_utc_no_dst(self) -> None:
        """Test that UTC schedules are not affected by DST (UTC has no DST transitions)."""
        # Schedules at various DST-sensitive times should be stable year-round for UTC
        spec_early = build_schedule_spec("30 2 * * *", timezone="UTC")
        spec_late = build_schedule_spec("30 1 * * *", timezone="UTC")

        assert spec_early.time_zone_name == "UTC"
        assert spec_late.time_zone_name == "UTC"
        # UTC offsets are constant; no skip or duplication expected
        assert spec_early.cron_expressions == ["30 2 * * *"]
        assert spec_late.cron_expressions == ["30 1 * * *"]
