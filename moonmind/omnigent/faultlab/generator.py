"""Seed-based deterministic fault-plan generator.

Source issue: MoonLadderStudios/MoonMind#3709.

Given a seed, ``generate_plan`` produces a bounded :class:`FaultPlan` that
interleaves transport, observation, and crash faults across the lifecycle. The
generation is a pure function of the seed (``random.Random(seed)`` only), so a
seed and scenario reproduce the same decisions and provider observations
(invariant 12 — deterministic replay). No wall clock or global RNG is used.
"""

from __future__ import annotations

import random

from .harness import ExecutionTrace, FaultPlan, ObservationFault, run_plan
from .scenario import CommandWindow, LogicalOperation, ResponseBehavior

_GROUND_TRUTHS = ("success", "failure", "cancelled")

_OBSERVATION_FAULTS = (
    ObservationFault.DROP_SNAPSHOT,
    ObservationFault.STALE_SESSION,
    ObservationFault.UNKNOWN_VOCAB,
    ObservationFault.RUNNING,
    ObservationFault.MISSED_EDGE,
    ObservationFault.EVIDENCE_DELAY,
    ObservationFault.CONSUMER_ACTIVE,
)

_SUBMIT_RESPONSES = (
    ResponseBehavior.SUCCESS,
    ResponseBehavior.DROP,
    ResponseBehavior.TIMEOUT,
)

#: Logical commands that can be interrupted at a window. Read-only observation
#: operations are excluded because they carry no durable side effect.
_CRASHABLE_OPERATIONS = (
    LogicalOperation.ENSURE_PROFILE_LEASE,
    LogicalOperation.ENSURE_HOST,
    LogicalOperation.ENSURE_SESSION,
    LogicalOperation.SUBMIT_TURN,
    LogicalOperation.HARVEST_EVIDENCE,
    LogicalOperation.BEGIN_CLEANUP,
    LogicalOperation.RELEASE_LEASES,
)

_COMMAND_WINDOWS = tuple(CommandWindow)


def generate_plan(seed: int) -> FaultPlan:
    """Deterministically derive a bounded fault plan from ``seed``."""

    rng = random.Random(seed)

    ground_truth = rng.choice(_GROUND_TRUTHS)
    desired_cancel = rng.random() < 0.15

    # A short, bounded observation-fault window keeps runs fast and guarantees a
    # decidable recovery horizon.
    fault_len = rng.randint(1, 5)
    observation_faults = tuple(
        rng.choice(_OBSERVATION_FAULTS) for _ in range(fault_len)
    )
    recovery_round = rng.randint(3, 9)

    # Crash a random subset of commands at a random window.
    command_crashes: dict[LogicalOperation, CommandWindow] = {}
    for operation in _CRASHABLE_OPERATIONS:
        if rng.random() < 0.4:
            command_crashes[operation] = rng.choice(_COMMAND_WINDOWS)

    return FaultPlan(
        seed=seed,
        requires_profile_lease=rng.random() < 0.85,
        requires_host=rng.random() < 0.85,
        requires_cleanup=rng.random() < 0.9,
        desired_cancel=desired_cancel,
        ground_truth_terminal=ground_truth,
        max_turn_attempts=rng.randint(1, 3),
        recovery_round=recovery_round,
        submit_response=rng.choice(_SUBMIT_RESPONSES),
        observation_faults=observation_faults,
        command_crashes=command_crashes,
    )


def _journal_signature(trace: ExecutionTrace) -> list[tuple]:
    return [
        (e.round_index, e.decision_kind.value, e.reason_code, e.command_id, e.fenced)
        for e in trace.journal
    ]


def is_deterministic(plan: FaultPlan) -> bool:
    """12. Running the same plan twice yields the identical decision journal."""

    first = run_plan(plan)
    second = run_plan(plan)
    return _journal_signature(first) == _journal_signature(second)


__all__ = ["generate_plan", "is_deterministic"]
