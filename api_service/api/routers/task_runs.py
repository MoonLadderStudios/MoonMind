from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.api.schemas_task_runs import (
    TaskRunLiveSessionHeartbeatRequest,
    TaskRunLiveSessionReportRequest,
    TaskRunLiveSessionResponse,
    TaskRunLiveSessionWorkerResponse,
)
from api_service.db.models import AgentJobLiveSessionStatus, TaskRunLiveSession

router = APIRouter(prefix="/task-runs", tags=["task_runs"])


@router.get(
    "/{id}/live-session",
    response_model=TaskRunLiveSessionResponse,
    responses={
        404: {"description": "Live session not found for this task run"},
    },
)
async def get_live_session(
    id: UUID,
    db: AsyncSession = Depends(get_async_session),
) -> TaskRunLiveSessionResponse:
    """Get the current live session status for a task run.

    This is intended for Mission Control operators to view live log outputs.
    """
    result = await db.execute(select(TaskRunLiveSession).where(TaskRunLiveSession.task_run_id == id))
    session = result.scalars().first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Live session not found"
        )

    return TaskRunLiveSessionResponse.model_validate(session)


@router.get(
    "/{id}/live-session/worker",
    response_model=TaskRunLiveSessionWorkerResponse,
    responses={
        404: {"description": "Live session not found for this task run"},
    },
)
async def get_live_session_worker(
    id: UUID,
    db: AsyncSession = Depends(get_async_session),
) -> TaskRunLiveSessionWorkerResponse:
    """Fetch current live-session payload for a worker."""
    result = await db.execute(select(TaskRunLiveSession).where(TaskRunLiveSession.task_run_id == id))
    session = result.scalars().first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Live session not found"
        )

    return TaskRunLiveSessionWorkerResponse.model_validate(session)


@router.post(
    "/{id}/live-session/report",
    response_model=TaskRunLiveSessionResponse,
)
async def report_live_session(
    id: UUID,
    request: TaskRunLiveSessionReportRequest,
    db: AsyncSession = Depends(get_async_session),
) -> TaskRunLiveSessionResponse:
    """Report live-session lifecycle updates for a task run."""
    result = await db.execute(select(TaskRunLiveSession).where(TaskRunLiveSession.task_run_id == id))
    session = result.scalars().first()

    now = datetime.now(timezone.utc)
    if not session:
        if not request.provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider is required when creating a new live session.",
            )
        session = TaskRunLiveSession(
            task_run_id=id,
            provider=request.provider,
            status=request.status,
            worker_id=request.worker_id,
        )
        db.add(session)

    # Update fields
    if request.status is not None:
        session.status = request.status
    if request.worker_hostname is not None:
        session.worker_hostname = request.worker_hostname
    if request.tmate_session_name is not None:
        session.tmate_session_name = request.tmate_session_name
    if request.tmate_socket_path is not None:
        session.tmate_socket_path = request.tmate_socket_path
    if request.attach_ro is not None:
        session.attach_ro = request.attach_ro
    if request.attach_rw is not None:
        session.attach_rw_encrypted = request.attach_rw  # Assuming encryption handles it automatically via StringEncryptedType
    if request.web_ro is not None:
        session.web_ro = request.web_ro
    if request.web_rw is not None:
        session.web_rw_encrypted = request.web_rw
    if request.expires_at is not None:
        session.expires_at = request.expires_at
    if request.error_message is not None:
        session.error_message = request.error_message

    if session.status == AgentJobLiveSessionStatus.READY and not session.ready_at:
        session.ready_at = now
    if session.status in (
        AgentJobLiveSessionStatus.ENDED,
        AgentJobLiveSessionStatus.ERROR,
        AgentJobLiveSessionStatus.REVOKED,
    ) and not session.ended_at:
        session.ended_at = now

    session.last_heartbeat_at = now

    await db.commit()
    await db.refresh(session)

    return TaskRunLiveSessionResponse.model_validate(session)


@router.post(
    "/{id}/live-session/heartbeat",
    response_model=dict,
)
async def heartbeat_live_session(
    id: UUID,
    request: TaskRunLiveSessionHeartbeatRequest,
    db: AsyncSession = Depends(get_async_session),
) -> dict:
    """Send live-session heartbeat updates."""
    result = await db.execute(select(TaskRunLiveSession).where(TaskRunLiveSession.task_run_id == id))
    session = result.scalars().first()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Live session not found"
        )

    if session.worker_id != request.worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker ID mismatch",
        )

    session.last_heartbeat_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "ok"}
