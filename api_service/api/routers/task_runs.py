import asyncio
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from api_service.api.routers.worker_auth import _WorkerRequestAuth, _require_worker_auth
from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.services.observability.subscriber import log_stream_generator
from moonmind.utils.metrics import get_metrics_emitter
from moonmind.schemas.agent_runtime_models import is_terminal_agent_run_state
from moonmind.observability.transport import SpoolLogReader

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

    terminal_statuses = {"completed", "failed", "canceled", "cancelled", "timed_out"}
    run_status = getattr(record, "status", None)
    is_terminal = run_status in terminal_statuses

    raw_live_stream_capable = getattr(record, "live_stream_capable", None)
    if is_terminal:
        supports_live = False
        live_stream_status = "ended"
    elif raw_live_stream_capable is True:
        supports_live = True
        live_stream_status = "available"
    else:  # Catches False and None
        supports_live = False
        live_stream_status = "unavailable"

    base = record.model_dump(by_alias=True)
    base["supportsLiveStreaming"] = supports_live
    base["liveStreamStatus"] = live_stream_status
    return {"summary": base}


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
        
    # Check capabilities 
    if not getattr(record, "live_stream_capable", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Live streaming is not supported for this run.",
        )

    metrics = get_metrics_emitter()
    tags = {"stream": "livelogs"}
    metrics.increment("livelogs.stream.connect", tags=tags)

    # Use queries parameter 'since'
    since_sequence = since or 0

    # Resolve correct workspace specifically by path tracking, matching the agent job
    job_workspace = getattr(record, "workspace_path", None)
    if not job_workspace:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Workspace path for this run is not available; cannot stream logs.",
        )
    
    reader = SpoolLogReader(workspace_path=str(job_workspace))

    async def _instrumented_generator():
        try:
            # We yield lines encoded as JSON for SSE Transport
            async for chunk in reader.follow(since_sequence=since_sequence):
                json_str = chunk.model_dump_json(by_alias=True)
                yield f"data: {json_str}\n\n".encode("utf-8")
                
                # Check terminal status once every few chunks if possible or dynamically,
                # but our reader naturally expires/stops if told so. We'll verify terminal 
                if is_terminal_agent_run_state(record.status):
                    # We continue generating until spool dries, then safely break
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            metrics.increment("livelogs.stream.error", tags=tags)
            raise
        finally:
            reader.stop()
            metrics.increment("livelogs.stream.disconnect", tags=tags)

    return StreamingResponse(
        _instrumented_generator(),
        media_type="text/event-stream; charset=utf-8",
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

        # Synthesize merged tail from stdout + stderr when no pre-built artifact exists.
        if target_ref is None:
            stdout_ref = getattr(record, "stdout_artifact_ref", None)
            stderr_ref = getattr(record, "stderr_artifact_ref", None)

            if not stdout_ref and not stderr_ref:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Merged log artifact not found and no stdout/stderr artifacts available to synthesize from",
                )

            artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()

            async def _stream_artifact_safe(ref: str | None):
                """
                Safely stream artifact contents in chunks without loading the entire file into memory.
                """
                if not ref:
                    return
                artp = (artifacts_root / ref).resolve()
                try:
                    safe = artp.is_relative_to(artifacts_root)
                except Exception:
                    safe = False
                if not safe:
                    return
                exists = await asyncio.to_thread(artp.is_file)
                if not exists:
                    return

                # Stream file contents in chunks using a background thread to avoid blocking the event loop.
                f = await asyncio.to_thread(artp.open, "rb")
                try:
                    while True:
                        chunk = await asyncio.to_thread(f.read, 8192)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    await asyncio.to_thread(f.close)

            async def merged_stream():
                first_section_emitted = False

                # Stream stdout first (if present), prefixed with a label.
                if stdout_ref:
                    header_emitted = False
                    async for chunk in _stream_artifact_safe(stdout_ref):
                        if not header_emitted:
                            yield b"--- stdout ---\n"
                            header_emitted = True
                            first_section_emitted = True
                        yield chunk

                # Then stream stderr (if present), separated by a newline and prefixed with a label.
                if stderr_ref:
                    header_emitted = False
                    async for chunk in _stream_artifact_safe(stderr_ref):
                        if not header_emitted:
                            if first_section_emitted:
                                yield b"\n"
                            yield b"--- stderr ---\n"
                            header_emitted = True
                        yield chunk

            return StreamingResponse(
                merged_stream(),
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "X-Merged-Synthesized": "true",
                },
            )

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
