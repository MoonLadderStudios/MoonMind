import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.worker_auth import _WorkerRequestAuth, _require_worker_auth
from api_service.api.schemas_task_runs import (
    TaskRunLiveSessionHeartbeatRequest,
    TaskRunLiveSessionReportRequest,
    TaskRunLiveSessionResponse,
    TaskRunLiveSessionWorkerResponse,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import AgentJobLiveSessionStatus, TaskRunLiveSession, User
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.services.observability.subscriber import log_stream_generator


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
    if not getattr(_user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires superuser privileges to access raw observability artifacts.",
        )
        
    store = ManagedRunStore(_get_agent_runtime_store_root())
    
    record = await asyncio.to_thread(store.load, str(id))
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )
    return {"summary": record.model_dump(by_alias=True)}


@router.get(
    "/{id}/logs/stream",
    responses={
        403: {"description": "Requires superuser privileges"},
        404: {"description": "Observability record not found for this task run"},
        410: {"description": "Run is no longer active"},
    },
)
async def stream_task_run_live_logs(
    id: UUID,
    request: Request,
    since: int | None = Query(default=None, ge=0, description="Resume from sequence number"),
    _user: User = Depends(get_current_user()),
):
    """Serve SSE real-time stream for active runs."""
    if not getattr(_user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires superuser privileges to access raw observability artifacts.",
        )

    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )

    # Check if run ended => artifact fallback
    terminal_statuses = ["completed", "failed", "canceled", "cancelled", "timed_out"]
    if getattr(record, "status", None) in terminal_statuses:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Run is no longer active. Use artifact retrieval APIs.",
        )
        
    return StreamingResponse(
        log_stream_generator(str(id), request, since=since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )



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
    if not getattr(_user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires superuser privileges to access raw observability artifacts.",
        )

    if stream_name not in ("stdout", "stderr", "merged"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log stream name. Must be stdout, stderr, or merged.",
        )
    
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )

    if stream_name == "merged":
        target_ref = getattr(record, "merged_log_artifact_ref", None)
        missing_detail = "Merged log artifact not found"
    else:
        target_ref = getattr(record, f"{stream_name}_artifact_ref", None)
        missing_detail = f"{stream_name} log artifact not found"
        
    if not target_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=missing_detail,
        )
        
    # The LogStreamer returns references like '<run_id>/stdout.log'
    artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
    artifact_path = (artifacts_root / target_ref).resolve()
    
    try:
        is_safe = artifact_path.is_relative_to(artifacts_root)
    except Exception:
        is_safe = False
        
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid artifact path",
        )
        
    is_file = await asyncio.to_thread(artifact_path.is_file)
    if not is_file:
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
    if not getattr(_user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requires superuser privileges to access raw observability artifacts.",
        )

    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))
    
    if not record or not record.diagnostics_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostics artifact not found",
        )
        
    artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
    artifact_path = (artifacts_root / record.diagnostics_ref).resolve()
    
    try:
        is_safe = artifact_path.is_relative_to(artifacts_root)
    except Exception:
        is_safe = False
        
    if not is_safe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid artifact path",
        )
        
    is_file = await asyncio.to_thread(artifact_path.is_file)
    if not is_file:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Diagnostics file {record.diagnostics_ref} does not exist",
        )
        
    return FileResponse(
        path=artifact_path,
        media_type="application/json",
    )
