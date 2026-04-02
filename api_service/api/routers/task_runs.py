import asyncio
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import FileResponse, StreamingResponse

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
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
    return str(managed_runtime_artifact_root())


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _coerce_owner_id(value: object) -> str | None:
    if isinstance(value, (list, tuple)):
        for item in value:
            candidate = _coerce_owner_id(item)
            if candidate:
                return candidate
        return None
    candidate = str(value or "").strip()
    return candidate or None


async def _load_execution_owner_binding(
    workflow_id: str,
) -> tuple[str | None, str | None]:
    from api_service.db.base import get_async_session_context
    from api_service.db.models import TemporalExecutionCanonicalRecord

    normalized_workflow_id = str(workflow_id or "").strip()
    if not normalized_workflow_id:
        return None, None

    async with get_async_session_context() as db:
        record = await db.get(TemporalExecutionCanonicalRecord, normalized_workflow_id)
        if record is None:
            return None, None

        search_attributes = dict(getattr(record, "search_attributes", None) or {})
        owner_type = str(_enum_value(getattr(record, "owner_type", None)) or "").strip().lower()
        owner_id = str(getattr(record, "owner_id", "") or "").strip()
        if not owner_id:
            owner_id = _coerce_owner_id(search_attributes.get("mm_owner_id")) or ""
        if not owner_type:
            owner_type = "system" if owner_id.lower() == "system" or not owner_id else "user"
        return owner_type or None, owner_id or None


async def _require_observability_access(record: object, user: User) -> None:
    if getattr(user, "is_superuser", False):
        return

    workflow_id = str(getattr(record, "workflow_id", "") or "").strip()
    if not workflow_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access observability for this run.",
        )

    owner_type, owner_id = await _load_execution_owner_binding(workflow_id)
    if owner_type != "user" or owner_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access observability for this run.",
        )


def _iter_spool_chunks(workspace_path: str | None) -> Iterator[dict[str, object]]:
    if not workspace_path:
        return
    spool_path = (Path(workspace_path) / "live_streams.spool").resolve()
    if not spool_path.is_file():
        return

    with spool_path.open("r", encoding="utf-8", errors="replace") as spool_file:
        for raw_line in spool_file:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            sequence = payload.get("sequence")
            if not isinstance(sequence, int):
                continue
            yield payload


def _coerce_utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    text = str(value or "").strip()
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iter_run_spool_chunks(
    workspace_path: str | None,
    *,
    started_at: object = None,
) -> Iterator[dict[str, object]]:
    run_started_at = _coerce_utc_datetime(started_at)
    for payload in _iter_spool_chunks(workspace_path):
        if run_started_at is not None:
            chunk_started_at = _coerce_utc_datetime(payload.get("timestamp"))
            if chunk_started_at is not None and chunk_started_at < run_started_at:
                continue
        yield payload


def _spool_contains_renderable_chunks(
    workspace_path: str | None,
    *,
    started_at: object = None,
) -> bool:
    return next(
        _iter_run_spool_chunks(workspace_path, started_at=started_at),
        None,
    ) is not None


def _iter_spool_rendered_content(
    workspace_path: str | None,
    *,
    started_at: object = None,
) -> Iterator[bytes]:
    current_stream: str | None = None
    emitted_any = False

    for chunk in _iter_run_spool_chunks(workspace_path, started_at=started_at):
        stream = str(chunk.get("stream", "system"))
        text = str(chunk.get("text", ""))
        if stream != current_stream:
            if emitted_any and not text.startswith("\n"):
                yield b"\n"
            yield f"--- {stream} ---\n".encode("utf-8")
            current_stream = stream
        if text:
            yield text.encode("utf-8")
            emitted_any = True
            if not text.endswith("\n"):
                yield b"\n"


def _resolve_safe_artifact_path(ref: str | None, artifacts_root: Path) -> Path | None:
    if not ref:
        return None
    artifact_path = (artifacts_root / ref).resolve()
    try:
        is_safe = artifact_path.is_relative_to(artifacts_root)
    except Exception:
        is_safe = False
    if not is_safe or not artifact_path.is_file():
        return None
    return artifact_path


def _iter_file_chunks(path: Path, *, chunk_size: int = 8192) -> Iterator[bytes]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk.encode("utf-8")


def _iter_artifact_fallback_content(
    stdout_path: Path | None,
    stderr_path: Path | None,
) -> Iterator[bytes]:
    yield b"--- system ---\n"
    yield b"[merged-order unavailable: spool metadata missing]\n"

    for stream_name, artifact_path in (("stdout", stdout_path), ("stderr", stderr_path)):
        if artifact_path is None:
            continue
        yield f"--- {stream_name} ---\n".encode("utf-8")
        last_chunk: bytes | None = None
        for chunk in _iter_file_chunks(artifact_path):
            last_chunk = chunk
            yield chunk
        if last_chunk is not None and not last_chunk.endswith(b"\n"):
            yield b"\n"


def _resolve_legacy_log_artifact_path(
    record: object,
    artifacts_root: Path,
) -> Path | None:
    """Return the pre-Phase-2 combined log artifact for historical runs."""

    return _resolve_safe_artifact_path(
        getattr(record, "log_artifact_ref", None),
        artifacts_root,
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
    
    record = await asyncio.to_thread(store.load, str(id))
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )
    await _require_observability_access(record, _user)

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
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )
    await _require_observability_access(record, _user)

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
    start_at_end = since is None

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
            async for chunk in reader.follow(
                since_sequence=since_sequence,
                start_at_end=start_at_end,
            ):
                if await request.is_disconnected():
                    break
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
    await _require_observability_access(record, _user)

    if stream_name == "merged":
        target_ref = getattr(record, "merged_log_artifact_ref", None)

        # Synthesize merged tail from stdout + stderr when no pre-built artifact exists.
        if target_ref is None:
            artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
            workspace_path = getattr(record, "workspace_path", None)
            started_at = getattr(record, "started_at", None)
            if _spool_contains_renderable_chunks(
                workspace_path,
                started_at=started_at,
            ):
                content_stream = _iter_spool_rendered_content(
                    workspace_path,
                    started_at=started_at,
                )
                order_source = "spool"
            else:
                stdout_path = _resolve_safe_artifact_path(
                    getattr(record, "stdout_artifact_ref", None),
                    artifacts_root,
                )
                stderr_path = _resolve_safe_artifact_path(
                    getattr(record, "stderr_artifact_ref", None),
                    artifacts_root,
                )
                legacy_log_path = _resolve_legacy_log_artifact_path(
                    record,
                    artifacts_root,
                )
                if legacy_log_path is not None:
                    content_stream = _iter_file_chunks(legacy_log_path)
                    order_source = "legacy-log-artifact"
                elif not stdout_path and not stderr_path:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=(
                            "Merged log artifact not found and no stdout/stderr or "
                            "legacy log artifacts are available to synthesize from"
                        ),
                    )
                else:
                    content_stream = _iter_artifact_fallback_content(
                        stdout_path,
                        stderr_path,
                    )
                    order_source = "artifact-fallback"

            return StreamingResponse(
                content_stream,
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "X-Merged-Synthesized": "true",
                    "X-Merged-Order-Source": order_source,
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
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))
    
    if not record or not record.diagnostics_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Diagnostics artifact not found",
        )
    await _require_observability_access(record, _user)
        
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
