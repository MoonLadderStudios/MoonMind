"""Authorized operator session-timeline diagnostic API.

Source: MoonLadderStudios/MoonMind#3708 ([Omnigent control plane 7/11]).

Exposes one authorized, machine-readable diagnostic projection for a single
canonical Omnigent session, derived entirely from durable control-plane records
(canonical session, turn attempts, observations, commands, decisions, and
cleanup authority). The endpoint is a **projection, not a second lifecycle
authority**: it only reads durable records, so it keeps explaining a session
after its live provider/host/workspace resources are cleaned up.

Access requires the ``operations.read`` operator permission. Every emitted field
is bounded and secret-free (see
:mod:`moonmind.omnigent.control_plane.timeline`): no provider credential,
internal token, raw host path, or unbounded payload is ever surfaced.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.settings_catalog import has_settings_permission
from moonmind.omnigent.control_plane.records import CLEANUP_STATE_CLAIMED
from moonmind.omnigent.control_plane.repositories import ControlPlaneRepositories
from moonmind.omnigent.control_plane.stuck_state import (
    SessionSignals,
    detect_stuck_state,
    plan_response,
)
from moonmind.omnigent.control_plane.timeline import build_timeline

router = APIRouter(prefix="/api/omnigent/sessions", tags=["Omnigent Session Timeline"])

_DIAGNOSTIC_PERMISSION = "operations.read"

#: Observation types classified as substantive provider events / snapshots (kept
#: in sync with the timeline projection so both endpoints read the same signal).
_EVENT_OBSERVATION_TYPES = ("event", "event_frontier", "event_batch")
_SNAPSHOT_OBSERVATION_TYPES = ("snapshot", "provider_snapshot")


def _require_diagnostic_read(user: User) -> None:
    if not has_settings_permission(user, _DIAGNOSTIC_PERMISSION):
        raise HTTPException(
            403,
            f"Missing required operator permission: {_DIAGNOSTIC_PERMISSION}.",
        )


def _signals_from_durable(
    session,
    *,
    active_turn,
    latest_event,
    latest_snapshot,
    active_command,
    cleanup,
) -> SessionSignals:
    """Map the durable evidence required by the stuck-state detectors.

    Every signal that a durable record can authoritatively express is derived:
    freshness (latest event/snapshot), the active turn's durable start (so
    absence is aged rather than tripping immediately), the active/ambiguous
    command, cleanup progress, admission, and compatibility resolution.

    Signals that would require an *independent provider observation* the read
    boundary does not own (``provider_terminal``/``provider_active``) or a
    cross-aggregate lease reconciliation (host/profile lease ownership) stay
    ``None`` (not observed): the detector must never treat an absent observation
    as an observed negative, and this endpoint must not fabricate one. Those are
    stamped by the reconciler at its own boundary.
    """

    cleanup_started_at = None
    if cleanup is not None and cleanup.state == CLEANUP_STATE_CLAIMED:
        cleanup_started_at = cleanup.updated_at or cleanup.created_at

    return SessionSignals(
        last_event_at=latest_event.observed_at if latest_event is not None else None,
        last_snapshot_at=latest_snapshot.observed_at if latest_snapshot is not None else None,
        active_turn_started_at=active_turn.created_at if active_turn is not None else None,
        active_command=active_command,
        active_command_since=(
            (active_command.updated_at or active_command.created_at)
            if active_command is not None
            else None
        ),
        cleanup_started_at=cleanup_started_at,
        # Admission and compatibility are durable session facts.
        admitted=session.provider_session_ref is not None,
        compatibility_known=True if session.compatibility_ref is not None else None,
    )


@router.get("/{session_id}/timeline")
async def get_session_timeline(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return the machine-readable operator timeline for one canonical session."""

    _require_diagnostic_read(user)
    repos = ControlPlaneRepositories.bind(db)

    session = await repos.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    # Bounded latest/active queries plus a count, so an operator diagnostic never
    # materializes the full append-only history of a long-running session.
    active_turn = (
        await repos.turn_attempts.get(session.active_turn_attempt_id)
        if session.active_turn_attempt_id is not None
        else None
    )
    turn_attempt_count = await repos.turn_attempts.count_for_session(session_id)
    latest_snapshot = await repos.observations.latest_for_session(
        session_id, observation_types=_SNAPSHOT_OBSERVATION_TYPES
    )
    latest_event = await repos.observations.latest_for_session(
        session_id, observation_types=_EVENT_OBSERVATION_TYPES
    )
    active_command = await repos.commands.active_for_session(session_id)
    latest_decision = await repos.decisions.latest_for_session(session_id)
    cleanup = await repos.cleanup.get(session_id)

    timeline = build_timeline(
        session=session,
        turn_attempts=[active_turn] if active_turn is not None else (),
        observations=[o for o in (latest_snapshot, latest_event) if o is not None],
        commands=[active_command] if active_command is not None else (),
        decisions=[latest_decision] if latest_decision is not None else (),
        cleanup=cleanup,
        turn_attempt_count=turn_attempt_count,
    )
    return timeline.to_dict()


@router.get("/{session_id}/stuck-state")
async def get_session_stuck_state(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return bounded stuck-state findings and the safe automated response.

    Uses server time (``now``) via the control-plane clock; the response is a
    fenced reconcile recommendation or a quarantine escalation. This endpoint
    never mutates state — it surfaces the recommendation for the reconciliation
    workflow and operators to act on.
    """

    _require_diagnostic_read(user)
    repos = ControlPlaneRepositories.bind(db)

    session = await repos.sessions.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")

    # Bounded latest/active queries only; the detectors need the freshest signal
    # on each channel, not the full observation/command history.
    active_turn = (
        await repos.turn_attempts.get(session.active_turn_attempt_id)
        if session.active_turn_attempt_id is not None
        else None
    )
    latest_event = await repos.observations.latest_for_session(
        session_id, observation_types=_EVENT_OBSERVATION_TYPES
    )
    latest_snapshot = await repos.observations.latest_for_session(
        session_id, observation_types=_SNAPSHOT_OBSERVATION_TYPES
    )
    active_command = await repos.commands.active_for_session(session_id)
    cleanup = await repos.cleanup.get(session_id)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    signals = _signals_from_durable(
        session,
        active_turn=active_turn,
        latest_event=latest_event,
        latest_snapshot=latest_snapshot,
        active_command=active_command,
        cleanup=cleanup,
    )
    findings = detect_stuck_state(session=session, signals=signals, now=now)

    # Bounded reconciliation-to-quarantine policy: escalation is driven by the
    # durable per-session/per-reason detection count. The decision journal is the
    # persistence — count prior decisions recorded under the dominant finding's
    # reason so repeated inspections of the same unresolved condition can reach
    # persistent_ambiguity_max and quarantine instead of recommending reconcile
    # forever.
    prior_detection_count = 0
    if findings:
        dominant_reason = findings[0].reason.value
        prior_detection_count = await repos.decisions.count_for_session_reason(
            session_id, dominant_reason
        )
    response = plan_response(
        session=session, findings=findings, prior_detection_count=prior_detection_count
    )

    return {
        "sessionId": session_id,
        "findings": [
            {
                "reason": f.reason.value,
                "action": f.action.value,
                "detail": f.detail,
                "remediation": f.remediation,
            }
            for f in findings
        ],
        "response": None
        if response is None
        else {
            "reconcile": response.reconcile,
            "quarantine": response.quarantine,
            "expectedRevision": response.expected_revision,
            "expectedFencingGeneration": response.expected_fencing_generation,
            "reasons": [r.value for r in response.reasons],
            "remediation": response.remediation,
        },
    }


__all__ = ["router"]
