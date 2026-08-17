"""Unit tests for the pure Omnigent session reconciler (#3705)."""

from __future__ import annotations

import pytest

from moonmind.omnigent.session_reconciler import (
    OmnigentSessionAdmissionPolicy,
    OmnigentSessionCommandCondition,
    OmnigentSessionCommandKind,
    OmnigentSessionFrontier,
    OmnigentSessionIntent,
    OmnigentSessionReconcilePolicy,
    OmnigentSessionSignals,
    OmnigentSessionStatus,
    admit_omnigent_session_intent,
    reconcile_omnigent_session,
    should_continue_as_new,
)


def _intent(**overrides) -> OmnigentSessionIntent:
    base = dict(
        canonicalSessionId="wf-1:omnigent",
        executionIntentRef="artifact:intent",
        executionIntentDigest="digest",
        owningWorkflowId="user-wf-1",
        stepExecutionId="step-1",
        agentRunId="wf-1",
        executionProfileRef="profile:codex-oauth",
        initialTurnAttemptId="wf-1:omnigent:turn:1",
        admittedFeatureGeneration=1,
    )
    base.update(overrides)
    return OmnigentSessionIntent(**base)


def _established_frontier(**overrides) -> OmnigentSessionFrontier:
    base = dict(
        provider_profile_lease_held=True,
        host_ready=True,
        provider_session_established=True,
        current_turn_attempt_id="wf-1:omnigent:turn:1",
        turn_submitted=True,
    )
    base.update(overrides)
    return OmnigentSessionFrontier(**base)


def _kinds(decision) -> list[str]:
    return [command.kind for command in decision.commands]


def test_launch_sequence_is_ordered():
    intent = _intent()
    signals = OmnigentSessionSignals()

    lease = reconcile_omnigent_session(
        intent, OmnigentSessionFrontier(), signals, elapsed_seconds=0.0
    )
    assert lease.status is OmnigentSessionStatus.AWAITING_LEASE
    assert _kinds(lease) == [OmnigentSessionCommandKind.ENSURE_PROVIDER_PROFILE_LEASE]

    host = reconcile_omnigent_session(
        intent,
        OmnigentSessionFrontier(provider_profile_lease_held=True),
        signals,
        elapsed_seconds=0.0,
    )
    assert _kinds(host) == [OmnigentSessionCommandKind.ENSURE_HOST]

    session = reconcile_omnigent_session(
        intent,
        OmnigentSessionFrontier(provider_profile_lease_held=True, host_ready=True),
        signals,
        elapsed_seconds=0.0,
    )
    assert _kinds(session) == [OmnigentSessionCommandKind.ENSURE_PROVIDER_SESSION]

    submit = reconcile_omnigent_session(
        intent,
        OmnigentSessionFrontier(
            provider_profile_lease_held=True,
            host_ready=True,
            provider_session_established=True,
        ),
        signals,
        elapsed_seconds=0.0,
    )
    assert submit.status is OmnigentSessionStatus.EXECUTING
    assert _kinds(submit) == [OmnigentSessionCommandKind.SUBMIT_TURN]
    assert submit.commands[0].turn_attempt_id == intent.initial_turn_attempt_id


def test_command_idempotency_key_embeds_generation():
    intent = _intent()
    frontier = OmnigentSessionFrontier(fencing_generation=3)
    decision = reconcile_omnigent_session(
        intent, frontier, OmnigentSessionSignals(), elapsed_seconds=0.0
    )
    command = decision.commands[0]
    assert command.expected_generation == 3
    assert ":3:" in command.idempotency_key


def test_recent_event_read_then_periodic_snapshot():
    intent = _intent()
    frontier = _established_frontier()

    reading = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(seconds_since_last_observation=1.0),
        elapsed_seconds=5.0,
    )
    assert reading.status is OmnigentSessionStatus.AWAITING_OBSERVATION
    assert _kinds(reading) == [OmnigentSessionCommandKind.READ_EVENT_BATCH]

    snapshot = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(
            seconds_since_last_observation=intent.policy.snapshot_interval_seconds + 1
        ),
        elapsed_seconds=5.0,
    )
    assert _kinds(snapshot) == [OmnigentSessionCommandKind.OBSERVE_SNAPSHOT]
    assert "periodic_authoritative_snapshot" in snapshot.reason_codes


def test_terminal_observed_harvest_publish_cleanup_release_order():
    intent = _intent()
    signals = OmnigentSessionSignals()
    frontier = _established_frontier(terminal_observed=True, terminal_outcome="completed")

    harvest = reconcile_omnigent_session(intent, frontier, signals, elapsed_seconds=1.0)
    assert harvest.status is OmnigentSessionStatus.HARVESTING
    assert _kinds(harvest) == [OmnigentSessionCommandKind.HARVEST_EVIDENCE]

    frontier = frontier.model_copy(update={"evidence_harvested": True})
    publish = reconcile_omnigent_session(intent, frontier, signals, elapsed_seconds=1.0)
    assert _kinds(publish) == [OmnigentSessionCommandKind.PUBLISH_WORKSPACE]

    frontier = frontier.model_copy(update={"workspace_published": True})
    stop_session = reconcile_omnigent_session(intent, frontier, signals, elapsed_seconds=1.0)
    assert _kinds(stop_session) == [OmnigentSessionCommandKind.STOP_PROVIDER_SESSION]

    frontier = frontier.model_copy(update={"provider_session_stopped": True})
    stop_host = reconcile_omnigent_session(intent, frontier, signals, elapsed_seconds=1.0)
    assert _kinds(stop_host) == [OmnigentSessionCommandKind.STOP_HOST]

    frontier = frontier.model_copy(update={"host_stopped": True})
    release = reconcile_omnigent_session(intent, frontier, signals, elapsed_seconds=1.0)
    assert release.status is OmnigentSessionStatus.RELEASING_LEASES
    assert _kinds(release) == [OmnigentSessionCommandKind.RELEASE_LEASES]

    frontier = frontier.model_copy(update={"leases_released": True})
    done = reconcile_omnigent_session(intent, frontier, signals, elapsed_seconds=1.0)
    assert done.is_terminal
    assert done.status is OmnigentSessionStatus.COMPLETED
    assert done.commands == ()


def test_lease_released_last_even_when_host_absent():
    intent = _intent()
    frontier = OmnigentSessionFrontier(
        provider_profile_lease_held=True,
        terminal_observed=True,
        terminal_outcome="completed",
        evidence_harvested=True,
        workspace_published=True,
    )
    decision = reconcile_omnigent_session(
        intent, frontier, OmnigentSessionSignals(), elapsed_seconds=1.0
    )
    assert _kinds(decision) == [OmnigentSessionCommandKind.RELEASE_LEASES]


def test_timeout_reconciles_before_declaring_timed_out():
    intent = _intent(policy=OmnigentSessionReconcilePolicy(maxSessionAgeSeconds=10))
    frontier = _established_frontier(observation_count=0)

    # Stale observation at timeout: force an authoritative snapshot first.
    reconciling = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(seconds_since_last_observation=999.0),
        elapsed_seconds=50.0,
    )
    assert reconciling.status is OmnigentSessionStatus.AWAITING_OBSERVATION
    assert _kinds(reconciling) == [OmnigentSessionCommandKind.OBSERVE_SNAPSHOT]
    assert "reconciling_before_timeout" in reconciling.reason_codes

    # Fresh observation still not terminal -> declare timed_out and clean up.
    declared = reconcile_omnigent_session(
        intent,
        frontier.model_copy(update={"observation_count": 1}),
        OmnigentSessionSignals(seconds_since_last_observation=0.0),
        elapsed_seconds=50.0,
    )
    assert declared.status in (
        OmnigentSessionStatus.CLEANING_UP,
        OmnigentSessionStatus.RELEASING_LEASES,
    )
    assert "reconciled_before_timeout" in declared.reason_codes


def test_delivery_unknown_reobserves_rather_than_resubmitting():
    intent = _intent()
    frontier = _established_frontier()
    decision = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(
            last_command_condition=OmnigentSessionCommandCondition.DELIVERY_UNKNOWN
        ),
        elapsed_seconds=1.0,
    )
    assert decision.status is OmnigentSessionStatus.DELIVERY_UNKNOWN
    assert _kinds(decision) == [OmnigentSessionCommandKind.OBSERVE_SNAPSHOT]


def test_integration_unavailable_reobserves():
    intent = _intent()
    frontier = _established_frontier()
    decision = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(
            last_command_condition=OmnigentSessionCommandCondition.INTEGRATION_UNAVAILABLE
        ),
        elapsed_seconds=1.0,
    )
    assert decision.status is OmnigentSessionStatus.INTEGRATION_UNAVAILABLE
    assert _kinds(decision) == [OmnigentSessionCommandKind.OBSERVE_SNAPSHOT]


def test_cancel_requested_descends_to_cleanup_and_canceled():
    intent = _intent()
    frontier = _established_frontier()
    decision = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(cancel_requested=True),
        elapsed_seconds=1.0,
    )
    # No terminal observed, so cleanup starts at stopping the provider session.
    assert decision.status is OmnigentSessionStatus.CLEANING_UP
    assert _kinds(decision) == [OmnigentSessionCommandKind.STOP_PROVIDER_SESSION]
    assert "cancel_requested" in decision.reason_codes

    cleaned = frontier.model_copy(
        update={
            "provider_session_stopped": True,
            "host_stopped": True,
            "leases_released": True,
        }
    )
    done = reconcile_omnigent_session(
        intent,
        cleaned,
        OmnigentSessionSignals(cancel_requested=True),
        elapsed_seconds=1.0,
    )
    assert done.is_terminal
    assert done.status is OmnigentSessionStatus.CANCELED


def test_cleanup_failure_reports_cleanup_incomplete_and_retries():
    intent = _intent()
    frontier = _established_frontier(terminal_observed=True, terminal_outcome="completed")
    frontier = frontier.model_copy(
        update={"evidence_harvested": True, "workspace_published": True}
    )
    decision = reconcile_omnigent_session(
        intent,
        frontier,
        OmnigentSessionSignals(
            last_command_condition=OmnigentSessionCommandCondition.CLEANUP_FAILED
        ),
        elapsed_seconds=1.0,
    )
    assert decision.status is OmnigentSessionStatus.CLEANUP_INCOMPLETE
    assert "cleanup_incomplete" in decision.reason_codes
    assert _kinds(decision) == [OmnigentSessionCommandKind.STOP_PROVIDER_SESSION]


def test_max_turn_attempts_exhausted_fails():
    intent = _intent(policy=OmnigentSessionReconcilePolicy(maxTurnAttempts=2))
    frontier = OmnigentSessionFrontier(
        provider_profile_lease_held=True,
        host_ready=True,
        provider_session_established=True,
        turn_submitted=False,
        turn_attempts=2,
    )
    decision = reconcile_omnigent_session(
        intent, frontier, OmnigentSessionSignals(), elapsed_seconds=1.0
    )
    assert "max_turn_attempts_exhausted" in decision.reason_codes
    # The provider session was established, so failure cleanup begins by
    # stopping it before eventually releasing the lease last.
    assert _kinds(decision) == [OmnigentSessionCommandKind.STOP_PROVIDER_SESSION]


def test_quarantine_is_terminal():
    intent = _intent()
    decision = reconcile_omnigent_session(
        intent,
        _established_frontier(),
        OmnigentSessionSignals(quarantined=True),
        elapsed_seconds=1.0,
    )
    assert decision.is_terminal
    assert decision.status is OmnigentSessionStatus.RECONCILIATION_QUARANTINED


def test_execution_failed_terminal_outcome_maps_status():
    intent = _intent()
    cleaned = _established_frontier(
        terminal_observed=True,
        terminal_outcome="failed",
        evidence_harvested=True,
        workspace_published=True,
        provider_session_stopped=True,
        host_stopped=True,
        leases_released=True,
    )
    decision = reconcile_omnigent_session(
        intent, cleaned, OmnigentSessionSignals(), elapsed_seconds=1.0
    )
    assert decision.status is OmnigentSessionStatus.EXECUTION_FAILED


@pytest.mark.parametrize(
    "enabled,canary,expected",
    [
        (False, 100, False),
        (True, 0, False),
        (True, 100, True),
    ],
)
def test_admission_policy(enabled, canary, expected):
    policy = OmnigentSessionAdmissionPolicy(enabled=enabled, canary_percent=canary)
    intent = admit_omnigent_session_intent(
        canonical_session_id="wf-1:omnigent",
        execution_intent_ref="ref",
        execution_intent_digest="digest",
        owning_workflow_id="user-wf-1",
        step_execution_id="step-1",
        agent_run_id="wf-1",
        execution_profile_ref="profile:codex",
        initial_turn_attempt_id="turn-1",
        policy=policy,
    )
    assert (intent is not None) is expected


def test_admission_requires_execution_profile():
    policy = OmnigentSessionAdmissionPolicy(enabled=True, canary_percent=100)
    intent = admit_omnigent_session_intent(
        canonical_session_id="wf-1:omnigent",
        execution_intent_ref="ref",
        execution_intent_digest="digest",
        owning_workflow_id="user-wf-1",
        step_execution_id="step-1",
        agent_run_id="wf-1",
        execution_profile_ref="",
        initial_turn_attempt_id="turn-1",
        policy=policy,
    )
    assert intent is None


def test_admission_canary_is_deterministic():
    policy = OmnigentSessionAdmissionPolicy(enabled=True, canary_percent=50)
    first = policy.admits("stable-session-id")
    second = policy.admits("stable-session-id")
    assert first == second


def test_continue_as_new_bounds_but_not_when_terminal():
    intent = _intent(
        policy=OmnigentSessionReconcilePolicy(continueAsNewDecisionThreshold=5)
    )
    frontier = OmnigentSessionFrontier(decision_count=5)
    assert should_continue_as_new(intent, frontier, history_length=0) is True
    released = frontier.model_copy(update={"leases_released": True})
    assert should_continue_as_new(intent, released, history_length=999999) is False
