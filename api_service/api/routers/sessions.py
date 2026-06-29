from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from api_service.api.routers import agent_runs as agent_runs_router
from api_service.api.routers.temporal_artifacts import _get_temporal_artifact_service
from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.schemas.temporal_artifact_models import ArtifactSessionControlRequest
from moonmind.workflows.temporal.artifacts import TemporalArtifactService

router = APIRouter(prefix="/sessions", tags=["sessions"])

ManagedRunStore = agent_runs_router.ManagedRunStore
ManagedSessionStore = agent_runs_router.ManagedSessionStore
_build_agent_run_artifact_session_projection = (
    agent_runs_router._build_agent_run_artifact_session_projection
)
_load_agent_run_observability_events = agent_runs_router._load_agent_run_observability_events
control_agent_run_artifact_session = agent_runs_router.control_agent_run_artifact_session
stream_agent_run_live_logs = agent_runs_router.stream_agent_run_live_logs


def _require_session_api_compat_enabled() -> None:
    if not settings.feature_flags.session_api_compat_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "session_api_compat_disabled",
                "message": (
                    "Session API aliases are disabled. Enable "
                    "MOONMIND_SESSION_API_COMPAT_ENABLED or "
                    "FEATURE_FLAGS__SESSION_API_COMPAT_ENABLED to use this surface."
                ),
            },
        )


async def _load_authorized_session_record(
    session_id: str,
    user: User,
) -> CodexManagedSessionRecord:
    store = ManagedSessionStore(
        agent_runs_router._get_managed_session_store_root()
    )
    try:
        record = await asyncio.to_thread(store.load, session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid session id",
        ) from exc
    if record is None:
        agent_runs_router._session_projection_not_found()
    await agent_runs_router._require_agent_run_access(record.agent_run_id, user)
    return record


async def _load_agent_run_record(agent_run_id: str) -> object | None:
    store = ManagedRunStore(
        agent_runs_router._get_agent_runtime_store_root()
    )
    return await agent_runs_router._load_managed_run_record(store, agent_run_id)


async def _session_projection(
    record: CodexManagedSessionRecord,
    artifact_service: TemporalArtifactService,
):
    projection = await _build_agent_run_artifact_session_projection(
        agent_run_id=record.agent_run_id,
        session_id=record.session_id,
        service=artifact_service,
    )
    if projection is None:
        agent_runs_router._session_projection_not_found()
    return projection


def _artifact_refs_from_projection(projection: object) -> dict[str, Any]:
    return {
        "latestSummaryRef": getattr(projection, "latest_summary_ref", None),
        "latestCheckpointRef": getattr(projection, "latest_checkpoint_ref", None),
        "latestControlEventRef": getattr(projection, "latest_control_event_ref", None),
        "latestResetBoundaryRef": getattr(projection, "latest_reset_boundary_ref", None),
        "groupedArtifacts": getattr(projection, "grouped_artifacts", []),
    }


_SUPPORTED_ELICITATION_DECISIONS = {"approve", "reject"}

_ELICITATION_DECISION_MESSAGES = {
    "approve": "Approved.",
    "reject": "Rejected.",
}


def _elicitation_resolution_decision(payload: dict[str, Any]) -> str:
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in _SUPPORTED_ELICITATION_DECISIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unsupported_elicitation_resolution",
                "message": (
                    "Supported elicitation resolution decisions are approve "
                    "and reject."
                ),
            },
        )
    return decision


def _event_item(event: dict[str, Any]) -> dict[str, Any]:
    sequence = event.get("sequence")
    kind = str(event.get("kind") or event.get("stream") or "event")
    return {
        "id": f"event:{sequence}" if isinstance(sequence, int) else f"event:{kind}",
        "type": "event",
        "kind": kind,
        "sequence": sequence,
        "timestamp": event.get("timestamp"),
        "sessionEpoch": event.get("sessionEpoch"),
        "threadId": event.get("threadId"),
        "turnId": event.get("turnId"),
        "event": event,
    }


def _artifact_items(projection: object) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, artifact_ref in (
        ("latest_summary", getattr(projection, "latest_summary_ref", None)),
        ("latest_checkpoint", getattr(projection, "latest_checkpoint_ref", None)),
        ("latest_control_event", getattr(projection, "latest_control_event_ref", None)),
        ("latest_reset_boundary", getattr(projection, "latest_reset_boundary_ref", None)),
    ):
        if artifact_ref is None:
            continue
        items.append(
            {
                "id": f"artifact:{name}",
                "type": "artifact_ref",
                "kind": name,
                "artifactRef": artifact_ref,
            }
        )
    return items


@router.get("/{session_id}", response_model=dict)
async def get_session_snapshot(
    session_id: str,
    _enabled: None = Depends(_require_session_api_compat_enabled),
    _user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> dict[str, Any]:
    record = await _load_authorized_session_record(session_id, _user)
    projection = await _session_projection(record, artifact_service)
    run_record = await _load_agent_run_record(record.agent_run_id)
    return {
        "id": record.session_id,
        "agentRunId": record.agent_run_id,
        "workflowId": getattr(run_record, "workflow_id", None),
        "status": getattr(run_record, "status", None) or record.status,
        "sessionEpoch": record.session_epoch,
        "interventionCapabilities": agent_runs_router._build_intervention_capabilities(
            record
        ),
        "artifactRefs": _artifact_refs_from_projection(projection),
    }


@router.get("/{session_id}/items", response_model=dict)
async def get_session_items(
    session_id: str,
    since: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
    _enabled: None = Depends(_require_session_api_compat_enabled),
    _user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> dict[str, Any]:
    record = await _load_authorized_session_record(session_id, _user)
    run_record = await _load_agent_run_record(record.agent_run_id)
    if run_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this session",
        )
    events, source = await asyncio.to_thread(
        _load_agent_run_observability_events,
        record=run_record,
        session_record=record,
        limit=limit,
        since=since,
        streams=set(),
        kinds=set(),
        session_epochs={record.session_epoch},
        thread_ids=set(),
    )
    events = agent_runs_router._filter_observability_events(
        events,
        since=since,
        streams=set(),
        kinds=set(),
        session_epochs={record.session_epoch},
        thread_ids=set(),
    )
    events.sort(key=agent_runs_router._event_sort_key)
    truncated = len(events) > limit
    if truncated:
        events = events[:limit]
    projection = await _session_projection(record, artifact_service)
    return {
        "items": [_event_item(event) for event in events] + _artifact_items(projection),
        "truncated": truncated,
        "source": source,
        "sessionEpoch": record.session_epoch,
    }


@router.get("/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    since: int | None = Query(default=None, ge=0),
    format: str = Query(default="sse"),
    _enabled: None = Depends(_require_session_api_compat_enabled),
    _user: User = Depends(get_current_user()),
):
    if format != "sse":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported session stream format. Supported formats: sse.",
        )
    record = await _load_authorized_session_record(session_id, _user)
    return await stream_agent_run_live_logs(
        id=record.agent_run_id,
        request=request,
        since=since,
        _user=_user,
    )


@router.post("/{session_id}/events", response_model=dict)
async def post_session_event(
    session_id: str,
    payload: dict[str, Any],
    _enabled: None = Depends(_require_session_api_compat_enabled),
    _user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(
        _get_temporal_artifact_service
    ),
) -> dict[str, Any]:
    record = await _load_authorized_session_record(session_id, _user)
    event_type = str(payload.get("type") or "").strip()
    if event_type == "message":
        try:
            control = ArtifactSessionControlRequest(
                action="send_follow_up",
                message=payload.get("message"),
                reason=payload.get("reason"),
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="message is required when type=message",
            ) from exc
    elif event_type == "interrupt":
        try:
            control = ArtifactSessionControlRequest(
                action="interrupt_turn",
                reason=payload.get("reason"),
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid interrupt event payload",
            ) from exc
    elif event_type == "clear_session":
        try:
            control = ArtifactSessionControlRequest(
                action="clear_session",
                reason=payload.get("reason"),
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid clear_session event payload",
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported session event type: {event_type or '<missing>'}",
        )
    response = await control_agent_run_artifact_session(
        agent_run_id=record.agent_run_id,
        session_id=record.session_id,
        payload=control,
        _user=_user,
        artifact_service=artifact_service,
    )
    return {
        "type": event_type,
        "action": response.action,
        "projection": jsonable_encoder(response.projection),
    }


@router.post("/{session_id}/elicitations/{elicitation_id}/resolve", response_model=dict)
async def resolve_session_elicitation(
    session_id: str,
    elicitation_id: str,
    payload: dict[str, Any],
    _enabled: None = Depends(_require_session_api_compat_enabled),
    _user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> dict[str, Any]:
    record = await _load_authorized_session_record(session_id, _user)
    decision = _elicitation_resolution_decision(payload)
    capabilities = agent_runs_router._build_intervention_capabilities(record)
    try:
        agent_runs_router._require_session_control_capability(
            action="send_follow_up",
            capabilities=capabilities,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This managed session is not available for elicitation "
                    "resolution."
                ),
            ) from exc
        raise
    control = ArtifactSessionControlRequest(
        action="send_follow_up",
        message=_ELICITATION_DECISION_MESSAGES[decision],
        reason=f"session_elicitation:{elicitation_id}:{decision}",
    )
    response = await control_agent_run_artifact_session(
        agent_run_id=record.agent_run_id,
        session_id=record.session_id,
        payload=control,
        _user=_user,
        artifact_service=artifact_service,
    )
    return {
        "type": "elicitation_resolution",
        "elicitationId": elicitation_id,
        "decision": decision,
        "action": response.action,
        "projection": jsonable_encoder(response.projection),
    }
