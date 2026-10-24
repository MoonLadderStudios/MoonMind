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

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_instance_access
from api_service.db.base import get_async_session
from api_service.services.omnigent_session_timeline_service import (
    OmnigentDiagnosticTargetUnavailable,
    OmnigentSessionProjectionNotFound,
    OmnigentSessionTimelineService,
)

router = APIRouter(prefix="/api/omnigent/sessions", tags=["Omnigent Session Timeline"])


@router.get("/{session_id}/timeline")
async def get_session_timeline(
    session_id: str,
    user: Any = Depends(get_instance_access),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return the machine-readable operator timeline for one canonical session."""

    try:
        return await OmnigentSessionTimelineService(db).timeline(session_id)
    except OmnigentSessionProjectionNotFound:
        raise HTTPException(404, "Session not found")


async def _diagnostic_redirect(
    *, session_id: str, kind: str, user: Any, db: AsyncSession
) -> RedirectResponse:
    try:
        target = await OmnigentSessionTimelineService(db).diagnostic_target(
            session_id, kind
        )
    except OmnigentSessionProjectionNotFound:
        raise HTTPException(404, "Session not found")
    except OmnigentDiagnosticTargetUnavailable:
        raise HTTPException(404, f"Authorized {kind} backend link unavailable")
    return RedirectResponse(target, status_code=307)


@router.get("/{session_id}/trace")
async def get_session_trace_link(
    session_id: str,
    user: Any = Depends(get_instance_access),
    db: AsyncSession = Depends(get_async_session),
) -> RedirectResponse:
    return await _diagnostic_redirect(
        session_id=session_id, kind="trace", user=user, db=db
    )


@router.get("/{session_id}/logs")
async def get_session_log_link(
    session_id: str,
    user: Any = Depends(get_instance_access),
    db: AsyncSession = Depends(get_async_session),
) -> RedirectResponse:
    return await _diagnostic_redirect(
        session_id=session_id, kind="logs", user=user, db=db
    )


@router.get("/{session_id}/stuck-state")
async def get_session_stuck_state(
    session_id: str,
    user: Any = Depends(get_instance_access),
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Return bounded stuck-state findings and the safe automated response.

    Uses server time (``now``) via the control-plane clock; the response is a
    fenced reconcile recommendation or a quarantine escalation. This endpoint
    never mutates state; the durable scheduled reconciler separately records and
    executes the same pure response plan.
    """

    try:
        return await OmnigentSessionTimelineService(db).stuck_state(session_id)
    except OmnigentSessionProjectionNotFound:
        raise HTTPException(404, "Session not found")


__all__ = ["router"]
