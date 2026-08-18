"""Explicit coverage for the twelve required reconciler invariants.

Source issue: MoonLadderStudios/MoonMind#3702 (Required invariants 1-12).
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DecisionKind,
    EventFrontierObservation,
    EvidenceObservation,
    HostObservation,
    LeaseObservation,
    LeaseState,
    ObservationSet,
    ProviderSessionObservation,
    ProviderTurnObservation,
    ReasonCode,
    SETTLED_DECISION_KINDS,
    SubmissionState,
    TerminalOutcome,
)


def test_invariant_1_provider_event_is_observation_not_mutation(
    make_intent, make_ready_durable, run, now
):
    # A running provider observation does not by itself terminalize; it only asks
    # to wait. The reducer never asserts durable state changed.
    intent = make_intent()
    durable = make_ready_durable()
    decision = run(
        intent,
        durable,
        ObservationSet(
            provider_session=ProviderSessionObservation(observed_at=now, raw_status="running")
        ),
    )
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert durable.terminal_outcome is None


def test_invariant_2_lost_terminal_recovered_from_snapshot(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    decision = run(
        intent,
        make_ready_durable(),
        ObservationSet(
            provider_session=ProviderSessionObservation(observed_at=now, raw_status="completed"),
            event_frontier=EventFrontierObservation(observed_at=now, terminal_event_seen=False),
        ),
    )
    assert decision.kind == DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT


def test_invariant_3_idle_with_open_tool_is_not_terminal(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    decision = run(
        intent,
        make_ready_durable(),
        ObservationSet(
            provider_session=ProviderSessionObservation(
                observed_at=now, raw_status="idle", open_tool_call=True
            )
        ),
    )
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.IDLE_WITH_OPEN_TOOL_CALL


def test_invariant_4_attempt_terminality_distinct_from_session(
    make_intent, make_ready_durable, run, now
):
    # A completed turn transcript for the current attempt drives a terminal
    # *recording* decision; it does not by itself mark the durable session
    # terminal (that is the executor's job once the decision is applied).
    intent = make_intent()
    durable = make_ready_durable()
    decision = run(
        intent,
        durable,
        ObservationSet(
            provider_session=ProviderSessionObservation(observed_at=now, raw_status="idle"),
            provider_turn=ProviderTurnObservation(
                observed_at=now, turn_complete=True, outcome=TerminalOutcome.SUCCESS
            ),
        ),
    )
    assert decision.kind == DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT
    assert durable.terminal_outcome is None  # session not yet terminal


def test_invariant_5_stale_observation_cannot_move_terminal_backward(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable(terminal_outcome=TerminalOutcome.SUCCESS)
    decision = run(
        intent,
        durable,
        ObservationSet(
            provider_session=ProviderSessionObservation(observed_at=now, raw_status="running")
        ),
    )
    assert decision.reason_code == ReasonCode.IGNORED_STALE_RUNNING_AFTER_TERMINAL
    assert decision.kind not in {
        DecisionKind.SUBMIT_TURN,
        DecisionKind.ENSURE_PROVIDER_SESSION,
    }


def test_invariant_6_unknown_vocabulary_fails_closed(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    decision = run(
        intent,
        make_ready_durable(),
        ObservationSet(
            provider_session=ProviderSessionObservation(
                observed_at=now, raw_status="a_status_we_have_never_seen"
            )
        ),
    )
    # Never mapped to success; fails closed to observation.
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.UNKNOWN_PROVIDER_STATUS


def test_invariant_7_no_duplicate_command_when_in_flight(
    make_intent, make_ready_durable, run
):
    intent = make_intent()
    decision = run(intent, make_ready_durable(submission=SubmissionState.IN_FLIGHT))
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.command is None


def test_invariant_8_no_release_while_consumer_observed(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        evidence_harvested=True,
        cleanup_started=True,
        cleanup_complete=True,
    )
    decision = run(
        intent,
        durable,
        ObservationSet(host=HostObservation(observed_at=now, registered=True, runner_ready=True)),
    )
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.LEASE_CONSUMERS_ACTIVE


def test_invariant_9_cleanup_distinct_from_completion(
    make_intent, make_ready_durable, run
):
    intent = make_intent()
    # Terminal recorded but cleanup not complete: session task is done, cleanup
    # is not; release is gated and the terminal outcome is preserved.
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        evidence_harvested=True,
        cleanup_started=True,
        cleanup_complete=False,
    )
    decision = run(intent, durable)
    assert decision.reason_code == ReasonCode.CLEANUP_INCOMPLETE_BEFORE_RELEASE
    assert durable.terminal_outcome == TerminalOutcome.SUCCESS


def test_invariant_10_nonterminal_states_have_bounded_deadline(
    make_intent, make_durable, make_ready_durable, run, now
):
    intent = make_intent()
    # Sample a variety of nonterminal decisions.
    samples = [
        run(intent, make_durable()),  # ensure_profile_lease
        run(intent, make_ready_durable()),  # await provider snapshot
        run(
            intent,
            make_ready_durable(),
            ObservationSet(
                provider_session=ProviderSessionObservation(observed_at=now, raw_status="running")
            ),
        ),
    ]
    for decision in samples:
        assert decision.kind not in SETTLED_DECISION_KINDS
        assert decision.next_deadline is not None
        assert decision.next_deadline > now


def test_invariant_11_no_trust_of_caller_supplied_identity(
    make_intent, make_durable, run, now
):
    intent = make_intent(provider="attacker/provider")
    durable = make_durable(profile_lease=LeaseState.HELD, host_lease=LeaseState.HELD)
    decision = run(
        intent,
        durable,
        ObservationSet(
            provider_session=ProviderSessionObservation(
                observed_at=now, raw_status="running", provider_session_id="spoofed"
            )
        ),
    )
    assert decision.expected_revision == durable.revision
    assert decision.expected_fencing_generation == durable.fencing_generation
    if decision.command is not None:
        # Authority identity is durable, not from the intent/observation.
        assert decision.command.provider_session_id == durable.provider_session_id


def test_invariant_12_deterministic_for_equal_inputs(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="completed"),
        event_frontier=EventFrontierObservation(observed_at=now, terminal_event_seen=True),
    )
    first = run(intent, durable, observations)
    second = run(intent, durable, observations)
    assert first == second
    assert first.model_dump_json() == second.model_dump_json()
