"""AgentRun's progress-aware execution-budget decisions.

Regression cover for MoonLadderStudios/MoonMind#3771. The AgentRun workflow
terminated a managed run at its flat one-hour budget while the runtime was
actively working — it had written files three seconds before the kill — and
reported the outcome as "no observable progress". An hour of uncommitted work
was discarded.

These exercise the decision methods the poll loop actually calls, in both patch
states, so the in-flight (pre-patch) path is covered alongside the new one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    ExecutionBudget,
    resolve_execution_budget,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

_BUDGET = ExecutionBudget(
    base_seconds=3600,
    max_seconds=21600,
    progress_stall_seconds=900,
)

_START = datetime(2026, 8, 25, 7, 24, tzinfo=UTC)


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="claude_code",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


# ---------------------------------------------------------------------------
# Progress observation
# ---------------------------------------------------------------------------


def test_idle_seconds_is_none_before_any_progress_is_observed():
    assert (
        MoonMindAgentRun._budget_idle_progress_seconds(
            progress_aware=True,
            last_progress_at=None,
            now=_START + timedelta(hours=1),
        )
        is None
    )


def test_idle_seconds_measures_from_the_last_observation():
    assert (
        MoonMindAgentRun._budget_idle_progress_seconds(
            progress_aware=True,
            last_progress_at=_START + timedelta(minutes=59),
            now=_START + timedelta(hours=1),
        )
        == 60.0
    )


def test_idle_seconds_is_none_when_the_patch_is_off():
    """An in-flight run records no progress evidence, so it cannot be extended."""

    assert (
        MoonMindAgentRun._budget_idle_progress_seconds(
            progress_aware=False,
            last_progress_at=_START + timedelta(minutes=59),
            now=_START + timedelta(hours=1),
        )
        is None
    )


# ---------------------------------------------------------------------------
# The #3771 journey
# ---------------------------------------------------------------------------


def test_working_run_is_not_terminated_at_the_base_budget():
    """The exact failure: base budget reached, progress three seconds ago."""

    verdict = MoonMindAgentRun._budget_verdict_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=3600.0,
        idle_progress_seconds=3.0,
    )

    assert verdict == "continue"


def test_working_run_keeps_a_positive_poll_budget_past_the_base_window():
    """The deadline must roll forward too, or the poll loop breaks out early
    with a non-positive remaining budget even though the run may continue."""

    deadline = MoonMindAgentRun._budget_deadline_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=3600.0,
        idle_progress_seconds=3.0,
    )

    assert deadline - 3600.0 > 0


def test_working_run_finally_stops_at_the_ceiling_and_says_so():
    verdict = MoonMindAgentRun._budget_verdict_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=float(_BUDGET.max_seconds),
        idle_progress_seconds=1.0,
    )
    detail = MoonMindAgentRun._budget_expiry_detail_for(
        budget=_BUDGET,
        progress_aware=True,
        verdict=verdict,
    )

    assert verdict == "expired_max_budget"
    # It must not accuse a run that was demonstrably working of doing nothing.
    assert "no observable progress" not in detail
    assert "maximum execution budget" in detail


def test_quiet_run_is_still_terminated_at_the_base_budget():
    verdict = MoonMindAgentRun._budget_verdict_for(
        budget=_BUDGET,
        progress_aware=True,
        elapsed_seconds=3600.0,
        idle_progress_seconds=float(_BUDGET.progress_stall_seconds),
    )
    detail = MoonMindAgentRun._budget_expiry_detail_for(
        budget=_BUDGET,
        progress_aware=True,
        verdict=verdict,
    )

    assert verdict == "expired_no_progress"
    assert "no observable progress" in detail


def test_run_with_no_evidence_at_all_is_terminated_at_the_base_budget():
    assert (
        MoonMindAgentRun._budget_verdict_for(
            budget=_BUDGET,
            progress_aware=True,
            elapsed_seconds=3600.0,
            idle_progress_seconds=None,
        )
        == "expired_no_progress"
    )


# ---------------------------------------------------------------------------
# In-flight safety: a run replaying from before the patch is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("elapsed", "idle", "expected"),
    [
        (3599.0, 0.0, "continue"),
        (3600.0, 0.0, "expired_no_progress"),
        (3600.0, None, "expired_no_progress"),
        (10000.0, 0.0, "expired_no_progress"),
    ],
)
def test_unpatched_runs_keep_the_flat_deadline(elapsed, idle, expected):
    """Fresh progress must not extend a run that recorded the flat deadline."""

    assert (
        MoonMindAgentRun._budget_verdict_for(
            budget=_BUDGET,
            progress_aware=False,
            elapsed_seconds=elapsed,
            idle_progress_seconds=idle,
        )
        == expected
    )
    assert (
        MoonMindAgentRun._budget_deadline_for(
            budget=_BUDGET,
            progress_aware=False,
            elapsed_seconds=elapsed,
            idle_progress_seconds=idle,
        )
        == float(_BUDGET.base_seconds)
    )


def test_unpatched_expiry_detail_matches_the_previous_wording():
    """In-flight runs keep the message their operators are already reading."""

    assert (
        MoonMindAgentRun._budget_expiry_detail_for(
            budget=_BUDGET,
            progress_aware=False,
            verdict="expired_no_progress",
        )
        == "made no observable progress and exceeded its execution budget"
    )


# ---------------------------------------------------------------------------
# Terminal result carries the evidence the decision was made on
# ---------------------------------------------------------------------------


def test_ceiling_timeout_result_records_the_budget_and_verdict():
    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=float(_BUDGET.max_seconds),
        elapsed_seconds=float(_BUDGET.max_seconds),
        detail=MoonMindAgentRun._budget_expiry_detail_for(
            budget=_BUDGET, progress_aware=True, verdict="expired_max_budget"
        ),
        budget=_BUDGET,
        verdict="expired_max_budget",
    )

    assert result.failure_class == "execution_error"
    assert result.metadata["budgetVerdict"] == "expired_max_budget"
    assert result.metadata["budgetExtendedForProgress"] is True
    assert result.metadata["executionBudget"] == {
        "baseSeconds": 3600,
        "maxSeconds": 21600,
        "progressStallSeconds": 900,
    }
    assert "no observable progress" not in result.summary


def test_stalled_timeout_result_is_not_labelled_as_extended():
    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=float(_BUDGET.base_seconds),
        elapsed_seconds=float(_BUDGET.base_seconds),
        detail=MoonMindAgentRun._budget_expiry_detail_for(
            budget=_BUDGET, progress_aware=True, verdict="expired_no_progress"
        ),
        budget=_BUDGET,
        verdict="expired_no_progress",
    )

    assert result.metadata["budgetVerdict"] == "expired_no_progress"
    assert result.metadata["budgetExtendedForProgress"] is False
    assert "no observable progress" in result.summary


def test_unpatched_timeout_result_omits_the_budget_metadata():
    """In-flight runs keep the metadata shape their history already carries."""

    workflow = MoonMindAgentRun()
    result = workflow._timed_out_result(
        request=_request(),
        timeout_seconds=3600,
        elapsed_seconds=3600,
        detail="made no observable progress and exceeded its execution budget",
    )

    assert "executionBudget" not in result.metadata
    assert "budgetVerdict" not in result.metadata
    assert "budgetExtendedForProgress" not in result.metadata


# ---------------------------------------------------------------------------
# The supervisor must receive the same budget the workflow enforces
# ---------------------------------------------------------------------------


def test_published_policy_carries_the_whole_budget():
    request = _request()

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=_BUDGET, progress_aware=True
    )

    # What the launcher serializes is what the supervisor resolves.
    launch_payload = request.model_dump(mode="json", by_alias=True)
    published = launch_payload["timeoutPolicy"]
    assert published["timeout_seconds"] == _BUDGET.base_seconds
    assert published["max_timeout_seconds"] == _BUDGET.max_seconds
    assert published["progress_stall_seconds"] == _BUDGET.progress_stall_seconds
    assert (
        resolve_execution_budget(agent_kind="managed", timeout_policy=published)
        == _BUDGET
    )


def test_published_policy_preserves_unrelated_timeout_policy_keys():
    request = _request()
    request.timeout_policy = {"custom_key": "kept"}

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=_BUDGET, progress_aware=True
    )

    assert request.timeout_policy["custom_key"] == "kept"


def test_unpatched_publish_keeps_the_prior_launch_payload_shape():
    """Replay safety: an in-flight run must not gain fields in a payload it has
    already dispatched."""

    request = _request()

    MoonMindAgentRun._publish_execution_budget(
        request=request, budget=_BUDGET, progress_aware=False
    )

    assert request.timeout_policy == {"timeout_seconds": _BUDGET.base_seconds}
