"""Stuck-state detector and automated-response tests.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

For the listed conditions: detect at the correct deadline, avoid false positives
while progress is occurring, trigger one idempotent fenced reconcile request,
never duplicate a turn or release authority, and quarantine persistent ambiguity.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from moonmind.omnigent.control_plane.records import CommandRecord, SessionRecord
from moonmind.omnigent.control_plane.stuck_state import (
    ResponseAction,
    SessionSignals,
    StuckStatePolicy,
    StuckStateReason,
    detect_stuck_state,
    plan_response,
)

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
POLICY = StuckStatePolicy()


def _session(**overrides) -> SessionRecord:
    base = dict(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        revision=5,
        fencing_generation=2,
        active_turn_attempt_id="turn-1",
    )
    base.update(overrides)
    return SessionRecord(**base)


def _reasons(findings):
    return {f.reason for f in findings}


def test_no_findings_when_progress_is_fresh():
    signals = SessionSignals(
        last_event_at=NOW - timedelta(seconds=5),
        last_snapshot_at=NOW - timedelta(seconds=5),
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert findings == []
    assert plan_response(session=_session(), findings=findings) is None


def test_active_no_recent_evidence_detected_at_deadline():
    stale = NOW - POLICY.event_staleness
    signals = SessionSignals(last_event_at=stale, last_snapshot_at=stale)
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE in _reasons(findings)


def test_never_observed_counts_as_no_recent_evidence():
    # Absent observations aged past the deadline from the durable turn start trip
    # the freshness finding, but are not treated as an observed
    # provider-terminal/active negative.
    signals = SessionSignals(
        last_event_at=None,
        last_snapshot_at=None,
        active_turn_started_at=NOW - POLICY.event_staleness,
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE in _reasons(findings)
    assert StuckStateReason.PROVIDER_TERMINAL_MOONMIND_NONTERMINAL not in _reasons(findings)


def test_fresh_turn_with_no_observations_does_not_trip_freshness():
    # A turn that has just started with no observations yet must not immediately
    # trip the freshness finding; absence is aged from the durable start.
    signals = SessionSignals(
        last_event_at=None,
        last_snapshot_at=None,
        active_turn_started_at=NOW - timedelta(seconds=5),
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE not in _reasons(findings)


def test_fresh_evidence_on_one_channel_suppresses_freshness_finding():
    # One channel is stale, but the other just reported progress => suppressed.
    signals = SessionSignals(
        last_event_at=NOW - POLICY.event_staleness,  # stale
        last_snapshot_at=NOW - timedelta(seconds=5),  # fresh
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE not in _reasons(findings)


def test_absent_provider_observation_is_not_a_negative():
    # provider_terminal is None (not observed) => no divergence finding.
    signals = SessionSignals(
        last_event_at=NOW, last_snapshot_at=NOW, provider_terminal=None, provider_active=None
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.PROVIDER_TERMINAL_MOONMIND_NONTERMINAL not in _reasons(findings)


def test_provider_terminal_moonmind_nonterminal():
    signals = SessionSignals(last_event_at=NOW, last_snapshot_at=NOW, provider_terminal=True)
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    reasons = _reasons(findings)
    assert StuckStateReason.PROVIDER_TERMINAL_MOONMIND_NONTERMINAL in reasons


def test_moonmind_terminal_provider_active():
    session = _session(terminal_state="success")
    signals = SessionSignals(provider_active=True)
    findings = detect_stuck_state(session=session, signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.MOONMIND_TERMINAL_PROVIDER_ACTIVE in _reasons(findings)


def test_active_turn_liveness_only():
    signals = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        liveness_only_since=NOW - POLICY.liveness_only_max,
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.ACTIVE_TURN_LIVENESS_ONLY in _reasons(findings)


def test_repeated_no_progress():
    signals = SessionSignals(
        last_event_at=NOW, last_snapshot_at=NOW, consecutive_no_progress=POLICY.no_progress_max
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.REPEATED_RECONCILIATION_NO_PROGRESS in _reasons(findings)


def test_orphan_host_lease():
    signals = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        host_lease_active=True,
        host_lease_owns_session_authority=False,
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.HOST_LEASE_WITHOUT_SESSION_AUTHORITY in _reasons(findings)


def test_profile_lease_without_consumer():
    signals = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        profile_lease_active=True,
        profile_lease_has_consumer=False,
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.PROFILE_LEASE_WITHOUT_CONSUMER in _reasons(findings)


def test_cleanup_incomplete_past_deadline():
    session = _session(terminal_state="success", cleanup_state="claimed", active_turn_attempt_id=None)
    signals = SessionSignals(cleanup_started_at=NOW - POLICY.cleanup_deadline)
    findings = detect_stuck_state(session=session, signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.CLEANUP_INCOMPLETE_PAST_DEADLINE in _reasons(findings)


def test_compatibility_unknown_after_admission():
    signals = SessionSignals(
        last_event_at=NOW, last_snapshot_at=NOW, admitted=True, compatibility_known=None
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.COMPATIBILITY_UNKNOWN_AFTER_ADMISSION in _reasons(findings)


def test_command_stuck_claimed():
    command = CommandRecord(
        command_id="cmd-1",
        session_id="sess-1",
        command_type="submit_turn",
        idempotency_key="ik",
        payload_digest="pd",
        status="claimed",
    )
    signals = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        active_command=command,
        active_command_since=NOW - POLICY.command_stuck_max,
    )
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.COMMAND_STUCK_CLAIMED_OR_DELIVERY_UNKNOWN in _reasons(findings)


def test_live_conformance_evidence_requests_reconcile_at_exact_deadline():
    just_fresh = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        conformance_evidence_at=(
            NOW - POLICY.conformance_max_age + timedelta(seconds=1)
        ),
    )
    assert StuckStateReason.LIVE_CONFORMANCE_EVIDENCE_STALE not in _reasons(
        detect_stuck_state(
            session=_session(), signals=just_fresh, now=NOW, policy=POLICY
        )
    )

    at_deadline = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        conformance_evidence_at=NOW - POLICY.conformance_max_age,
    )
    findings = detect_stuck_state(
        session=_session(), signals=at_deadline, now=NOW, policy=POLICY
    )
    conformance = [f for f in findings if f.reason is StuckStateReason.LIVE_CONFORMANCE_EVIDENCE_STALE]
    assert conformance and conformance[0].action is ResponseAction.RECONCILE


def test_missing_live_conformance_evidence_after_admission_requests_reconcile():
    signals = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        admitted=True,
        conformance_evidence_at=None,
    )

    findings = detect_stuck_state(
        session=_session(), signals=signals, now=NOW, policy=POLICY
    )

    conformance = [
        finding
        for finding in findings
        if finding.reason is StuckStateReason.LIVE_CONFORMANCE_EVIDENCE_STALE
    ]
    assert conformance and conformance[0].action is ResponseAction.RECONCILE


def test_no_false_positive_just_before_deadline():
    just_fresh = NOW - POLICY.event_staleness + timedelta(seconds=1)
    signals = SessionSignals(last_event_at=just_fresh, last_snapshot_at=just_fresh)
    findings = detect_stuck_state(session=_session(), signals=signals, now=NOW, policy=POLICY)
    assert StuckStateReason.MOONMIND_ACTIVE_NO_RECENT_EVIDENCE not in _reasons(findings)


# --- Automated response policy ----------------------------------------------


def test_first_response_is_a_fenced_reconcile_bound_to_current_authority():
    session = _session(revision=9, fencing_generation=4)
    signals = SessionSignals(provider_terminal=True, last_event_at=NOW, last_snapshot_at=NOW)
    findings = detect_stuck_state(session=session, signals=signals, now=NOW, policy=POLICY)
    response = plan_response(session=session, findings=findings)
    assert response is not None
    assert response.reconcile is True
    assert response.quarantine is False
    assert response.expected_revision == 9
    assert response.expected_fencing_generation == 4


def test_detector_never_authorizes_a_provider_mutation():
    # No action across the entire ResponseAction vocabulary is a resubmit/release.
    assert {a.value for a in ResponseAction} == {"reconcile", "quarantine", "observe"}


def test_persistent_ambiguity_escalates_to_quarantine():
    session = _session()
    signals = SessionSignals(provider_terminal=True, last_event_at=NOW, last_snapshot_at=NOW)
    findings = detect_stuck_state(session=session, signals=signals, now=NOW, policy=POLICY)
    response = plan_response(
        session=session,
        findings=findings,
        prior_detection_count=POLICY.persistent_ambiguity_max,
        policy=POLICY,
    )
    assert response is not None
    assert response.quarantine is True
    assert response.reconcile is False
    assert "reasons" in response.diagnostics


def test_live_conformance_first_response_is_a_fenced_reconcile():
    session = _session()
    signals = SessionSignals(
        last_event_at=NOW,
        last_snapshot_at=NOW,
        conformance_evidence_at=NOW - POLICY.conformance_max_age,
    )
    findings = detect_stuck_state(session=session, signals=signals, now=NOW, policy=POLICY)
    response = plan_response(session=session, findings=findings)
    assert response is not None
    assert response.reconcile is True
    assert response.quarantine is False
    assert response.expected_revision == session.revision
    assert response.expected_fencing_generation == session.fencing_generation
    assert response.remediation is not None
