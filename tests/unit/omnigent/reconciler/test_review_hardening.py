"""Regression coverage for the reconciler review-hardening fixes.

Each test pins a correctness fix requested in review of
MoonLadderStudios/MoonMind#3731 (source issue #3702): identity correlation,
attempt-identity fail-closed, terminal-outcome propagation, observed-negative
lease release, cleanup-gated closure, ambiguous-submission recovery, timeout
classification, intervention status modeling, and bounded shadow comparison.
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DecisionKind,
    EventFrontierObservation,
    LeaseObservation,
    LeaseState,
    ObservationSet,
    ProviderSessionObservation,
    ProviderStatusClass,
    ProviderTurnObservation,
    ReasonCode,
    SubmissionState,
    TerminalOutcome,
    shadow_compare,
)


# -- Identity correlation ---------------------------------------------------


def test_intent_durable_session_mismatch_quarantines(make_intent, make_durable, run):
    intent = make_intent(session_id="session-A")
    durable = make_durable(session_id="session-B")
    decision = run(intent, durable)
    assert decision.kind == DecisionKind.QUARANTINE_AMBIGUOUS_STATE
    assert decision.reason_code == ReasonCode.SESSION_IDENTITY_MISMATCH


def test_mismatched_provider_session_snapshot_awaits_correlated_evidence(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable(provider_session_id="provider-session-1")
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now,
            raw_status="completed",
            provider_session_id="a-different-session",
        ),
        event_frontier=EventFrontierObservation(observed_at=now, terminal_event_seen=True),
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.AWAITING_CORRELATED_TERMINAL_EVIDENCE


def test_stale_turn_from_previous_attempt_does_not_synthesize(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable(attempt_id="attempt-2")
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="idle"),
        provider_turn=ProviderTurnObservation(
            observed_at=now,
            attempt_id="attempt-1",  # a delayed transcript from the prior turn
            turn_complete=True,
            outcome=TerminalOutcome.SUCCESS,
        ),
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.IDLE_PENDING_TURN_EVIDENCE


# -- Attempt-identity fail-closed ------------------------------------------


def test_submission_without_attempt_identity_fails_closed(
    make_intent, make_durable, run
):
    intent = make_intent()
    durable = make_durable(
        profile_lease=LeaseState.HELD,
        host_lease=LeaseState.HELD,
        provider_session_attached=True,
        provider_session_id="ps",
        attempt_id=None,  # no durable attempt identity
        submission=SubmissionState.NOT_SUBMITTED,
    )
    decision = run(intent, durable)
    assert decision.kind == DecisionKind.QUARANTINE_AMBIGUOUS_STATE
    assert decision.reason_code == ReasonCode.MISSING_ATTEMPT_IDENTITY
    assert decision.command is None


# -- Terminal-outcome propagation ------------------------------------------


def test_recorded_terminal_command_carries_observed_outcome(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="failed"),
        event_frontier=EventFrontierObservation(observed_at=now, terminal_event_seen=True),
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.RECORD_PROVIDER_TERMINAL
    assert decision.command is not None
    assert decision.command.terminal_outcome == TerminalOutcome.FAILURE


def test_synthesized_terminal_command_carries_turn_outcome(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(observed_at=now, raw_status="idle"),
        provider_turn=ProviderTurnObservation(
            observed_at=now, turn_complete=True, outcome=TerminalOutcome.SUCCESS
        ),
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT
    assert decision.command.terminal_outcome == TerminalOutcome.SUCCESS


def test_cancellation_terminal_command_carries_cancelled_outcome(
    make_intent, make_ready_durable, run
):
    from moonmind.omnigent.reconciler import DesiredLifecycle

    intent = make_intent()
    durable = make_ready_durable(desired=DesiredLifecycle.CANCEL)
    decision = run(intent, durable)
    assert decision.kind == DecisionKind.RECORD_PROVIDER_TERMINAL
    assert decision.command.terminal_outcome == TerminalOutcome.CANCELLED


# -- Exhausted attempts finalize (not a settled dead end) -------------------


def test_exhausted_attempts_record_failure_terminal(make_intent, make_durable, run):
    intent = make_intent(max_turn_attempts=1)
    durable = make_durable(
        profile_lease=LeaseState.HELD,
        host_lease=LeaseState.HELD,
        provider_session_attached=True,
        provider_session_id="ps",
        attempt_id="a1",
        submission=SubmissionState.NOT_SUBMITTED,
        turn_attempts=1,
    )
    decision = run(intent, durable)
    assert decision.kind == DecisionKind.RECORD_PROVIDER_TERMINAL
    assert decision.reason_code == ReasonCode.MAX_TURN_ATTEMPTS_EXHAUSTED
    assert decision.command.terminal_outcome == TerminalOutcome.FAILURE


# -- Observed-negative lease release ---------------------------------------


def test_release_requires_observed_consumer_negative(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        terminal_evidence_ref="evref-1",
        evidence_harvested=True,
        cleanup_started=True,
        cleanup_complete=True,
    )
    # No lease observation -> not observed -> must wait, never release.
    waiting = run(intent, durable, ObservationSet())
    assert waiting.kind == DecisionKind.AWAIT_OBSERVATION
    assert waiting.reason_code == ReasonCode.AWAITING_LEASE_CONSUMER_CONFIRMATION

    # Fresh observed-negatives for every held lease -> release allowed.
    confirmed = run(
        intent,
        durable,
        ObservationSet(
            profile_lease=LeaseObservation(observed_at=now, held=True, consumer_active=False),
            host_lease=LeaseObservation(observed_at=now, held=True, consumer_active=False),
        ),
    )
    assert confirmed.kind == DecisionKind.RELEASE_LEASES


# -- Cleanup gates closure independently of leases (invariant 9) ------------


def test_cleanup_incomplete_blocks_close_after_leases_released(
    make_intent, make_ready_durable, run
):
    intent = make_intent()
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        terminal_evidence_ref="evref-1",
        evidence_harvested=True,
        cleanup_started=True,
        cleanup_complete=False,  # cleanup still unfinished
        profile_lease=LeaseState.RELEASED,
        host_lease=LeaseState.RELEASED,
    )
    decision = run(intent, durable)
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.CLEANUP_INCOMPLETE_BEFORE_CLOSE


# -- Durable evidence reference required before cleanup ---------------------


def test_harvest_flag_without_ref_still_requires_evidence(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    # evidence_harvested True but no durable terminal_evidence_ref yet.
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        evidence_harvested=True,
        terminal_evidence_ref=None,
    )
    decision = run(
        intent,
        durable,
        ObservationSet(),
    )
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.AWAITING_EVIDENCE


# -- Ambiguous submission recovery -----------------------------------------


def test_in_flight_submission_recovers_from_terminal_observation(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent(max_turn_attempts=2)
    durable = make_ready_durable(
        submission=SubmissionState.IN_FLIGHT,
        provider_session_id="provider-session-1",
    )
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now,
            raw_status="completed",
            provider_session_id="provider-session-1",
        ),
        event_frontier=EventFrontierObservation(observed_at=now, terminal_event_seen=True),
    )
    decision = run(intent, durable, observations)
    # The lost submit response is recovered: the correlated terminal is recorded
    # rather than waiting forever, and the submit is never reissued.
    assert decision.kind == DecisionKind.RECORD_PROVIDER_TERMINAL
    assert decision.command.command_kind != DecisionKind.SUBMIT_TURN


def test_in_flight_without_observation_still_waits(
    make_intent, make_ready_durable, run
):
    intent = make_intent()
    durable = make_ready_durable(submission=SubmissionState.IN_FLIGHT)
    decision = run(intent, durable, ObservationSet())
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.SUBMISSION_DELIVERY_AMBIGUOUS
    assert decision.command is None


# -- Intervention status modeling ------------------------------------------


def test_intervention_status_is_actionable_product_state(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable()
    decision = run(
        intent,
        durable,
        ObservationSet(
            provider_session=ProviderSessionObservation(
                observed_at=now, raw_status="awaiting_approval"
            )
        ),
    )
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.PROVIDER_INTERVENTION_REQUIRED
    assert decision.changes_product_visible_state is True
    assert (
        decision.diagnostics.provider_status_class == ProviderStatusClass.INTERVENTION
    )


# -- Bounded shadow comparison ----------------------------------------------


def test_shadow_compare_does_not_echo_unknown_action(make_intent, make_durable, run):
    intent = make_intent()
    decision = run(intent, make_durable())
    sensitive = "leak session-secret token=abc " * 40
    comparison = shadow_compare(sensitive, decision)
    assert comparison.agreement is False
    assert comparison.divergence_reason == "unknown_legacy_action"
    # The raw, oversized/sensitive action string is never retained.
    assert comparison.legacy_action == "unknown"
    assert "session-secret" not in comparison.model_dump_json()


def test_legacy_action_table_has_one_action_per_decision():
    from moonmind.omnigent.reconciler.reducer import LEGACY_ACTION_TO_DECISION_KIND

    kinds = list(LEGACY_ACTION_TO_DECISION_KIND.values())
    # One canonical legacy action per decision kind (no maintained aliases).
    assert len(kinds) == len(set(kinds))
