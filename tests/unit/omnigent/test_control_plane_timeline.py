"""Operator session-timeline projection tests.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

Constructs sessions in launching, running, delivery-unknown, awaiting-observation,
terminal, cleanup-incomplete, and quarantined states and verifies the timeline
explains the state from durable records and survives live-resource deletion.
"""

from __future__ import annotations

from datetime import datetime, timezone

from moonmind.omnigent.control_plane import build_timeline
from moonmind.omnigent.control_plane.records import (
    CleanupAuthorityRecord,
    CommandRecord,
    DecisionRecord,
    ObservationRecord,
    SessionRecord,
    TurnAttemptRecord,
)
from moonmind.omnigent.control_plane.timeline import TimelineStatus

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _session(**overrides) -> SessionRecord:
    base = dict(
        session_id="sess-1",
        moonmind_workflow_id="wf-1",
        provider="codex",
        intent_ref="intent-abc",
        intent_digest="deadbeef",
        desired_state="run",
        revision=3,
        fencing_generation=1,
    )
    base.update(overrides)
    return SessionRecord(**base)


def test_launching_session_explained_from_records():
    timeline = build_timeline(session=_session())
    assert timeline.status is TimelineStatus.LAUNCHING
    doc = timeline.to_dict()
    assert doc["state"]["desired"] == "run"
    assert doc["state"]["revision"] == 3


def test_turn_attempt_count_uses_explicit_count_when_supplied():
    # A caller that fetched only the active turn attempt (bounded query) supplies
    # the authoritative database count instead of len(turn_attempts).
    session = _session(active_turn_attempt_id="turn-1", provider_session_ref="psr-1")
    active = TurnAttemptRecord(
        turn_attempt_id="turn-1", session_id="sess-1", idempotency_key="k1", state="running"
    )
    timeline = build_timeline(
        session=session, turn_attempts=[active], turn_attempt_count=42
    )
    assert timeline.turn_attempt_count == 42
    assert timeline.active_turn_attempt_state == "running"
    # Falls back to the sequence length when no explicit count is given.
    assert build_timeline(session=session, turn_attempts=[active]).turn_attempt_count == 1


def test_awaiting_observation_status():
    session = _session(active_turn_attempt_id="turn-1", provider_session_ref="psr-1")
    timeline = build_timeline(
        session=session,
        turn_attempts=[
            TurnAttemptRecord(
                turn_attempt_id="turn-1", session_id="sess-1", idempotency_key="k1", state="running"
            )
        ],
    )
    assert timeline.status is TimelineStatus.AWAITING_OBSERVATION
    assert timeline.active_turn_attempt_state == "running"


def test_running_status_with_observed_state():
    session = _session(
        active_turn_attempt_id="turn-1", provider_session_ref="psr-1", observed_state="active"
    )
    timeline = build_timeline(session=session)
    assert timeline.status is TimelineStatus.RUNNING


def test_delivery_unknown_status_takes_precedence():
    command = CommandRecord(
        command_id="cmd-1",
        session_id="sess-1",
        command_type="submit_turn",
        idempotency_key="ik-1",
        payload_digest="pd-1",
        status="delivery_unknown",
        delivery_ambiguous=True,
    )
    timeline = build_timeline(session=_session(observed_state="active"), commands=[command])
    assert timeline.status is TimelineStatus.DELIVERY_UNKNOWN
    assert timeline.active_command is not None
    assert timeline.active_command.delivery_ambiguous is True


def test_terminal_with_incomplete_cleanup():
    session = _session(terminal_state="success", cleanup_state="pending")
    cleanup = CleanupAuthorityRecord(session_id="sess-1", state="claimed")
    timeline = build_timeline(session=session, cleanup=cleanup)
    assert timeline.status is TimelineStatus.CLEANUP_INCOMPLETE
    assert timeline.janitor_state == "claimed"


def test_terminal_closed_when_cleanup_complete():
    session = _session(terminal_state="success", cleanup_state="complete")
    cleanup = CleanupAuthorityRecord(session_id="sess-1", state="complete")
    timeline = build_timeline(session=session, cleanup=cleanup)
    assert timeline.status is TimelineStatus.CLOSED


def test_quarantined_status_from_metadata():
    session = _session(metadata={"quarantined": True})
    timeline = build_timeline(session=session)
    assert timeline.status is TimelineStatus.QUARANTINED


def test_timeline_survives_live_resource_deletion():
    # Terminal session whose live provider/host/workspace observations and
    # commands have been cleaned up: only durable session + decision remain.
    session = _session(
        terminal_state="success",
        terminal_evidence_ref="evidence-xyz",
        cleanup_state="complete",
        observed_state="terminal_success",
        reconciled_state="closed",
    )
    decision = DecisionRecord(
        decision_id="dec-1",
        session_id="sess-1",
        decision_code="record_provider_terminal",
        reason_code="terminal_event_observed",
        created_at=NOW,
        trace_ref="trace123",
        diagnostics_ref="artifact456",
    )
    timeline = build_timeline(session=session, observations=[], commands=[], decisions=[decision])
    doc = timeline.to_dict()
    # The durable evidence and last decision are still explained after cleanup.
    assert doc["terminal"]["state"] == "success"
    assert doc["terminal"]["evidenceRef"] == "evidence-xyz"
    assert doc["lastDecision"]["decisionCode"] == "record_provider_terminal"
    # Trace has no registered route in this phase, so the link is omitted rather
    # than pointed at a nonexistent /api/omnigent/traces route (would 404).
    assert doc["lastDecision"]["traceLink"] is None
    assert doc["links"]["trace"] is None
    # Artifact-backed diagnostics link uses the registered /api/artifacts route.
    assert doc["lastDecision"]["diagnosticsLink"] == "/api/artifacts/artifact456"


def test_timeline_drops_secret_like_refs_and_host_paths():
    session = _session(
        intent_ref="/home/app/secret/intent.json",  # host path -> dropped
        terminal_evidence_ref="ghp_" + "a" * 36,  # credential -> dropped
        compatibility_ref="compat-ok",
        image_manifest_ref="sha256digest",
    )
    timeline = build_timeline(session=session)
    doc = timeline.to_dict()
    assert doc["intent"]["ref"] is None
    assert doc["terminal"]["evidenceRef"] is None
    assert doc["compatibility"]["ref"] == "compat-ok"
    assert doc["compatibility"]["imageManifestRef"] == "sha256digest"


def test_timeline_shows_desired_durable_observed_reconciled_difference():
    session = _session(
        desired_state="run",
        observed_state="active",
        reconciled_state="running",
    )
    doc = build_timeline(session=session).to_dict()
    state = doc["state"]
    assert state["desired"] == "run"
    assert state["observed"] == "active"
    assert state["reconciled"] == "running"
    assert state["durable"] == "running"


def test_last_snapshot_derived_from_observations():
    session = _session(active_turn_attempt_id="turn-1", provider_session_ref="psr-1")
    older = ObservationRecord(
        observation_id="o1",
        session_id="sess-1",
        observation_type="snapshot",
        source="provider",
        observed_at=datetime(2026, 8, 18, 11, 0, tzinfo=timezone.utc),
        deduplication_key="d1",
        source_digest="digest-old",
    )
    newer = ObservationRecord(
        observation_id="o2",
        session_id="sess-1",
        observation_type="snapshot",
        source="provider",
        observed_at=NOW,
        deduplication_key="d2",
        source_digest="digest-new",
    )
    timeline = build_timeline(session=session, observations=[older, newer])
    assert timeline.last_snapshot_at == NOW
    assert timeline.last_snapshot_digest == "digest-new"
