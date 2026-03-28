from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.api.schemas_task_runs import (
    TaskRunLiveSessionHeartbeatRequest,
    TaskRunLiveSessionReportRequest,
    TaskRunLiveSessionResponse,
    TaskRunLiveSessionWorkerResponse,
)
from api_service.db.models import AgentJobLiveSessionStatus, TaskRunLiveSession, User
from api_service.auth_providers import get_current_user
from api_service.api.routers.worker_auth import _require_worker_auth, _WorkerRequestAuth

router = APIRouter(prefix="/task-runs", tags=["task_runs"])


async def get_live_session_db(
    id: UUID, db: AsyncSession = Depends(get_async_session)
) -> TaskRunLiveSession:
    result = await db.execute(
        select(TaskRunLiveSession).where(TaskRunLiveSession.task_run_id == id)
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Live session not found"
        )
    return session


@router.get(
    "/{id}/live-session",
    response_model=dict,
    responses={
        404: {"description": "Live session not found for this task run"},
    },
)
async def get_live_session(
    session: TaskRunLiveSession = Depends(get_live_session_db),
    _user: User = Depends(get_current_user()),
) -> dict:
    """Get the current live session status for a task run.

    This is intended for Mission Control operators to view live log outputs.
    """
    return {"session": TaskRunLiveSessionResponse.model_validate(session)}


@router.get(
    "/{id}/live-session/worker",
    response_model=dict,
    responses={
        404: {"description": "Live session not found for this task run"},
    },
)
async def get_live_session_worker(
    session: TaskRunLiveSession = Depends(get_live_session_db),
    _worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> dict:
    """Fetch current live-session payload for a worker."""
    base_response = TaskRunLiveSessionWorkerResponse.model_validate(session)
    data = base_response.model_dump(by_alias=True)

    if hasattr(session, "attach_rw_encrypted"):
        data["attachRw"] = getattr(session, "attach_rw_encrypted")
    if hasattr(session, "web_rw_encrypted"):
        data["webRw"] = getattr(session, "web_rw_encrypted")

    return {"session": data}


@router.post(
    "/{id}/live-session/report",
    response_model=dict,
)
async def report_live_session(
    id: UUID,
    request: TaskRunLiveSessionReportRequest,
    db: AsyncSession = Depends(get_async_session),
    _worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> dict:
    """Report live-session lifecycle updates for a task run."""
    result = await db.execute(
        select(TaskRunLiveSession).where(TaskRunLiveSession.task_run_id == id)
    )
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
    else:
        if session.worker_id and request.worker_id and session.worker_id != request.worker_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Worker ID mismatch logic prevents hijacking.",
            )

    # Data driven updates
    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        if field == "attach_rw":
            session.attach_rw_encrypted = value
        elif field == "web_rw":
            session.web_rw_encrypted = value
        elif hasattr(session, field):
            setattr(session, field, value)

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

    return {"session": TaskRunLiveSessionResponse.model_validate(session)}


@router.post(
    "/{id}/live-session/heartbeat",
    response_model=dict,
)
async def heartbeat_live_session(
    request: TaskRunLiveSessionHeartbeatRequest,
    session: TaskRunLiveSession = Depends(get_live_session_db),
    db: AsyncSession = Depends(get_async_session),
    _worker_auth: _WorkerRequestAuth = Depends(_require_worker_auth),
) -> dict:
    """Send live-session heartbeat updates."""
    if session.worker_id != request.worker_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker ID mismatch",
        )

    session.last_heartbeat_at = datetime.now(timezone.utc)
    await db.commit()

    return {"status": "ok"}


# Observability API endpoints

import os
from pathlib import Path
from fastapi.responses import FileResponse
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

def _get_agent_runtime_store_root() -> str:
    return os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
        "managed_runs",
    )

def _get_agent_runtime_artifacts_root() -> str:
    return os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_ARTIFACTS", "/work/agent_jobs"),
        "artifacts",
    )

@router.get(
    "/{id}/observability-summary",
    response_model=dict,
    responses={
        404: {"description": "Observability record not found for this task run"},
    },
)
async def get_observability_summary(
    id: UUID,
    _user: User = Depends(get_current_user()),
) -> dict:
    """Fetch the observability summary for a task run from the shared agent jobs volume."""
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = store.load(str(id))
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )
    return {"summary": record.model_dump(by_alias=True)}


@router.get(
    "/{id}/logs/{stream_name}",
    responses={
        404: {"description": "Log artifact not found"},
        400: {"description": "Invalid stream name"},
    },
)
async def stream_task_run_log(
    id: UUID,
    stream_name: str,
    _user: User = Depends(get_current_user()),
):
    """Serve stdout, stderr, or merged logs directly from the shared volume."""
    if stream_name not in ("stdout", "stderr", "merged"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log stream name. Must be stdout, stderr, or merged.",
        )
    
    target_stream = "stdout" if stream_name == "merged" else stream_name

    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = store.load(str(id))
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )

    target_ref = getattr(record, f"{target_stream}_artifact_ref", None)
    if not target_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{target_stream} log artifact not found",
        )
        
    # The LogStreamer returns references like '<run_id>/stdout.log'
    artifact_path = Path(_get_agent_runtime_artifacts_root()) / target_ref
    
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log file {target_ref} does not exist",
        )
        
    return FileResponse(
        path=artifact_path,
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@router.get(
    "/{id}/diagnostics",
    responses={
        404: {"description": "Diagnostics artifact not found"},
    },
)
async def get_task_run_diagnostics(
    id: UUID,
    _user: User = Depends(get_current_user()),
):
    """Return the diagnostics.json payload for a task run."""
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = store.load(str(id))
    
    if not record or not record.diagnostics_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostics artifact not found",
        )
        
    artifact_path = Path(_get_agent_runtime_artifacts_root()) / record.diagnostics_ref
    
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnostics file {record.diagnostics_ref} does not exist",
        )
        
    return FileResponse(
        path=artifact_path,
        media_type="application/json",
    )
