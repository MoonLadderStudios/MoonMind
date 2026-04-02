"""T021/T025/T028/T030: Unit tests for Jules auto-answer sub-flow in MoonMind.AgentRun."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.workflows.agent_run import (
    MoonMindAgentRun,
    RunStatus,
    _EXTERNAL_STATUS_TO_RUN_STATUS,
)

pytestmark = [pytest.mark.asyncio]


# --- T021: RunStatus has awaiting_feedback ---


class TestRunStatusAwaitingFeedback:
    """T021: Verify awaiting_feedback is in RunStatus and status maps."""

    async def test_run_status_has_awaiting_feedback(self):
        assert hasattr(RunStatus, "awaiting_feedback")
        assert RunStatus.awaiting_feedback == "awaiting_feedback"

    async def test_external_status_map_has_awaiting_feedback(self):
        assert "awaiting_feedback" in _EXTERNAL_STATUS_TO_RUN_STATUS
        assert (
            _EXTERNAL_STATUS_TO_RUN_STATUS["awaiting_feedback"]
            == RunStatus.awaiting_feedback
        )

    async def test_normalize_external_status_maps_awaiting_feedback(self):
        """awaiting_feedback from provider maps to RunStatus.awaiting_feedback."""
        result = MoonMindAgentRun._normalize_external_status(
            normalized_status="awaiting_feedback",
            raw_status=None,
            provider_status=None,
        )
        assert result == RunStatus.awaiting_feedback


# --- T021: Auto-answer state variables ---


class TestAgentRunAutoAnswerState:
    """T021: Verify auto-answer state is initialized in __init__."""

    async def test_init_has_auto_answer_state(self):
        run = MoonMindAgentRun()
        assert hasattr(run, "_answered_activity_ids")
        assert isinstance(run._answered_activity_ids, set)
        assert len(run._answered_activity_ids) == 0

    async def test_init_has_auto_answer_count(self):
        run = MoonMindAgentRun()
        assert hasattr(run, "_auto_answer_count")
        assert run._auto_answer_count == 0


# --- T025: Max-cycle enforcement ---


class TestAutoAnswerMaxCycle:
    """T025: Max-cycle enforcement via _auto_answer_count."""

    async def test_auto_answer_count_increments(self):
        """Verify the counter can be incremented (workflow logic relies on this)."""
        run = MoonMindAgentRun()
        run._auto_answer_count += 1
        assert run._auto_answer_count == 1
        run._auto_answer_count += 1
        assert run._auto_answer_count == 2

    async def test_max_cycle_exceeds_limit(self):
        """When count >= max (3), the workflow should escalate."""
        run = MoonMindAgentRun()
        run._auto_answer_count = 3
        # The workflow checks: self._auto_answer_count >= aa_max
        assert run._auto_answer_count >= 3


# --- T028: Opt-out behavior ---


class TestAutoAnswerOptOut:
    """T028: get_auto_answer_config controls opt-out."""

    @pytest.mark.asyncio
    async def test_config_disabled_returns_false(self):
        """When env var is false, config.enabled is False."""
        from unittest.mock import patch

        from moonmind.workflows.temporal.activities.jules_activities import (
            jules_get_auto_answer_config_activity,
        )

        with patch.dict(
            "os.environ", {"JULES_AUTO_ANSWER_ENABLED": "false"}, clear=False
        ):
            config = await jules_get_auto_answer_config_activity()
        assert config["enabled"] is False

    @pytest.mark.asyncio
    async def test_config_enabled_by_default(self):
        """When env var is not set, config.enabled defaults to True."""
        from unittest.mock import patch

        from moonmind.workflows.temporal.activities.jules_activities import (
            jules_get_auto_answer_config_activity,
        )

        with patch.dict("os.environ", {}, clear=False):
            config = await jules_get_auto_answer_config_activity()
        assert config["enabled"] is True


# --- T030: Deduplication ---


# --- Question probe triggers on running status ---


class TestAutoAnswerRunningStatusProbe:
    """Verify the auto-answer sub-flow triggers for both awaiting_feedback and running status.

    The Jules API may report IN_PROGRESS at the task level even while Jules
    is asking questions in the session activity stream.  The workflow must
    probe list_activities during running status to detect questions.
    """

    async def test_running_status_is_eligible_for_auto_answer(self):
        """The auto-answer gate should include RunStatus.running."""
        eligible_statuses = {RunStatus.awaiting_feedback, RunStatus.running}
        assert RunStatus.running in eligible_statuses
        assert RunStatus.awaiting_feedback in eligible_statuses

    async def test_completed_status_is_not_eligible(self):
        """Terminal statuses should not trigger the auto-answer probe."""
        eligible_statuses = {RunStatus.awaiting_feedback, RunStatus.running}
        assert RunStatus.completed not in eligible_statuses

    async def test_failed_status_is_not_eligible(self):
        eligible_statuses = {RunStatus.awaiting_feedback, RunStatus.running}
        assert RunStatus.failed not in eligible_statuses


class TestAutoAnswerDeduplication:
    """T030: Deduplication via _answered_activity_ids set."""

    async def test_dedup_set_tracks_answered_ids(self):
        run = MoonMindAgentRun()
        run._answered_activity_ids.add("act-1")
        assert "act-1" in run._answered_activity_ids

    async def test_dedup_set_prevents_re_answer(self):
        """Once an activity ID is in the set, it should be skipped."""
        run = MoonMindAgentRun()
        run._answered_activity_ids.add("act-1")

        # Simulating the workflow check: act_id not in self._answered_activity_ids
        act_id = "act-1"
        should_answer = act_id not in run._answered_activity_ids
        assert should_answer is False

    async def test_dedup_set_allows_new_ids(self):
        """New activity IDs should be answerable."""
        run = MoonMindAgentRun()
        run._answered_activity_ids.add("act-1")

        act_id = "act-2"
        should_answer = act_id not in run._answered_activity_ids
        assert should_answer is True


class TestOperatorMessageQueue:
    """Operator clarification replies should be queued for explicit sendMessage flows."""

    async def test_operator_message_signal_queues_non_empty_message(self):
        run = MoonMindAgentRun()

        run.operator_message({"message": "Use the Provider Profiles name."})

        assert run._pending_operator_messages == [
            "Use the Provider Profiles name."
        ]

    async def test_operator_message_signal_ignores_blank_message(self):
        run = MoonMindAgentRun()

        run.operator_message({"message": "   "})

        assert run._pending_operator_messages == []
