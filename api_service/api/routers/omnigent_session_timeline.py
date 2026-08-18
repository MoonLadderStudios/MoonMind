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
from moonmind.omnigent.control_plane.repositories import ControlPlaneRepositories
from moonmind.omnigent.control_plane.stuck_state import (
    SessionSignals,
    detect_stuck_state,
    plan_response,
)
from moonmind.omnigent.control_plane.timeline import build_timeline

router = APIRouter(prefix="/api/omnigent/sessions", tags=["Omnigent Session Timeline"])

_DIAGNOSTIC_PERMISSION = "operations.read"


def _require_diagnostic_read(user: User) -> None:
    if not has_settings_permission(user, _DIAGNOSTIC_PERMISSION):
        raise HTTPException(
            403,
            f"Missing required operator permission: {_DIAGNOSTIC_PERMISSION}.",
        )


def _signals_from_records(session, observations, commands) -> SessionSignals:
    """Derive bounded stuck-state signals from durable records only.

    Provider/host/lease flags stay ``None`` (not observed) because durable
    records alone are not an independent provider observation — the detector must
    not treat an absent observation as an observed negative.
    """

    last_event_at = None
    last_snapshot_at = None
    for observation in observations:
        if observation.observation_type in {"event", "event_frontier", "event_batch"}:
            if last_event_at is None or observation.observed_at > last_event_at:
                last_event_at = observation.observed_at
        if observation.observation_type in {"snapshot", "provider_snapshot"}:
            if last_snapshot_at is None or observation.observed_at > last_snapshot_at:
                last_snapshot_at = observation.observed_at

    active_command = None
    active_command_since = None
    for command in commands:
        if command.status in {"claimed", "delivery_unknown"}:
            active_command = command
            active_command_since = command.updated_at or command.created_at
            if command.status == "delivery_unknown":
                break

    return SessionSignals(
        last_event_at=last_event_at,
        last_snapshot_at=last_snapshot_at,
        active_command=active_command,
        active_command_since=active_command_since,
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

    turn_attempts = await repos.turn_attempts.list_for_session(session_id)
    observations = await repos.observations.list_for_session(session_id)
    commands = await repos.commands.list_for_session(session_id)
    decisions = await repos.decisions.list_for_session(session_id)
    cleanup = await repos.cleanup.get(session_id)

    timeline = build_timeline(
        session=session,
        turn_attempts=turn_attempts,
        observations=observations,
        commands=commands,
        decisions=decisions,
        cleanup=cleanup,
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

    observations = await repos.observations.list_for_session(session_id)
    commands = await repos.commands.list_for_session(session_id)

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    signals = _signals_from_records(session, observations, commands)
    findings = detect_stuck_state(session=session, signals=signals, now=now)
    response = plan_response(session=session, findings=findings)

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
