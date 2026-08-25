"""Progress-aware execution budget contract.

Regression cover for MoonLadderStudios/MoonMind#3771: an agent run was terminated
at its flat one-hour budget while it was actively working — files were written
three seconds before the kill — and the failure was reported as "no observable
progress". The budget below only ends a run whose progress has actually gone
stale, or which has reached a hard ceiling.
"""

from __future__ import annotations

import pytest

from moonmind.schemas.agent_runtime_models import (
    DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
    DEFAULT_MANAGED_TIMEOUT_SECONDS,
    DEFAULT_PROGRESS_EXTENSION_FACTOR,
    DEFAULT_PROGRESS_STALL_SECONDS,
    MAX_EXECUTION_BUDGET_SECONDS,
    ExecutionBudget,
    evaluate_execution_budget,
    resolve_execution_budget,
)
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


# ---------------------------------------------------------------------------
# Budget resolution
# ---------------------------------------------------------------------------


def test_managed_default_budget_keeps_base_and_derives_ceiling():
    budget = resolve_execution_budget(agent_kind="managed", timeout_policy=None)

    # The base window is unchanged, so runs that used to finish inside an hour
    # behave exactly as before.
    assert budget.base_seconds == DEFAULT_MANAGED_TIMEOUT_SECONDS
    assert budget.max_seconds == (
        DEFAULT_MANAGED_TIMEOUT_SECONDS * DEFAULT_PROGRESS_EXTENSION_FACTOR
    )
    assert budget.progress_stall_seconds == DEFAULT_PROGRESS_STALL_SECONDS


def test_external_default_budget_is_capped_at_the_absolute_maximum():
    budget = resolve_execution_budget(agent_kind="external")

    assert budget.base_seconds == DEFAULT_EXTERNAL_TIMEOUT_SECONDS
    assert budget.max_seconds == MAX_EXECUTION_BUDGET_SECONDS


def test_tight_explicit_budget_gets_a_tight_ceiling():
    """An explicit small budget must not be extended to a six-hour ceiling."""

    budget = resolve_execution_budget(timeout_policy={"timeout_seconds": 60})

    assert budget.base_seconds == 60
    assert budget.max_seconds == 60 * DEFAULT_PROGRESS_EXTENSION_FACTOR
    # The stall window never exceeds the base budget: a run is not given more
    # time to prove it is alive than it was given to finish.
    assert budget.progress_stall_seconds == 60


def test_explicit_ceiling_and_stall_window_win():
    budget = resolve_execution_budget(
        timeout_policy={
            "timeout_seconds": 3600,
            "max_timeout_seconds": 7200,
            "progress_stall_seconds": 300,
        }
    )

    assert (budget.base_seconds, budget.max_seconds) == (3600, 7200)
    assert budget.progress_stall_seconds == 300


def test_requesting_a_larger_base_never_shortens_the_run():
    """A base above the default ceiling raises the ceiling with it."""

    budget = resolve_execution_budget(timeout_policy={"timeout_seconds": 40000})

    assert budget.base_seconds == 40000
    assert budget.max_seconds >= budget.base_seconds


def test_degraded_timeout_policy_falls_back_per_field():
    """An in-flight payload predating the new fields still resolves."""

    budget = resolve_execution_budget(
        timeout_policy={
            "timeout_seconds": "not-a-number",
            "max_timeout_seconds": 0,
            "progress_stall_seconds": None,
        }
    )

    assert budget.base_seconds == DEFAULT_MANAGED_TIMEOUT_SECONDS
    assert budget.max_seconds == (
        DEFAULT_MANAGED_TIMEOUT_SECONDS * DEFAULT_PROGRESS_EXTENSION_FACTOR
    )
    assert budget.progress_stall_seconds == DEFAULT_PROGRESS_STALL_SECONDS


def test_published_policy_round_trips_the_whole_budget():
    """What the workflow publishes must resolve back to the same budget."""

    original = resolve_execution_budget(timeout_policy={"timeout_seconds": 3600})
    republished = resolve_execution_budget(
        timeout_policy=original.as_timeout_policy()
    )

    assert republished == original


# ---------------------------------------------------------------------------
# Budget evaluation
# ---------------------------------------------------------------------------


_BUDGET = ExecutionBudget(
    base_seconds=3600,
    max_seconds=21600,
    progress_stall_seconds=900,
)


@pytest.mark.parametrize(
    ("elapsed", "idle", "expected"),
    [
        # Inside the base window, nothing terminates the run.
        (0.0, None, "continue"),
        (3599.0, None, "continue"),
        (3599.0, 3599.0, "continue"),
        # The #3771 shape: base budget reached, progress three seconds ago.
        (3600.0, 3.0, "continue"),
        # Well past the base budget and still progressing.
        (10000.0, 30.0, "continue"),
        # Progress went stale: this is a genuinely stuck run.
        (3600.0, 900.0, "expired_no_progress"),
        (3600.0, 5000.0, "expired_no_progress"),
        # No progress was ever observed — not evidence of health.
        (3600.0, None, "expired_no_progress"),
        # The ceiling is absolute: fresh progress does not extend past it.
        (21600.0, 0.0, "expired_max_budget"),
        (30000.0, 0.0, "expired_max_budget"),
    ],
)
def test_budget_verdicts(elapsed, idle, expected):
    assert (
        evaluate_execution_budget(
            budget=_BUDGET,
            elapsed_seconds=elapsed,
            idle_progress_seconds=idle,
        )
        == expected
    )


def test_a_run_that_never_progresses_is_not_extended_by_a_single_second():
    """The flat behaviour is preserved exactly when there is no evidence."""

    for elapsed in (3600.0, 3601.0, 7200.0):
        assert (
            evaluate_execution_budget(
                budget=_BUDGET,
                elapsed_seconds=elapsed,
                idle_progress_seconds=None,
            )
            == "expired_no_progress"
        )


# ---------------------------------------------------------------------------
# The two primitives must agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("elapsed", "idle"),
    [
        (0.0, None),
        (1800.0, 10.0),
        (3599.0, 3598.0),
        (3600.0, 0.0),
        (3600.0, 899.0),
        (3600.0, 900.0),
        (3600.0, None),
        (12000.0, 100.0),
        (21599.0, 0.0),
        (21600.0, 0.0),
        (40000.0, 5.0),
    ],
)
def test_effective_deadline_agrees_with_the_verdict(elapsed, idle):
    """Every remaining-time computation in the poll loop derives from the
    deadline, while the terminal decision comes from the verdict. If the two
    disagreed, a run could be told it may continue and then be handed a negative
    poll budget (or the reverse), so this equivalence is load-bearing.
    """

    verdict = evaluate_execution_budget(
        budget=_BUDGET,
        elapsed_seconds=elapsed,
        idle_progress_seconds=idle,
    )
    deadline = MoonMindAgentRun._effective_deadline_seconds(
        budget=_BUDGET,
        elapsed_seconds=elapsed,
        idle_progress_seconds=idle,
    )

    if verdict == "continue":
        assert deadline > elapsed
    else:
        assert deadline <= elapsed
