"""Failing-plan minimizer.

Source issue: MoonLadderStudios/MoonMind#3709.

Given a fault plan whose run violates one or more invariants, ``minimize_plan``
reduces it — dropping faults, crashes, and lowering budgets — while preserving
the same invariant failure, so the stored replay fixture is as small and
readable as possible. It is a deterministic greedy delta-debugging reduction: no
randomness, so a seed and failure minimize to the same result every time.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from .harness import FaultPlan, run_plan
from .invariants import check_all
from .scenario import ResponseBehavior

Oracle = Callable[[FaultPlan], frozenset]


def _default_oracle(plan: FaultPlan) -> frozenset:
    """Return the set of invariant names a plan violates."""

    return frozenset(
        name for name, found in check_all(run_plan(plan)).items() if found
    )


def minimize_plan(plan: FaultPlan, *, oracle: Oracle | None = None) -> FaultPlan:
    """Reduce ``plan`` while preserving its invariant failure.

    Raises ``ValueError`` if the plan does not currently fail any invariant —
    there is nothing to minimize and silently returning it would hide a
    non-reproducing "failure".
    """

    oracle = oracle or _default_oracle
    target = oracle(plan)
    if not target:
        raise ValueError("plan does not violate any invariant; nothing to minimize")

    def preserves(candidate: FaultPlan) -> bool:
        return target <= oracle(candidate)

    current = plan
    changed = True
    while changed:
        changed = False

        # 1. Drop observation faults one at a time.
        for index in range(len(current.observation_faults)):
            reduced = list(current.observation_faults)
            del reduced[index]
            candidate = replace(current, observation_faults=tuple(reduced))
            if preserves(candidate):
                current = candidate
                changed = True
                break
        if changed:
            continue

        # 2. Drop command crashes one at a time.
        for operation in list(current.command_crashes):
            crashes = dict(current.command_crashes)
            del crashes[operation]
            candidate = replace(current, command_crashes=crashes)
            if preserves(candidate):
                current = candidate
                changed = True
                break
        if changed:
            continue

        # 3. Lower the recovery round toward 1.
        if current.recovery_round > 1:
            candidate = replace(current, recovery_round=current.recovery_round - 1)
            if preserves(candidate):
                current = candidate
                changed = True
                continue

        # 4. Simplify scalar knobs that are not load-bearing for the failure.
        for candidate in (
            replace(current, submit_response=ResponseBehavior.SUCCESS),
            replace(current, max_turn_attempts=1),
            replace(current, requires_profile_lease=False),
            replace(current, requires_host=False),
        ):
            if candidate != current and preserves(candidate):
                current = candidate
                changed = True
                break

    return current


__all__ = ["minimize_plan", "Oracle"]
