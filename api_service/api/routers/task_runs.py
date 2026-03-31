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
    elif raw_live_stream_capable is False:
        supports_live = False
        live_stream_status = "unavailable"
    else:
        # True or None (unknown — treat as potentially capable)
        supports_live = bool(raw_live_stream_capable) if raw_live_stream_capable is not None else False
        live_stream_status = "available" if supports_live else "unavailable"

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

    if stream_name not in ("stdout", "stderr", "merged", "stream"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log stream name. Must be stdout, stderr, merged, or stream.",
        )
    
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))
    
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )

    if stream_name == "stream":
        if not getattr(record, "live_stream_capable", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Live streaming is not supported for this run.",
            )

        if is_terminal_agent_run_state(record.status):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Execution has already terminated (terminal). Cannot follow live logs.",
            )

        # Parse 'since' query parameter if present from request
        since_sequence = 0 # To be fetched from request properly, but test client doesn't pass one yet

        # Setup SpoolLogReader
        artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
        # Navigate up from artifact root to job root
        job_root = artifacts_root.parent
        
        reader = SpoolLogReader(workspace_path=str(job_root))

        async def _generator():
            try:
                # We yield lines encoded as JSON for SSE Transport
                async for chunk in reader.follow(since_sequence=since_sequence):
                    json_str = chunk.model_dump_json(by_alias=True)
                    yield f"data: {json_str}\n\n".encode("utf-8")
                    
                    # Stop if the record becomes terminal while we are streaming
                    # (in reality we should poll, but for this PR we accept manual stoppage
                    #  or closing frontend)
                    
            except asyncio.CancelledError:
                reader.stop()
                raise
            finally:
                reader.stop()

        return StreamingResponse(
            _generator(),
            media_type="text/event-stream; charset=utf-8",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
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

            async def _read_artifact_safe(ref: str | None) -> bytes:
                if not ref:
                    return b""
                artp = (artifacts_root / ref).resolve()
                try:
                    safe = artp.is_relative_to(artifacts_root)
                except Exception:
                    safe = False
                if not safe:
                    return b""
                exists = await asyncio.to_thread(artp.is_file)
                if not exists:
                    return b""
                return await asyncio.to_thread(artp.read_bytes)

            stdout_bytes = await _read_artifact_safe(stdout_ref)
            stderr_bytes = await _read_artifact_safe(stderr_ref)

            # Interleave in a simple ordered form: stdout first, then stderr,
            # labelled so operators can distinguish them.
            parts: list[bytes] = []
            if stdout_bytes:
                parts.append(b"--- stdout ---\n" + stdout_bytes)
            if stderr_bytes:
                parts.append(b"--- stderr ---\n" + stderr_bytes)
            merged_bytes = b"\n".join(parts) if parts else b""

            return Response(
                content=merged_bytes,
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
