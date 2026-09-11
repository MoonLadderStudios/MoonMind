"""MoonLadderStudios/MoonMind#4226: profile-bound retry inherits remaining budget."""

from moonmind.workflows.temporal.workflows.agent_run import (
    OMNIGENT_PROFILE_BOUND_REMAINING_BUDGET_PATCH_ID,
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
