"""Table-driven transition coverage for the pure reconciler.

Covers every legal state and decision, including impossible/contradictory
combinations, per MoonLadderStudios/MoonMind#3702.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from moonmind.omnigent.reconciler import (
    CompatibilityObservation,
    COMMAND_DECISION_KINDS,
    DecisionKind,
    DesiredLifecycle,
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


def _scenarios(now, make_durable, make_ready_durable):
    """Return (name, durable, observations, expected_kind, expected_reason)."""

    ps = lambda **kw: ProviderSessionObservation(observed_at=now, **kw)  # noqa: E731
    pt = lambda **kw: ProviderTurnObservation(observed_at=now, **kw)  # noqa: E731
    ef = lambda **kw: EventFrontierObservation(observed_at=now, **kw)  # noqa: E731
    ev = lambda **kw: EvidenceObservation(observed_at=now, **kw)  # noqa: E731
    lease = lambda **kw: LeaseObservation(observed_at=now, **kw)  # noqa: E731

    terminal = lambda **kw: make_ready_durable(  # noqa: E731
        terminal_outcome=TerminalOutcome.SUCCESS, **kw
    )

    return [
        # --- forward provisioning ---
        (
            "fresh_needs_profile_lease",
            make_durable(),
            ObservationSet(),
            DecisionKind.ENSURE_PROFILE_LEASE,
            ReasonCode.PROFILE_LEASE_REQUIRED,
        ),
        (
            "profile_held_needs_host",
            make_durable(profile_lease=LeaseState.HELD),
            ObservationSet(),
            DecisionKind.ENSURE_HOST,
            ReasonCode.HOST_REQUIRED,
        ),
        (
            "leases_held_needs_session",
            make_durable(
                profile_lease=LeaseState.HELD, host_lease=LeaseState.HELD
            ),
            ObservationSet(),
            DecisionKind.ENSURE_PROVIDER_SESSION,
            ReasonCode.PROVIDER_SESSION_REQUIRED,
        ),
        (
            "session_attached_needs_submit",
            make_durable(
                profile_lease=LeaseState.HELD,
                host_lease=LeaseState.HELD,
                provider_session_attached=True,
                provider_session_id="ps",
                attempt_id="a1",
            ),
            ObservationSet(),
            DecisionKind.SUBMIT_TURN,
            ReasonCode.TURN_SUBMISSION_REQUIRED,
        ),
        (
            "submission_in_flight_awaits",
            make_ready_durable(submission=SubmissionState.IN_FLIGHT),
            ObservationSet(),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.SUBMISSION_DELIVERY_AMBIGUOUS,
        ),
        (
            "accepted_no_snapshot_awaits",
            make_ready_durable(),
            ObservationSet(),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.AWAITING_PROVIDER_SNAPSHOT,
        ),
        (
            "accepted_running_awaits",
            make_ready_durable(),
            ObservationSet(provider_session=ps(raw_status="running")),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.PROVIDER_RUNNING,
        ),
        (
            "accepted_unknown_status_awaits",
            make_ready_durable(),
            ObservationSet(provider_session=ps(raw_status="brand_new_status")),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.UNKNOWN_PROVIDER_STATUS,
        ),
        (
            "accepted_idle_open_tool_awaits",
            make_ready_durable(),
            ObservationSet(
                provider_session=ps(raw_status="idle", open_tool_call=True)
            ),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.IDLE_WITH_OPEN_TOOL_CALL,
        ),
        (
            "accepted_idle_no_turn_evidence_awaits",
            make_ready_durable(),
            ObservationSet(provider_session=ps(raw_status="idle")),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.IDLE_PENDING_TURN_EVIDENCE,
        ),
        (
            "accepted_idle_turn_complete_synthesizes",
            make_ready_durable(),
            ObservationSet(
                provider_session=ps(raw_status="idle"),
                provider_turn=pt(turn_complete=True, outcome=TerminalOutcome.SUCCESS),
            ),
            DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
            ReasonCode.TERMINAL_IDLE_SYNTHESIS,
        ),
        (
            "accepted_completed_with_event_records",
            make_ready_durable(),
            ObservationSet(
                provider_session=ps(raw_status="completed"),
                event_frontier=ef(terminal_event_seen=True),
            ),
            DecisionKind.RECORD_PROVIDER_TERMINAL,
            ReasonCode.TERMINAL_EVENT_OBSERVED,
        ),
        (
            "accepted_completed_missed_event_synthesizes",
            make_ready_durable(),
            ObservationSet(
                provider_session=ps(raw_status="completed"),
                event_frontier=ef(terminal_event_seen=False),
            ),
            DecisionKind.SYNTHESIZE_TERMINAL_FROM_SNAPSHOT,
            ReasonCode.TERMINAL_SNAPSHOT_SYNTHESIS,
        ),
        (
            "accepted_provider_session_missing_quarantines",
            make_ready_durable(),
            ObservationSet(provider_session=ps(present=False, raw_status="idle")),
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            ReasonCode.PROVIDER_SESSION_MISSING,
        ),
        # --- desired cancellation ---
        (
            "desired_cancel_records_terminal",
            make_ready_durable(desired=DesiredLifecycle.CANCEL),
            ObservationSet(),
            DecisionKind.RECORD_PROVIDER_TERMINAL,
            ReasonCode.DESIRED_CANCELLATION,
        ),
        # --- sticky meta terminals ---
        (
            "failed_is_sticky",
            make_ready_durable(failed=True),
            ObservationSet(provider_session=ps(raw_status="running")),
            DecisionKind.FAIL_NONRETRYABLE,
            ReasonCode.SESSION_FAILED,
        ),
        (
            "quarantined_is_sticky",
            make_ready_durable(quarantined=True),
            ObservationSet(),
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            ReasonCode.SESSION_QUARANTINED,
        ),
        (
            "max_attempts_exhausted_fails",
            make_durable(
                profile_lease=LeaseState.HELD,
                host_lease=LeaseState.HELD,
                provider_session_attached=True,
                provider_session_id="ps",
                attempt_id="a1",
                submission=SubmissionState.NOT_SUBMITTED,
                turn_attempts=1,
            ),
            ObservationSet(),
            DecisionKind.FAIL_NONRETRYABLE,
            ReasonCode.MAX_TURN_ATTEMPTS_EXHAUSTED,
        ),
        # --- compatibility gate ---
        (
            "unknown_compat_version_quarantines",
            make_ready_durable(),
            ObservationSet(
                compatibility=CompatibilityObservation(
                    observed_at=now, compatibility_version="v9", runtime_ready=True
                )
            ),
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            ReasonCode.UNKNOWN_COMPATIBILITY_VERSION,
        ),
        (
            "runtime_not_ready_awaits",
            make_ready_durable(),
            ObservationSet(
                compatibility=CompatibilityObservation(
                    observed_at=now, compatibility_version="v1", runtime_ready=False
                )
            ),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.RUNTIME_NOT_READY,
        ),
        # --- post terminal chain ---
        (
            "terminal_no_evidence_awaits",
            terminal(),
            ObservationSet(),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.AWAITING_EVIDENCE,
        ),
        (
            "terminal_evidence_unavailable_retries",
            terminal(),
            ObservationSet(evidence=ev(terminal_evidence_available=False)),
            DecisionKind.RETRY_TRANSIENT_OBSERVATION,
            ReasonCode.EVIDENCE_NOT_YET_AVAILABLE,
        ),
        (
            "terminal_evidence_available_harvests",
            terminal(),
            ObservationSet(evidence=ev(terminal_evidence_available=True)),
            DecisionKind.HARVEST_EVIDENCE,
            ReasonCode.EVIDENCE_HARVEST_REQUIRED,
        ),
        (
            "terminal_harvested_begins_cleanup",
            terminal(evidence_harvested=True),
            ObservationSet(),
            DecisionKind.BEGIN_CLEANUP,
            ReasonCode.CLEANUP_REQUIRED,
        ),
        (
            "terminal_cleanup_incomplete_before_release",
            terminal(evidence_harvested=True, cleanup_started=True),
            ObservationSet(),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.CLEANUP_INCOMPLETE_BEFORE_RELEASE,
        ),
        (
            "terminal_consumer_active_blocks_release",
            terminal(
                evidence_harvested=True,
                cleanup_started=True,
                cleanup_complete=True,
            ),
            ObservationSet(host_lease=lease(held=True, consumer_active=True)),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.LEASE_CONSUMERS_ACTIVE,
        ),
        (
            "terminal_ready_releases_leases",
            terminal(
                evidence_harvested=True,
                cleanup_started=True,
                cleanup_complete=True,
            ),
            ObservationSet(),
            DecisionKind.RELEASE_LEASES,
            ReasonCode.LEASE_RELEASE_REQUIRED,
        ),
        (
            "terminal_fully_settled_no_op",
            terminal(
                evidence_harvested=True,
                cleanup_started=True,
                cleanup_complete=True,
                profile_lease=LeaseState.RELEASED,
                host_lease=LeaseState.RELEASED,
            ),
            ObservationSet(),
            DecisionKind.NO_OP,
            ReasonCode.SESSION_CLOSED,
        ),
        (
            "terminal_late_running_ignored_not_backward",
            terminal(),
            ObservationSet(provider_session=ps(raw_status="running")),
            DecisionKind.AWAIT_OBSERVATION,
            ReasonCode.IGNORED_STALE_RUNNING_AFTER_TERMINAL,
        ),
        (
            "terminal_contradictory_outcome_quarantines",
            terminal(),
            ObservationSet(provider_session=ps(raw_status="failed")),
            DecisionKind.QUARANTINE_AMBIGUOUS_STATE,
            ReasonCode.CONTRADICTORY_TERMINAL_OUTCOME,
        ),
    ]


def test_transition_table(now, make_intent, make_durable, make_ready_durable, run):
    intent = make_intent()
    scenarios = _scenarios(now, make_durable, make_ready_durable)
    seen_kinds = set()
    for name, durable, observations, expected_kind, expected_reason in scenarios:
        decision = run(intent, durable, observations)
        assert decision.kind == expected_kind, name
        assert decision.reason_code == expected_reason, name
        seen_kinds.add(decision.kind)

    # Every decision kind in the closed vocabulary is exercised by the table.
    assert seen_kinds == set(DecisionKind)


def test_every_decision_carries_durable_authority(
    now, make_intent, make_durable, make_ready_durable, run
):
    """expected revision/fencing always come from durable state (invariant 11)."""

    intent = make_intent()
    for name, durable, observations, _kind, _reason in _scenarios(
        now, make_durable, make_ready_durable
    ):
        decision = run(intent, durable, observations)
        assert decision.expected_revision == durable.revision, name
        assert (
            decision.expected_fencing_generation == durable.fencing_generation
        ), name


def test_command_presence_and_deadline_rules(
    now, make_intent, make_durable, make_ready_durable, run
):
    intent = make_intent()
    for name, durable, observations, _kind, _reason in _scenarios(
        now, make_durable, make_ready_durable
    ):
        decision = run(intent, durable, observations)
        if decision.kind in COMMAND_DECISION_KINDS:
            assert decision.command is not None, name
            assert decision.command.command_kind == decision.kind, name
            assert decision.command.command_id, name
        else:
            assert decision.command is None, name

        if decision.kind in SETTLED_DECISION_KINDS:
            assert decision.next_deadline is None, name
        else:
            # Every nonterminal decision carries a bounded deadline (invariant 10).
            assert decision.next_deadline == now + timedelta(
                seconds=intent.reconcile_interval_seconds
            ), name


@pytest.mark.parametrize(
    "requires_profile_lease, requires_host, expected",
    [
        (False, True, DecisionKind.ENSURE_HOST),
        (True, False, DecisionKind.ENSURE_PROFILE_LEASE),
        (False, False, DecisionKind.ENSURE_PROVIDER_SESSION),
    ],
)
def test_optional_provisioning_requirements(
    requires_profile_lease, requires_host, expected, make_intent, make_durable, run
):
    intent = make_intent(
        requires_profile_lease=requires_profile_lease, requires_host=requires_host
    )
    decision = run(intent, make_durable())
    assert decision.kind == expected


def test_cleanup_not_required_skips_to_release(
    make_intent, make_ready_durable, run
):
    intent = make_intent(requires_cleanup=False)
    durable = make_ready_durable(
        terminal_outcome=TerminalOutcome.SUCCESS, evidence_harvested=True
    )
    decision = run(intent, durable)
    # With cleanup not required, the chain goes straight to lease release.
    assert decision.kind == DecisionKind.RELEASE_LEASES
