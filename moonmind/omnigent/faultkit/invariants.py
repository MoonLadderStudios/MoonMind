"""The twelve named reliability properties enforced by generated tests.

Owned by MoonLadderStudios/MoonMind#3709.

Each invariant inspects a :class:`~moonmind.omnigent.faultkit.harness.RunResult`
and returns an :class:`InvariantViolation` when the property is broken. Every
escaped production incident should map onto one of these generalized invariants
rather than a bespoke one-off assertion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from moonmind.omnigent.faultkit.commands import LogicalCommand
from moonmind.omnigent.faultkit.reconciler import Decision
from moonmind.omnigent.faultkit.recording import scan_for_secrets
from moonmind.omnigent.faultkit.reference_model import SessionState, TurnState
from moonmind.omnigent.faultkit.scenario import SideEffectKind

if TYPE_CHECKING:  # pragma: no cover
    from moonmind.omnigent.faultkit.harness import RunResult


@dataclass(frozen=True)
class InvariantViolation:
    invariant: str
    detail: str


@dataclass(frozen=True)
class Invariant:
    key: str
    title: str
    check: "Callable[[RunResult], InvariantViolation | None]"


# -- individual property checks ------------------------------------------------


def _at_most_once_submission(result: "RunResult") -> InvariantViolation | None:
    keys = {
        eff.idempotency_key
        for eff in result.recorder.side_effects
        if eff.kind is SideEffectKind.ACCEPTED and eff.idempotency_key
    }
    for key in keys:
        count = result.recorder.accepted_side_effect_count(key)
        if count > 1:
            return InvariantViolation(
                "at_most_once_submission",
                f"idempotency identity {key!r} produced {count} accepted side effects",
            )
    return None


def _eventual_convergence(result: "RunResult") -> InvariantViolation | None:
    ref = result.reference_view
    got = result.reconciler_view
    # When the oracle reached a terminal turn from sufficient evidence, so must
    # the reconciler.
    ref_terminal = ref.turn_state in {TurnState.COMPLETED, TurnState.FAILED}
    got_terminal = got.turn_state in {TurnState.COMPLETED, TurnState.FAILED}
    if ref_terminal and not got_terminal:
        return InvariantViolation(
            "eventual_convergence",
            f"oracle reached {ref.turn_state.value} but reconciler stayed "
            f"{got.turn_state.value}",
        )
    if ref_terminal and got_terminal and ref.turn_state != got.turn_state:
        return InvariantViolation(
            "eventual_convergence",
            f"oracle terminal {ref.turn_state.value} != reconciler "
            f"{got.turn_state.value}",
        )
    return None


def _monotonic_authority(result: "RunResult") -> InvariantViolation | None:
    prev_session = 0
    prev_turn = 0
    for session_rev, turn_rev in result.reconciler.revision_history:
        if session_rev < prev_session or turn_rev < prev_turn:
            return InvariantViolation(
                "monotonic_authority",
                f"revision moved backward: session {prev_session}->{session_rev}, "
                f"turn {prev_turn}->{turn_rev}",
            )
        prev_session, prev_turn = session_rev, turn_rev
    return None


def _fencing_safety(result: "RunResult") -> InvariantViolation | None:
    # Replay side effects in commit order: a mutating write must never commit
    # under a generation that was already superseded at the time it was attempted.
    # (A write that was current when committed and only superseded later is safe.)
    current = 1
    for eff in sorted(result.recorder.side_effects, key=lambda e: e.sequence):
        if eff.kind is SideEffectKind.REPLACED:
            current += 1
            continue
        if eff.kind in {
            SideEffectKind.ACCEPTED,
            SideEffectKind.DELETED,
        } and eff.generation < current:
            return InvariantViolation(
                "fencing_safety",
                f"former generation {eff.generation} committed {eff.kind.value} "
                f"under current generation {current}",
            )
    return None


def _no_blind_ambiguity_retry(result: "RunResult") -> InvariantViolation | None:
    for key, count in result.reconciler.blind_resubmissions.items():
        if count > 0:
            return InvariantViolation(
                "no_blind_ambiguity_retry",
                f"reconciler blindly re-submitted the active identity {key!r} "
                f"{count} time(s) instead of reconciling",
            )
    return None


def _distinct_terminality(result: "RunResult") -> InvariantViolation | None:
    view = result.reconciler_view
    # A session may only be terminal when its turn is terminal (or it was never
    # started); turn/session terminality must not be conflated with mid-run.
    if view.session_state is SessionState.TERMINAL and view.turn_state not in {
        TurnState.COMPLETED,
        TurnState.FAILED,
        TurnState.NONE,
    }:
        return InvariantViolation(
            "distinct_terminality",
            f"session terminal while turn is {view.turn_state.value}",
        )
    return None


def _lease_safety(result: "RunResult") -> InvariantViolation | None:
    if result.reconciler.lease_consumers > 0 and not result.reconciler_view.lease_held:
        return InvariantViolation(
            "lease_safety",
            "lease released while a credential consumer remained",
        )
    return None


def _cleanup_safety(result: "RunResult") -> InvariantViolation | None:
    for entry in result.reconciler.journal:
        if (
            entry.command is LogicalCommand.CLEANUP
            and entry.decision is not Decision.SKIP_CLEANUP_GUARD
        ):
            step = result.scenario.steps[entry.step_index]
            if step.generation and step.generation > result.reconciler.generation:
                return InvariantViolation(
                    "cleanup_safety",
                    "cleanup executed against a replacement-generation resource",
                )
    return None


def _historical_read_safety(result: "RunResult") -> InvariantViolation | None:
    view = result.reconciler_view
    reached_terminal = view.turn_state in {TurnState.COMPLETED, TurnState.FAILED}
    removed_live = any(
        eff.kind is SideEffectKind.DELETED for eff in result.recorder.side_effects
    ) or view.cleanup_state.value == "done"
    if reached_terminal and removed_live and not view.terminal_evidence_retained:
        return InvariantViolation(
            "historical_read_safety",
            "terminal evidence not retained after live-resource removal",
        )
    return None


def _compatibility_safety(result: "RunResult") -> InvariantViolation | None:
    # Unknown scenario schema must never have executed.
    if result.scenario.quarantined or not result.scenario.supported:
        return InvariantViolation(
            "compatibility_safety",
            "a quarantined/unsupported scenario schema was executed",
        )
    return None


def _secret_safety(result: "RunResult") -> InvariantViolation | None:
    payload = {
        "journal": result.decision_journal(),
        "recorder": result.recorder.to_journal(),
        "scenario": result.scenario.to_mapping(),
    }
    leaks = scan_for_secrets(payload)
    if leaks:
        return InvariantViolation(
            "secret_safety",
            f"retained fault evidence contains secret-like content: {leaks}",
        )
    return None


def _deterministic_replay(result: "RunResult") -> InvariantViolation | None:
    # Re-run the same scenario; decisions and observations must match exactly.
    from moonmind.omnigent.faultkit.harness import run_scenario

    if result.scenario.quarantined or not result.scenario.supported:
        # An unexecutable scenario is handled by compatibility_safety.
        return None
    replay = run_scenario(result.scenario)
    if replay.decision_journal() != result.decision_journal():
        return InvariantViolation(
            "deterministic_replay",
            "replaying the scenario produced a different decision journal",
        )
    if replay.recorder.to_journal() != result.recorder.to_journal():
        return InvariantViolation(
            "deterministic_replay",
            "replaying the scenario produced different provider observations",
        )
    return None


INVARIANTS: tuple[Invariant, ...] = (
    Invariant("at_most_once_submission", "At-most-once logical submission", _at_most_once_submission),
    Invariant("eventual_convergence", "Eventual convergence", _eventual_convergence),
    Invariant("monotonic_authority", "Monotonic authority", _monotonic_authority),
    Invariant("fencing_safety", "Fencing safety", _fencing_safety),
    Invariant("no_blind_ambiguity_retry", "No blind ambiguity retry", _no_blind_ambiguity_retry),
    Invariant("distinct_terminality", "Distinct terminality", _distinct_terminality),
    Invariant("lease_safety", "Lease safety", _lease_safety),
    Invariant("cleanup_safety", "Cleanup safety", _cleanup_safety),
    Invariant("historical_read_safety", "Historical-read safety", _historical_read_safety),
    Invariant("compatibility_safety", "Compatibility safety", _compatibility_safety),
    Invariant("secret_safety", "Secret safety", _secret_safety),
    Invariant("deterministic_replay", "Deterministic replay", _deterministic_replay),
)


def check_invariants(
    result: "RunResult", *, skip: frozenset[str] = frozenset()
) -> list[InvariantViolation]:
    """Return every invariant violated by ``result`` (empty list means safe)."""
    violations: list[InvariantViolation] = []
    for invariant in INVARIANTS:
        if invariant.key in skip:
            continue
        violation = invariant.check(result)
        if violation is not None:
            violations.append(violation)
    return violations


__all__ = [
    "Invariant",
    "InvariantViolation",
    "INVARIANTS",
    "check_invariants",
]
