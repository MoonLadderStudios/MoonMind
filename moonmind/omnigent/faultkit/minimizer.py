"""Delta-debug minimizer for failing generated scenarios.

Owned by MoonLadderStudios/MoonMind#3709.

Given a scenario and a predicate that returns ``True`` while the scenario still
reproduces a target invariant failure, :func:`minimize_scenario` returns the
smallest subsequence of steps that preserves the failure. The result is a safe,
bounded declarative fault scenario suitable for storage under the reliability
replay corpus.

The algorithm is deterministic ddmin, so a seed + scenario always minimize to
the same result ("deterministic replay").
"""

from __future__ import annotations

from typing import Callable

from moonmind.omnigent.faultkit.scenario import Scenario, ScenarioStep

Predicate = Callable[[Scenario], bool]


def _with_steps(scenario: Scenario, steps: tuple[ScenarioStep, ...]) -> Scenario:
    return Scenario(
        schema_version=scenario.schema_version,
        seed=scenario.seed,
        steps=steps,
        name=scenario.name,
        metadata=scenario.metadata,
    )


def minimize_scenario(scenario: Scenario, predicate: Predicate) -> Scenario:
    """Return the minimal subsequence of steps that still fails ``predicate``.

    ``predicate`` must be ``True`` for ``scenario`` to begin with; otherwise the
    original scenario is returned unchanged.
    """

    steps = list(scenario.steps)
    if not predicate(scenario):
        return scenario

    n = 2
    while len(steps) >= 2:
        chunk_size = max(1, len(steps) // n)
        reduced = False
        # Try removing each complement (ddmin "increase granularity") first, then
        # each chunk.
        for start in range(0, len(steps), chunk_size):
            complement = steps[:start] + steps[start + chunk_size :]
            if not complement:
                continue
            candidate = _with_steps(scenario, tuple(complement))
            if predicate(candidate):
                steps = complement
                n = max(n - 1, 2)
                reduced = True
                break
        if not reduced:
            if n >= len(steps):
                break
            n = min(len(steps), n * 2)

    return _with_steps(scenario, tuple(steps))


__all__ = ["minimize_scenario", "Predicate"]
