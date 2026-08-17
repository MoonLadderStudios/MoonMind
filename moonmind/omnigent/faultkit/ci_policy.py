"""CI corpus policy for the model-based reliability suite.

Owned by MoonLadderStudios/MoonMind#3709.

* Required PR CI runs a **deterministic bounded** corpus (:data:`PR_CI_SEEDS`)
  plus the fixed incident corpus, so Omnigent-owned changes are gated on a fast,
  predictable, non-flaky set.
* A **larger rotating** seed corpus (:func:`rotating_seeds`) runs on main or on a
  schedule, widening coverage without inflating PR latency.

Flaky retry is never an accepted result: a scenario that is not deterministic is
itself a failure (enforced by the ``deterministic_replay`` invariant). Explicit
time and scenario-count budgets keep the suite predictable.
"""

from __future__ import annotations

#: Deterministic bounded seed corpus for required PR CI (fast domain layer).
PR_CI_SEED_COUNT = 64
PR_CI_SEEDS: tuple[int, ...] = tuple(range(PR_CI_SEED_COUNT))

#: A fixed seed corpus that also runs in the required reliability journey.
FIXED_RELIABILITY_SEEDS: tuple[int, ...] = (
    12345,
    3698,
    3683,
    3665,
    3684,
    3694,
    3697,
    3696,
    3685,
)

#: Rotating corpus sizing for main / scheduled runs.
ROTATING_WINDOW_SIZE = 512

#: Predictability budgets. The domain layer must stay well under these.
PR_CI_TIME_BUDGET_SECONDS = 60
PR_CI_MAX_SCENARIOS = PR_CI_SEED_COUNT + 32
ROTATING_TIME_BUDGET_SECONDS = 30 * 60


def rotating_seeds(shard: int, *, window: int = ROTATING_WINDOW_SIZE) -> tuple[int, ...]:
    """Return the deterministic seed window for a rotating shard.

    ``shard`` is typically derived from the day-of-year or build number by the
    caller (kept out of this module so the function stays pure and replayable).
    """
    if shard < 0:
        raise ValueError("shard must be non-negative")
    start = shard * window
    return tuple(range(start, start + window))


__all__ = [
    "PR_CI_SEED_COUNT",
    "PR_CI_SEEDS",
    "FIXED_RELIABILITY_SEEDS",
    "ROTATING_WINDOW_SIZE",
    "PR_CI_TIME_BUDGET_SECONDS",
    "PR_CI_MAX_SCENARIOS",
    "ROTATING_TIME_BUDGET_SECONDS",
    "rotating_seeds",
]
