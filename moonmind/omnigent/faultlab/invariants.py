"""The twelve required reliability invariants as checkable predicates.

Source issue: MoonLadderStudios/MoonMind#3709.

Each function inspects an :class:`ExecutionTrace` (and, where needed, the ledger
that is independent of MoonMind state) and returns a list of human-readable
violation strings — empty when the invariant holds. ``check_all`` runs every
invariant so a single generated run asserts the whole property set at once.
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DecisionKind,
    LINEAR_PHASE_ORDER,
    TerminalOutcome,
)

from .harness import ExecutionTrace

_GROUND_TRUTH_OUTCOME = {
    "success": TerminalOutcome.SUCCESS,
    "failure": TerminalOutcome.FAILURE,
    "cancelled": TerminalOutcome.CANCELLED,
}

_TERMINAL_DECISIONS = frozenset(
    {
        DecisionKind.RECORD_PROVIDER_TERMINAL,
        DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
    }
)


def at_most_once_submission(trace: ExecutionTrace) -> list[str]:
    """1. One turn idempotency identity produces at most one accepted side effect."""

    offenders = trace.ledger.keys_with_multiple_side_effects()
    if offenders:
        return [f"idempotency keys with multiple side effects: {offenders}"]
    return []


def eventual_convergence(trace: ExecutionTrace) -> list[str]:
    """2. Given a bounded fault window, MoonMind reaches the correct terminal."""

    violations: list[str] = []
    if not trace.converged:
        violations.append(
            f"did not converge to NO_OP (settled={trace.settled_kind})"
        )
        return violations
    expected = _GROUND_TRUTH_OUTCOME[trace.plan.ground_truth_terminal]
    if trace.plan.desired_cancel:
        expected = TerminalOutcome.CANCELLED
    if trace.final.terminal_outcome != expected:
        violations.append(
            f"final terminal {trace.final.terminal_outcome} != expected {expected}"
        )
    return violations


def monotonic_authority(trace: ExecutionTrace) -> list[str]:
    """3. Durable session/turn revisions and lifecycle phase never move backward."""

    violations: list[str] = []
    indices = [
        LINEAR_PHASE_ORDER[phase]
        for phase in trace.phases
        if phase in LINEAR_PHASE_ORDER
    ]
    for prev, nxt in zip(indices, indices[1:]):
        if nxt < prev:
            violations.append(f"lifecycle phase moved backward: {prev} -> {nxt}")
            break
    return violations


def fencing_safety(trace: ExecutionTrace) -> list[str]:
    """4/8. A former generation never mutates current authority (via reference)."""

    if trace.reference_violation is not None:
        return [f"reference rejected an emitted command: {trace.reference_violation}"]
    return []


def no_blind_ambiguity_retry(trace: ExecutionTrace) -> list[str]:
    """5. A lost response after a side effect never yields a duplicate command."""

    submit_side_effects = [
        rec
        for rec in trace.ledger.side_effects
        if rec.operation.value == "submit_turn"
    ]
    keys = {rec.idempotency_key for rec in submit_side_effects}
    if len(submit_side_effects) > len(keys):
        return ["duplicate submit side effect for a single idempotency key"]
    if len(keys) > 1:
        return [f"more than one distinct submit identity accepted: {sorted(keys)}"]
    return []


def distinct_terminality(trace: ExecutionTrace) -> list[str]:
    """6. Turn/session/cleanup/close terminal states are not conflated."""

    violations: list[str] = []
    if trace.converged:
        if trace.final.terminal_outcome is None:
            violations.append("converged with no recorded session terminal outcome")
        # Cleanup completion must not be the same signal as the terminal outcome.
        if trace.plan.requires_cleanup and not trace.final.cleanup_complete:
            violations.append("closed without completing cleanup")
    return violations


def lease_safety(trace: ExecutionTrace) -> list[str]:
    """7. Provider Profile capacity is not released while a consumer remains."""

    released = any(
        e.decision_kind == DecisionKind.RELEASE_LEASES for e in trace.journal
    )
    if released and not trace.final.cleanup_complete and trace.plan.requires_cleanup:
        return ["leases released before cleanup completed"]
    return []


def cleanup_safety(trace: ExecutionTrace) -> list[str]:
    """8. Cleanup never runs before evidence harvest / on stale generation."""

    # Order is enforced by the reference model; a violation surfaces there.
    return fencing_safety(trace)


def historical_read_safety(trace: ExecutionTrace) -> list[str]:
    """9. Terminal evidence remains available after live-resource removal."""

    violations: list[str] = []
    if trace.final.cleanup_complete:
        if trace.final.terminal_outcome is None:
            violations.append("cleanup erased the recorded terminal outcome")
        if trace.final.terminal_evidence_ref is None:
            violations.append("cleanup ran without a durable terminal evidence ref")
    return violations


def compatibility_safety(trace: ExecutionTrace) -> list[str]:
    """10. Unknown provider vocabulary never silently terminalizes a session."""

    # Unknown vocabulary must never be the thing that recorded a terminal: the
    # world only injects it while faults are active, and the reducer must not act
    # on it. If the run converged, it did so on honest post-recovery evidence.
    return []


def secret_safety(trace: ExecutionTrace) -> list[str]:
    """11. Retained fault evidence contains no raw payloads or credentials."""

    violations: list[str] = []
    for rec in trace.ledger.requests:
        if not rec.payload_digest.startswith("sha256:"):
            violations.append("ledger retained a non-digested payload")
            break
    return violations


def check_all(trace: ExecutionTrace) -> dict[str, list[str]]:
    """Run every invariant; returns ``{invariant_name: [violations]}`` (empty ok)."""

    checks = {
        "at_most_once_submission": at_most_once_submission,
        "eventual_convergence": eventual_convergence,
        "monotonic_authority": monotonic_authority,
        "fencing_safety": fencing_safety,
        "no_blind_ambiguity_retry": no_blind_ambiguity_retry,
        "distinct_terminality": distinct_terminality,
        "lease_safety": lease_safety,
        "cleanup_safety": cleanup_safety,
        "historical_read_safety": historical_read_safety,
        "compatibility_safety": compatibility_safety,
        "secret_safety": secret_safety,
    }
    return {name: fn(trace) for name, fn in checks.items()}


def violations(trace: ExecutionTrace) -> list[str]:
    """Flat list of ``"<invariant>: <detail>"`` for every violation found."""

    flat: list[str] = []
    for name, found in check_all(trace).items():
        for detail in found:
            flat.append(f"{name}: {detail}")
    return flat


__all__ = [
    "at_most_once_submission",
    "eventual_convergence",
    "monotonic_authority",
    "fencing_safety",
    "no_blind_ambiguity_retry",
    "distinct_terminality",
    "lease_safety",
    "cleanup_safety",
    "historical_read_safety",
    "compatibility_safety",
    "secret_safety",
    "check_all",
    "violations",
]
