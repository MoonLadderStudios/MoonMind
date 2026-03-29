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
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import AgentJobLiveSessionStatus, TaskRunLiveSession, User
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.services.observability.subscriber import log_stream_generator
from moonmind.utils.metrics import get_metrics_emitter

router = APIRouter(prefix="/task-runs", tags=["task_runs"])


# Live Session legacy endpoints removed in Phase 6. Use /observability-summary and /logs/stream.


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
        
    metrics = get_metrics_emitter()
    tags = {"stream": "livelogs"}
    metrics.increment("livelogs.stream.connect", tags=tags)

    async def _instrumented_generator():
        try:
            async for chunk in log_stream_generator(str(id), request, since=since):
                yield chunk
        except asyncio.CancelledError:
            raise
        except Exception:
            metrics.increment("livelogs.stream.error", tags=tags)
            raise
        finally:
            metrics.increment("livelogs.stream.disconnect", tags=tags)

    return StreamingResponse(
        _instrumented_generator(),
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
