"""Escaped-failure replay coverage for the pure reconciler.

Each test reproduces a real escaped failure as a generalized invariant behind
the reconciler rather than as a bespoke local branch.

Source issue: MoonLadderStudios/MoonMind#3702 (related: #3698, #3683).
"""

from __future__ import annotations

from moonmind.omnigent.reconciler import (
    DecisionKind,
    EventFrontierObservation,
    EvidenceObservation,
    LeaseObservation,
    ObservationSet,
    ProviderSessionObservation,
    ProviderTurnObservation,
    ReasonCode,
    SubmissionState,
    TerminalOutcome,
)


def test_missed_terminal_event_recovered_from_snapshot(
    make_intent, make_ready_durable, run, now
):
    """#3698: the terminal SSE edge was missed; snapshot evidence recovers it."""

    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now, raw_status="completed"
        ),
        # The terminal event was never observed on the frontier.
        event_frontier=EventFrontierObservation(
            observed_at=now, terminal_event_seen=False
        ),
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT
    assert decision.reason_code == ReasonCode.TERMINAL_SNAPSHOT_SYNTHESIS
    assert decision.changes_product_visible_state is True


def test_provider_idle_after_completed_work_is_terminal(
    make_intent, make_ready_durable, run, now
):
    """#3683: provider returns to idle after finishing; transcript proves done."""

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
    assert decision.reason_code == ReasonCode.TERMINAL_IDLE_SYNTHESIS


def test_idle_with_open_tool_call_is_not_terminal(
    make_intent, make_ready_durable, run, now
):
    """#3683 boundary: idle alone is not terminal while a tool call is open."""

    intent = make_intent()
    durable = make_ready_durable()
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now, raw_status="idle", open_tool_call=True
        ),
        provider_turn=ProviderTurnObservation(observed_at=now, turn_complete=False),
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.IDLE_WITH_OPEN_TOOL_CALL


def test_retry_after_durable_side_effect_does_not_reissue_submit(
    make_intent, make_ready_durable, run, now
):
    """Retry after a durable submit side effect, before the response is recorded.

    Submission is in flight (delivery ambiguous); the reducer must wait, not
    reissue the turn (at-most-once submission, invariant 7).
    """

    intent = make_intent()
    durable = make_ready_durable(submission=SubmissionState.IN_FLIGHT)
    decision = run(intent, durable, ObservationSet())
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.SUBMISSION_DELIVERY_AMBIGUOUS
    assert decision.command is None


def test_late_running_event_after_terminal_does_not_move_backward(
    make_intent, make_ready_durable, run, now
):
    """A late running observation after terminal is ignored (invariant 5)."""

    intent = make_intent()
    durable = make_ready_durable(terminal_outcome=TerminalOutcome.SUCCESS)
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now, raw_status="running"
        )
    )
    decision = run(intent, durable, observations)
    # It progresses the post-terminal chain (await evidence), never re-runs the turn.
    assert decision.kind == DecisionKind.AWAIT_OBSERVATION
    assert decision.reason_code == ReasonCode.IGNORED_STALE_RUNNING_AFTER_TERMINAL
    assert decision.command is None


def test_provider_terminal_while_cleanup_and_release_incomplete(
    make_intent, make_ready_durable, run, now
):
    """Terminal observed while cleanup and lease release remain incomplete.

    Evidence is harvested and cleanup begun before any lease can be released
    (invariants 8 and 9).
    """

    intent = make_intent()

    # Terminal recorded, evidence not yet harvested -> harvest first.
    durable = make_ready_durable(terminal_outcome=TerminalOutcome.SUCCESS)
    harvest = run(
        intent,
        durable,
        ObservationSet(
            evidence=EvidenceObservation(
                observed_at=now, terminal_evidence_available=True
            )
        ),
    )
    assert harvest.kind == DecisionKind.HARVEST_EVIDENCE

    # Harvested (with a durable evidence ref), cleanup not started -> begin
    # cleanup, still no lease release.
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        evidence_harvested=True,
        terminal_evidence_ref="evref-1",
    )
    cleanup = run(intent, durable, ObservationSet())
    assert cleanup.kind == DecisionKind.BEGIN_CLEANUP

    # Cleanup started but incomplete -> leases must not be released yet.
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        evidence_harvested=True,
        terminal_evidence_ref="evref-1",
        cleanup_started=True,
        cleanup_complete=False,
    )
    blocked = run(intent, durable, ObservationSet())
    assert blocked.kind == DecisionKind.AWAIT_OBSERVATION
    assert blocked.reason_code == ReasonCode.CLEANUP_INCOMPLETE_BEFORE_RELEASE

    # Cleanup complete but a consumer is still observed -> still no release.
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS,
        evidence_harvested=True,
        terminal_evidence_ref="evref-1",
        cleanup_started=True,
        cleanup_complete=True,
    )
    consumer_active = run(
        intent,
        durable,
        ObservationSet(
            profile_lease=LeaseObservation(
                observed_at=now, held=True, consumer_active=True
            )
        ),
    )
    assert consumer_active.kind == DecisionKind.AWAIT_OBSERVATION
    assert consumer_active.reason_code == ReasonCode.LEASE_CONSUMERS_ACTIVE

    # Cleanup complete, no consumer *observation* at all -> still no release,
    # because absent observations are "not observed", not an observed negative.
    no_confirmation = run(intent, durable, ObservationSet())
    assert no_confirmation.kind == DecisionKind.AWAIT_OBSERVATION
    assert (
        no_confirmation.reason_code
        == ReasonCode.AWAITING_LEASE_CONSUMER_CONFIRMATION
    )

    # Cleanup complete, both leases confirmed consumer-free -> release allowed.
    release = run(
        intent,
        durable,
        ObservationSet(
            profile_lease=LeaseObservation(
                observed_at=now, held=True, consumer_active=False
            ),
            host_lease=LeaseObservation(
                observed_at=now, held=True, consumer_active=False
            ),
        ),
    )
    assert release.kind == DecisionKind.RELEASE_LEASES

    # Releasing/closing never erases the recorded terminal outcome (invariant 9).
    assert durable.terminal_outcome == TerminalOutcome.SUCCESS


def test_contradictory_terminal_outcome_quarantines(
    make_intent, make_ready_durable, run, now
):
    intent = make_intent()
    durable = make_ready_durable(terminal_outcome=TerminalOutcome.SUCCESS)
    observations = ObservationSet(
        provider_session=ProviderSessionObservation(
            observed_at=now, raw_status="failed"
        )
    )
    decision = run(intent, durable, observations)
    assert decision.kind == DecisionKind.QUARANTINE_AMBIGUOUS_STATE
    assert decision.reason_code == ReasonCode.CONTRADICTORY_TERMINAL_OUTCOME
