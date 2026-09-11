"""MoonLadderStudios/MoonMind#4226: profile-bound retry inherits remaining budget."""

from datetime import datetime, timezone
from typing import Any

import pytest

import moonmind.workflows.temporal.workflows.agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    OMNIGENT_PROFILE_BOUND_REMAINING_BUDGET_PATCH_ID,
    MoonMindAgentRun,
    profile_bound_retry_start_to_close_seconds,
)


def test_retry_inherits_remaining_budget_not_fresh_window() -> None:
    # A 6h first attempt that failed after 10 minutes leaves ~5h50m.
    retry_stc = profile_bound_retry_start_to_close_seconds(
        first_stc_seconds=21600,
        elapsed_seconds=600.0,
    )
    assert retry_stc == 21000
    assert retry_stc < 21600


def test_attempt_two_cannot_exceed_parent_remaining_schedule_to_close() -> None:
    first_stc = 21600
    for elapsed in (0.0, 600.0, 18000.0, 21599.0, 99999.0):
        retry_stc = profile_bound_retry_start_to_close_seconds(
            first_stc_seconds=first_stc,
            elapsed_seconds=elapsed,
        )
        # Attempt 2 is bounded by what the parent has left (clamped to the
        # minimum probe when the budget is exhausted), never a fresh window
        # on top of elapsed time.
        assert retry_stc <= first_stc
        assert elapsed + retry_stc <= first_stc + 60 or retry_stc == 60
        assert retry_stc >= 60


def test_exhausted_budget_collapses_to_minimum_probe() -> None:
    assert (
        profile_bound_retry_start_to_close_seconds(
            first_stc_seconds=3600,
            elapsed_seconds=7200.0,
        )
        == 60
    )


def test_remaining_budget_patch_id_is_stable() -> None:
    assert (
        OMNIGENT_PROFILE_BOUND_REMAINING_BUDGET_PATCH_ID
        == "agent-run-omnigent-profile-bound-remaining-budget-v1"
    )


@pytest.mark.asyncio
async def test_attempt_two_receives_remaining_budget_through_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activity-boundary test: attempt 2 uses the remaining budget.

    The first lane attempt fails after 600s of a 6h window; the retry the
    workflow issues through ``_execute_omnigent_with_admitted_capacity``
    must carry ``21600 - 600 = 21000`` (never a fresh 21600), each with a
    single-shot retry policy.
    """

    lane_start = datetime(2026, 9, 10, 5, 12, 50, tzinfo=timezone.utc)
    attempt_two_now = datetime(2026, 9, 10, 5, 22, 50, tzinfo=timezone.utc)
    now_calls = {"count": 0}

    def _fake_now() -> datetime:
        now_calls["count"] += 1
        return lane_start if now_calls["count"] == 1 else attempt_two_now

    monkeypatch.setattr(agent_run_module.workflow, "now", _fake_now)

    stc_calls: list[int] = []
    retry_policies: list[Any] = []

    async def _fake_execute(
        *,
        act_name: str,
        request: Any,
        admission: Any,
        parent_info: Any,
        stc_seconds: int,
        admit_capacity_before_activity: bool,
        execution_plan_admission: bool = False,
        retry_policy: Any = None,
    ) -> tuple[Any, Any]:
        stc_calls.append(stc_seconds)
        retry_policies.append(retry_policy)
        if len(stc_calls) == 1:
            raise RuntimeError("attempt 1 transport failure")
        return ({"ok": True}, None)

    run = MoonMindAgentRun()
    monkeypatch.setattr(
        run, "_execute_omnigent_with_admitted_capacity", _fake_execute
    )

    result, admitted_at = await run._execute_profile_bound_with_remaining_budget(
        act_name="integration.omnigent.profile_bound_execute",
        request=object(),
        admission=object(),
        parent_info=None,
        stc_seconds=21600,
        admit_capacity_before_activity=False,
        execution_plan_admission=False,
    )

    assert result == {"ok": True}
    assert admitted_at is None
    assert stc_calls == [
        21600,
        profile_bound_retry_start_to_close_seconds(
            first_stc_seconds=21600,
            elapsed_seconds=600.0,
        ),
    ]
    assert stc_calls[1] == 21000
    assert stc_calls[1] <= stc_calls[0]
    assert all(
        policy is not None
        and getattr(policy, "maximum_attempts", None) == 1
        for policy in retry_policies
    )
