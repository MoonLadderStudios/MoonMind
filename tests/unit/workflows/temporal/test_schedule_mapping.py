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
