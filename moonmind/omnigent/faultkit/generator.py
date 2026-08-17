"""Seeded, deterministic action-sequence generator.

Owned by MoonLadderStudios/MoonMind#3709.

The generator turns a seed into a valid, convergent Omnigent lifecycle scenario
that stresses dropped responses, duplicated/reordered events, ambiguous
submissions, fencing generations, lease pressure, and cleanup races. A correct
reconciler must satisfy every invariant on every generated scenario; a violation
is a real reliability defect the model-based suite has surfaced.

The generator never uses global randomness -- only ``random.Random(seed)`` -- so
a seed reproduces the same scenario and therefore the same decisions and
observations ("deterministic replay").
"""

from __future__ import annotations

import random

from moonmind.omnigent.faultkit.commands import COMMAND_WINDOWS, LogicalCommand
from moonmind.omnigent.faultkit.scenario import (
    CANONICAL_SCENARIO_SCHEMA_VERSION,
    ResponseMode,
    Scenario,
    ScenarioStep,
    SideEffectKind,
)


def _step(on: LogicalCommand, **kwargs) -> ScenarioStep:  # type: ignore[no-untyped-def]
    return ScenarioStep(on=on, **kwargs)


def generate_scenario(seed: int, *, max_turns: int = 3) -> Scenario:
    """Generate one deterministic, convergent fault scenario from ``seed``."""
    rng = random.Random(seed)
    steps: list[ScenarioStep] = []

    # Bring up a session and hold a lease.
    steps.append(_step(LogicalCommand.ENSURE_SESSION, side_effect=SideEffectKind.CREATED))
    steps.append(_step(LogicalCommand.LEASE_ACQUIRE))

    turns = rng.randint(1, max_turns)
    for turn_index in range(1, turns + 1):
        label = str(turn_index)
        submit_lost = rng.random() < 0.5
        response = ResponseMode.DROP if submit_lost else ResponseMode.SUCCESS
        steps.append(
            _step(
                LogicalCommand.SUBMIT_TURN,
                side_effect=SideEffectKind.ACCEPTED,
                response=response,
                turn=label,
            )
        )
        # A duplicate submission of the same turn identity: a correct reconciler
        # must reconcile it, never re-submit.
        if rng.random() < 0.4:
            steps.append(
                _step(
                    LogicalCommand.SUBMIT_TURN,
                    side_effect=SideEffectKind.ACCEPTED,
                    response=ResponseMode.SUCCESS,
                    turn=label,
                )
            )
        # Some noisy, unreliable event reads.
        if rng.random() < 0.6:
            steps.append(
                _step(
                    LogicalCommand.READ_EVENTS,
                    emit=({"type": "turn.running", "id": f"e{turn_index}a"},),
                    disconnect=rng.random() < 0.3,
                    duplicate=rng.random() < 0.3,
                    reorder=rng.random() < 0.3,
                )
            )
        if submit_lost:
            # Reconcile the ambiguous submission from an authoritative snapshot.
            steps.append(
                _step(
                    LogicalCommand.RECONCILE,
                    snapshot={"sessionState": "idle", "turnState": "completed"},
                )
            )
        # Guaranteed authoritative terminal observation -> convergence.
        steps.append(
            _step(
                LogicalCommand.OBSERVE_SNAPSHOT,
                snapshot={
                    "sessionState": "idle",
                    "turnState": "completed",
                    "unfinishedToolCalls": 0,
                },
                turn=label,
            )
        )
        # Occasionally a stale snapshot arrives after the terminal frontier.
        if rng.random() < 0.3:
            steps.append(
                _step(
                    LogicalCommand.OBSERVE_SNAPSHOT,
                    snapshot={"sessionState": "running", "turnState": "running"},
                )
            )

    # Occasionally replace the host/lease generation and prove fencing.
    if rng.random() < 0.4:
        steps.append(
            _step(LogicalCommand.HOST_REPLACE, side_effect=SideEffectKind.REPLACED)
        )
        # A former-generation write must be fenced (generation 1 < current 2).
        steps.append(
            _step(
                LogicalCommand.SUBMIT_TURN,
                side_effect=SideEffectKind.ACCEPTED,
                generation=1,
                turn="stale",
            )
        )

    # Occasionally exercise a command-window crash and prove recovery.
    if rng.random() < 0.5:
        window = rng.choice(COMMAND_WINDOWS)
        steps.append(
            _step(
                LogicalCommand.OBSERVE_SNAPSHOT,
                snapshot={"sessionState": "idle", "turnState": "completed"},
                crash_at=window,
            )
        )

    # Tear down: cleanup retains evidence, release lease last.
    steps.append(_step(LogicalCommand.CLEANUP))
    steps.append(_step(LogicalCommand.DELETE_SESSION, side_effect=SideEffectKind.DELETED))
    steps.append(_step(LogicalCommand.LEASE_RELEASE))

    return Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=seed,
        steps=tuple(steps),
        name=f"generated-seed-{seed}",
        metadata={"generator": "faultkit", "seed": seed},
    )


def generate_corpus(seeds: range | list[int], *, max_turns: int = 3) -> list[Scenario]:
    """Generate a deterministic corpus of scenarios from a range of seeds."""
    return [generate_scenario(seed, max_turns=max_turns) for seed in seeds]


__all__ = ["generate_scenario", "generate_corpus"]
