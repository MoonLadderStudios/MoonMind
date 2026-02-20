"""Operator endpoints for managing the global worker pause state."""

from __future__ import annotations

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.schemas import (
    QueueSystemMetadataModel,
    WorkerPauseAuditEventModel,
    WorkerPauseAuditListModel,
    WorkerPauseMetricsModel,
    WorkerPauseSnapshotResponse,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.workflows import get_agent_queue_repository
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
    QueueSystemMetadata,
    WorkerPauseAuditEvent,
    WorkerPauseSnapshot,
)

router = APIRouter(prefix="/api/system/worker-pause", tags=["system-worker-pause"])
logger = logging.getLogger(__name__)


class WorkerPauseActionRequest(BaseModel):
    """Request payload for pause/resume actions."""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["pause", "resume"] = Field(..., alias="action")
    mode: Optional[Literal["drain", "quiesce"]] = Field(None, alias="mode")
    reason: str = Field(..., alias="reason", min_length=1)
    force_resume: bool = Field(False, alias="forceResume")


async def _get_repository(
    session: AsyncSession = Depends(get_async_session),
) -> AgentQueueRepository:
    return get_agent_queue_repository(session)


async def _get_service(
    repository: AgentQueueRepository = Depends(_get_repository),
) -> AgentQueueService:
    return AgentQueueService(repository)


@router.get("", response_model=WorkerPauseSnapshotResponse)
async def get_worker_pause_state(
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> WorkerPauseSnapshotResponse:
    """Return the current worker pause snapshot."""

    snapshot = await service.get_worker_pause_snapshot()
    return _serialize_worker_pause_snapshot(snapshot)


@router.post("", response_model=WorkerPauseSnapshotResponse)
async def apply_worker_pause_state(
    payload: WorkerPauseActionRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> WorkerPauseSnapshotResponse:
    """Pause or resume workers via the global control."""

    try:
        snapshot = await service.apply_worker_pause_action(
            action=payload.action,
            mode=payload.mode,
            reason=payload.reason,
            actor_user_id=getattr(user, "id", None),
            force_resume=payload.force_resume,
        )
    except AgentQueueValidationError as exc:
        raise HTTPException(  # noqa: B904
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "worker_pause_invalid_request",
                "message": str(exc) or "Invalid worker pause request.",
            },
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive fail-safe
        logger.exception("Worker pause update failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "worker_pause_apply_failed",
                "message": "Failed to update worker pause state.",
            },
        ) from exc

    return _serialize_worker_pause_snapshot(snapshot)


def _serialize_worker_pause_snapshot(
    snapshot: WorkerPauseSnapshot,
) -> WorkerPauseSnapshotResponse:
    return WorkerPauseSnapshotResponse(
        system=_serialize_system_metadata(snapshot.system),
        metrics=WorkerPauseMetricsModel(
            queued=snapshot.metrics.queued,
            running=snapshot.metrics.running,
            stale_running=snapshot.metrics.stale_running,
            is_drained=snapshot.metrics.is_drained,
        ),
        audit=WorkerPauseAuditListModel(
            latest=[_serialize_audit_event(event) for event in snapshot.audit_events]
        ),
    )


def _serialize_system_metadata(
    metadata: QueueSystemMetadata,
) -> QueueSystemMetadataModel:
    return QueueSystemMetadataModel(
        workers_paused=metadata.workers_paused,
        mode=metadata.mode.value if metadata.mode else None,
        reason=metadata.reason,
        version=metadata.version,
        requested_by_user_id=metadata.requested_by_user_id,
        requested_at=metadata.requested_at,
        updated_at=metadata.updated_at,
    )


def _serialize_audit_event(
    event: WorkerPauseAuditEvent,
) -> WorkerPauseAuditEventModel:
    return WorkerPauseAuditEventModel(
        id=event.id,
        action=event.action,
        mode=event.mode.value if event.mode else None,
        reason=event.reason,
        actorUserId=event.actor_user_id,
        createdAt=event.created_at,
    )
