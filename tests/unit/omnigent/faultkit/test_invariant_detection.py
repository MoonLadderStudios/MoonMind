"""Prove each of the twelve invariants fires when its property is broken.

MoonLadderStudios/MoonMind#3709. A safety suite that can never fail is worthless;
these tests confirm the checker detects each violation class.
"""

from __future__ import annotations

from moonmind.omnigent.faultkit.commands import LogicalCommand
from moonmind.omnigent.faultkit.harness import RunResult, run_scenario
from moonmind.omnigent.faultkit.invariants import check_invariants
from moonmind.omnigent.faultkit.reconciler import (
    Decision,
    FaultKitReconciler,
    JournalEntry,
)
from moonmind.omnigent.faultkit.recording import ProviderRecorder, RecordedSideEffect
from moonmind.omnigent.faultkit.reference_model import (
    SessionState,
    SessionView,
    TerminalKind,
    TurnState,
)
from moonmind.omnigent.faultkit.scenario import (
    CANONICAL_SCENARIO_SCHEMA_VERSION,
    Scenario,
    ScenarioStep,
    SideEffectKind,
)


def _clean_result() -> RunResult:
    scenario = Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=1,
        steps=(
            ScenarioStep(on=LogicalCommand.ENSURE_SESSION, side_effect=SideEffectKind.CREATED),
            ScenarioStep(on=LogicalCommand.SUBMIT_TURN, side_effect=SideEffectKind.ACCEPTED),
            ScenarioStep(on=LogicalCommand.OBSERVE_SNAPSHOT, snapshot={"sessionState": "idle", "turnState": "completed"}),
        ),
        name="clean",
    )
    return run_scenario(scenario)


def _keys(result: RunResult) -> set[str]:
    return {v.invariant for v in check_invariants(result)}


def test_baseline_is_clean() -> None:
    assert _keys(_clean_result()) == set()


def test_at_most_once_submission_detected() -> None:
    result = _clean_result()
    result.recorder.side_effects.append(
        RecordedSideEffect(
            sequence=999,
            command=LogicalCommand.SUBMIT_TURN,
            kind=SideEffectKind.ACCEPTED,
            idempotency_key="turn:1",
            generation=1,
        )
    )
    assert "at_most_once_submission" in _keys(result)


def test_eventual_convergence_detected() -> None:
    result = _clean_result()
    result.reference_view.turn_state = TurnState.COMPLETED
    result.reconciler.view.turn_state = TurnState.NONE
    assert "eventual_convergence" in _keys(result)


def test_monotonic_authority_detected() -> None:
    result = _clean_result()
    result.reconciler.revision_history = [(0, 0), (2, 1), (1, 1)]
    assert "monotonic_authority" in _keys(result)


def test_fencing_safety_detected() -> None:
    result = _clean_result()
    recorder = ProviderRecorder()
    recorder.side_effects = [
        RecordedSideEffect(1, LogicalCommand.HOST_REPLACE, SideEffectKind.REPLACED, None, 1),
        RecordedSideEffect(2, LogicalCommand.SUBMIT_TURN, SideEffectKind.ACCEPTED, "turn:stale", 1),
    ]
    result.recorder = recorder
    assert "fencing_safety" in _keys(result)


def test_no_blind_ambiguity_retry_detected() -> None:
    result = _clean_result()
    result.reconciler.blind_resubmissions["turn:1"] = 2
    assert "no_blind_ambiguity_retry" in _keys(result)


def test_distinct_terminality_detected() -> None:
    result = _clean_result()
    result.reconciler.view.session_state = SessionState.TERMINAL
    result.reconciler.view.turn_state = TurnState.RUNNING
    assert "distinct_terminality" in _keys(result)


def test_lease_safety_detected() -> None:
    result = _clean_result()
    result.reconciler.lease_consumers = 1
    result.reconciler.view.lease_held = False
    assert "lease_safety" in _keys(result)


def test_cleanup_safety_detected() -> None:
    scenario = Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=1,
        steps=(ScenarioStep(on=LogicalCommand.CLEANUP, generation=5),),
        name="cleanup-bad",
    )
    reconciler = FaultKitReconciler(generation=1)
    reconciler.journal = [
        JournalEntry(step_index=0, command=LogicalCommand.CLEANUP, decision=Decision.PROCEED)
    ]
    result = RunResult(
        scenario=scenario,
        recorder=ProviderRecorder(),
        reconciler=reconciler,
        reference_view=SessionView(),
    )
    assert "cleanup_safety" in _keys(result)


def test_historical_read_safety_detected() -> None:
    result = _clean_result()
    result.reconciler.view.turn_state = TurnState.COMPLETED
    result.reconciler.view.terminal_evidence_retained = False
    result.recorder.side_effects.append(
        RecordedSideEffect(998, LogicalCommand.DELETE_SESSION, SideEffectKind.DELETED, None, 1)
    )
    assert "historical_read_safety" in _keys(result)


def test_compatibility_safety_detected() -> None:
    scenario = Scenario(
        schema_version="future/v9",
        seed=1,
        steps=(),
        name="quarantined",
        quarantined=True,
    )
    result = RunResult(
        scenario=scenario,
        recorder=ProviderRecorder(),
        reconciler=FaultKitReconciler(),
        reference_view=SessionView(),
    )
    assert "compatibility_safety" in _keys(result)


def test_secret_safety_detected() -> None:
    scenario = Scenario(
        schema_version=CANONICAL_SCENARIO_SCHEMA_VERSION,
        seed=1,
        steps=(ScenarioStep(on=LogicalCommand.ENSURE_SESSION),),
        name="leaky",
        metadata={"note": "token=ghp_" + "a" * 30},
    )
    result = RunResult(
        scenario=scenario,
        recorder=ProviderRecorder(),
        reconciler=FaultKitReconciler(),
        reference_view=SessionView(),
    )
    assert "secret_safety" in _keys(result)


def test_deterministic_replay_detected() -> None:
    result = _clean_result()
    # Corrupt the recorded journal so a fresh replay no longer matches.
    result.reconciler.journal.append(
        JournalEntry(step_index=99, command=LogicalCommand.RECONCILE, decision=Decision.PROCEED)
    )
    assert "deterministic_replay" in _keys(result)
