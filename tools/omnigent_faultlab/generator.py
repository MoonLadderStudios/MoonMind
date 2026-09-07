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


def _run_signature(trace: ExecutionTrace) -> tuple:
    """The complete bounded observable trace of a run, for determinism checking.

    Deterministic replay (invariant 12) is a property of the *whole* observable
    trace, not only the decision journal: two runs that reach the same decisions
    can still diverge in the observations they surfaced, the crash windows and
    faults they hit, the provider side effects and requests they performed, the
    lifecycle phases they passed through, or the final durable state. Comparing
    only the reduced journal tuple would report such a run deterministic in
    violation of the replay contract, so the signature includes the full bounded
    trace.
    """

    journal = tuple(
        (
            e.round_index,
            e.decision_kind.value,
            e.reason_code,
            e.command_id,
            e.fenced,
            e.crash_window.value if e.crash_window is not None else None,
            e.observation_fault.value,
        )
        for e in trace.journal
    )
    phases = tuple(phase.value for phase in trace.phases)
    ledger = trace.ledger
    requests = tuple(
        (r.operation.value, r.idempotency_key, r.payload_digest, r.response.value)
        for r in ledger.requests
    )
    side_effects = tuple(
        (
            r.operation.value,
            r.idempotency_key,
            r.payload_digest,
            r.side_effect.value,
        )
        for r in ledger.side_effects
    )
    observations = tuple(
        (o.operation.value, o.raw_status, o.delivered) for o in ledger.observations
    )
    return (
        journal,
        phases,
        requests,
        side_effects,
        observations,
        tuple(trace.cleanup_effects),
        tuple(trace.crashes_fired),
        tuple((cid, kind.value) for cid, kind in trace.distinct_commands),
        trace.converged,
        trace.settled_kind.value if trace.settled_kind is not None else None,
        trace.reference_violation,
        trace.reference.final_phase().value,
        # The final durable state is the terminal fact a replay must reproduce.
        tuple(sorted(trace.final.model_dump(mode="json").items())),
    )


def is_deterministic(plan: FaultPlan) -> bool:
    """12. Running the same plan twice yields the identical observable trace."""

    first = run_plan(plan)
    second = run_plan(plan)
    return _run_signature(first) == _run_signature(second)


__all__ = ["generate_plan", "is_deterministic"]
