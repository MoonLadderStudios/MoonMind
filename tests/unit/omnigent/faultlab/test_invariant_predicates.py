"""Detection tests for the reliability-invariant predicates.

Source issue: MoonLadderStudios/MoonMind#3709.

The generated corpus proves the invariants *hold* for the production reducer. It
cannot, on its own, prove the predicates would *catch* the regression each one
guards, because the correct reducer never emits the offending decision. These
tests inject the specific regression each strengthened predicate targets (a
terminal recorded while unknown vocabulary is being observed, a lease released
while a consumer is still active, a cleanup effect landing under a superseded
generation) and assert the predicate flags it — so a future reducer change that
reintroduced the bug would fail rather than silently converge.
"""

from __future__ import annotations

from moonmind.omnigent.faultlab import (
    JournalEntry,
    ObservationFault,
    run_plan,
)
from moonmind.omnigent.faultlab.corpus import INITIAL_CORPUS
from moonmind.omnigent.faultlab.harness import FaultPlan
from moonmind.omnigent.faultlab.invariants import (
    cleanup_safety,
    compatibility_safety,
    lease_safety,
)
from moonmind.omnigent.reconciler import DecisionKind


def _corpus_plan(scenario_id: str) -> FaultPlan:
    for entry in INITIAL_CORPUS:
        if entry.scenario_id == scenario_id:
            return entry.plan
    raise AssertionError(f"unknown corpus scenario {scenario_id!r}")


def _terminal_journal_entry(fault: ObservationFault) -> JournalEntry:
    return JournalEntry(
        round_index=99,
        decision_kind=DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
        reason_code="regression",
        command_id="session-1:g1:synthesize_terminal_from_snapshot",
        fenced=False,
        observation_fault=fault,
    )


# -- compatibility_safety (invariant 10) --------------------------------------


def test_compatibility_safety_holds_on_correct_unknown_vocab_run():
    """The real reducer never terminalizes on unknown vocabulary."""

    trace = run_plan(_corpus_plan("unknown-completion-vocabulary-mid-session"))
    assert compatibility_safety(trace) == []


def test_compatibility_safety_flags_terminal_on_unknown_vocab():
    """A terminal recorded during an unknown-vocab round is rejected."""

    trace = run_plan(_corpus_plan("unknown-completion-vocabulary-mid-session"))
    assert compatibility_safety(trace) == []
    # Simulate a regression that maps ``frobnicate`` straight to a terminal.
    trace.journal.append(_terminal_journal_entry(ObservationFault.UNKNOWN_VOCAB))
    assert compatibility_safety(trace), "unknown-vocab terminalization must be caught"


def test_compatibility_safety_ignores_terminal_in_an_honest_round():
    """A terminal recorded in an honest round is not a compatibility violation."""

    trace = run_plan(_corpus_plan("unknown-completion-vocabulary-mid-session"))
    trace.journal.append(_terminal_journal_entry(ObservationFault.HONEST))
    assert compatibility_safety(trace) == []


# -- lease_safety (invariant 7) -----------------------------------------------


def test_lease_safety_holds_on_correct_consumer_active_run():
    trace = run_plan(_corpus_plan("profile-lease-replacement-old-host-alive"))
    assert lease_safety(trace) == []


def test_lease_safety_flags_release_while_consumer_active():
    """Releasing capacity in a round where a consumer is observed active fails."""

    trace = run_plan(_corpus_plan("profile-lease-replacement-old-host-alive"))
    assert lease_safety(trace) == []
    trace.journal.append(
        JournalEntry(
            round_index=99,
            decision_kind=DecisionKind.RELEASE_LEASES,
            reason_code="regression",
            command_id="session-1:g1:release_leases",
            fenced=False,
            observation_fault=ObservationFault.CONSUMER_ACTIVE,
        )
    )
    assert lease_safety(trace), "release while consumer active must be caught"


def test_lease_safety_ignores_fenced_release_while_consumer_active():
    """A fenced (rejected) release performs no side effect and is not a violation."""

    trace = run_plan(_corpus_plan("profile-lease-replacement-old-host-alive"))
    trace.journal.append(
        JournalEntry(
            round_index=99,
            decision_kind=DecisionKind.RELEASE_LEASES,
            reason_code="regression",
            command_id="session-1:g1:release_leases",
            fenced=True,
            observation_fault=ObservationFault.CONSUMER_ACTIVE,
        )
    )
    assert lease_safety(trace) == []


# -- cleanup_safety (invariant 8) ---------------------------------------------


def test_cleanup_safety_holds_on_correct_cleanup_race_run():
    trace = run_plan(_corpus_plan("cleanup-races-new-continuation"))
    assert cleanup_safety(trace) == []
    # Every recorded cleanup effect ran under the authorized (final) generation.
    authorized = trace.final.fencing_generation
    assert all(gen == authorized for _cid, gen in trace.cleanup_effects)


def test_cleanup_safety_flags_effect_under_superseded_generation():
    """A cleanup effect recorded under a superseded generation is rejected."""

    trace = run_plan(_corpus_plan("cleanup-races-new-continuation"))
    assert cleanup_safety(trace) == []
    authorized = trace.final.fencing_generation
    # A replacement continuation has moved authority forward; a stale cleanup
    # command from the previous generation still deletes under the old number.
    trace.cleanup_effects.append(
        (f"session-1:g{authorized - 1}:begin_cleanup", authorized - 1)
    )
    assert cleanup_safety(trace), "stale-generation cleanup must be caught"
