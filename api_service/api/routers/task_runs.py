import asyncio
import json
import os
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from fastapi.responses import FileResponse, StreamingResponse

from api_service.api.routers.temporal_artifacts import (
    _get_temporal_artifact_service,
    _serialize_metadata,
)
from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.schemas.temporal_artifact_models import (
    ArtifactMetadataModel,
    ArtifactRefModel,
    ArtifactSessionControlRequest,
    ArtifactSessionControlResponse,
    ArtifactSessionGroupModel,
    ArtifactSessionProjectionModel,
)
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.schemas.agent_runtime_models import RunObservabilityEvent
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore
from moonmind.workflows.temporal.runtime.paths import managed_runtime_artifact_root
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactNotFoundError,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.utils.metrics import get_metrics_emitter
from moonmind.schemas.agent_runtime_models import is_terminal_agent_run_state
from moonmind.observability.transport import SpoolLogReader

router = APIRouter(prefix="/task-runs", tags=["task_runs"])

_HISTORICAL_EVENT_CHUNK_SIZE = 65536
_OBSERVABILITY_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out"}
)


# Live Session legacy endpoints removed in Phase 6. Use /observability-summary and /logs/stream.


# Observability API endpoints

def _get_agent_runtime_store_root() -> str:
    return os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
        "managed_runs",
    )


def _get_managed_session_store_root() -> str:
    return os.path.join(
        os.environ.get("MOONMIND_AGENT_RUNTIME_STORE", "/work/agent_jobs"),
        "managed_sessions",
    )


def _get_agent_runtime_artifacts_root() -> str:
    return str(managed_runtime_artifact_root())


@lru_cache(maxsize=1)
def get_temporal_client_adapter() -> TemporalClientAdapter:
    return TemporalClientAdapter()


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


async def _require_task_run_access(task_run_id: str, user: User) -> None:
    if getattr(user, "is_superuser", False):
        return

    owner_type, owner_id = await _load_execution_owner_binding(task_run_id)
    if owner_type != "user" or owner_id != str(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this task run or its session projection.",
        )


def _session_projection_not_found() -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "session_projection_not_found",
            "message": "Managed session projection was not found for the requested task run.",
        },
    )


async def _resolve_projection_artifact(
    *,
    artifact_id: str | None,
    service: TemporalArtifactService,
) -> ArtifactMetadataModel | None:
    normalized = str(artifact_id or "").strip()
    if not normalized:
        return None
    try:
        artifact, links, pinned, read_policy = await service.get_metadata(
            artifact_id=normalized,
            principal="service:task_runs",
        )
    except (
        TemporalArtifactAuthorizationError,
        TemporalArtifactNotFoundError,
        TemporalArtifactStateError,
    ):
        return None
    return _serialize_metadata(
        artifact=artifact,
        links=links,
        pinned=pinned,
        read_policy=read_policy,
    )


async def _build_task_run_artifact_session_projection(
    *,
    task_run_id: str,
    session_id: str,
    service: TemporalArtifactService,
) -> ArtifactSessionProjectionModel | None:
    store = ManagedSessionStore(_get_managed_session_store_root())
    try:
        record = await asyncio.to_thread(store.load, session_id)
    except ValueError:
        return None
    if record is None or record.task_run_id != task_run_id:
        return None

    cache: dict[str, ArtifactMetadataModel | None] = {}

    async def _cached_metadata(artifact_id: str | None) -> ArtifactMetadataModel | None:
        normalized = str(artifact_id or "").strip()
        if not normalized:
            return None
        if normalized not in cache:
            cache[normalized] = await _resolve_projection_artifact(
                artifact_id=normalized,
                service=service,
            )
        return cache[normalized]

    groups: list[ArtifactSessionGroupModel] = []
    for group_key, title, refs in (
        (
            "runtime",
            "Runtime",
            (
                record.stdout_artifact_ref,
                record.stderr_artifact_ref,
                record.diagnostics_ref,
            ),
        ),
        (
            "continuity",
            "Continuity",
            (
                record.latest_summary_ref,
                record.latest_checkpoint_ref,
            ),
        ),
        (
            "control",
            "Control",
            (
                record.latest_control_event_ref,
                record.latest_reset_boundary_ref,
            ),
        ),
    ):
        artifacts: list[ArtifactMetadataModel] = []
        for ref in refs:
            metadata = await _cached_metadata(ref)
            if metadata is not None:
                artifacts.append(metadata)
        if artifacts:
            groups.append(
                ArtifactSessionGroupModel(
                    group_key=group_key,
                    title=title,
                    artifacts=artifacts,
                )
            )

    async def _artifact_ref(artifact_id: str | None) -> ArtifactRefModel | None:
        metadata = await _cached_metadata(artifact_id)
        if metadata is None:
            return None
        return metadata.artifact_ref

    return ArtifactSessionProjectionModel(
        task_run_id=record.task_run_id,
        session_id=record.session_id,
        session_epoch=record.session_epoch,
        grouped_artifacts=groups,
        latest_summary_ref=await _artifact_ref(record.latest_summary_ref),
        latest_checkpoint_ref=await _artifact_ref(record.latest_checkpoint_ref),
        latest_control_event_ref=await _artifact_ref(record.latest_control_event_ref),
        latest_reset_boundary_ref=await _artifact_ref(record.latest_reset_boundary_ref),
    )


def _task_run_session_workflow_id(*, task_run_id: str, runtime_id: str) -> str:
    return f"{task_run_id}:session:{runtime_id}"


def _load_task_run_session_record(task_run_id: str) -> CodexManagedSessionRecord | None:
    store_root = Path(_get_managed_session_store_root())
    if not store_root.exists():
        return None

    targeted_paths = list(store_root.glob(f"sess:{task_run_id}:*.json"))
    candidate_paths = targeted_paths if targeted_paths else store_root.rglob("*.json")

    best_record: CodexManagedSessionRecord | None = None
    best_updated_at: datetime | None = None
    for path in candidate_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = CodexManagedSessionRecord(**payload)
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            continue
        if record.task_run_id != task_run_id:
            continue
        candidate_updated_at = record.updated_at or record.started_at
        if best_record is None or (
            candidate_updated_at is not None
            and (best_updated_at is None or candidate_updated_at > best_updated_at)
        ):
            best_record = record
            best_updated_at = candidate_updated_at
    return best_record


def _build_session_snapshot(
    record: CodexManagedSessionRecord | None,
) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "sessionId": record.session_id,
        "sessionEpoch": record.session_epoch,
        "containerId": record.container_id,
        "threadId": record.thread_id,
        "activeTurnId": record.active_turn_id,
        "status": record.status,
        "latestSummaryRef": record.latest_summary_ref,
        "latestCheckpointRef": record.latest_checkpoint_ref,
        "latestControlEventRef": record.latest_control_event_ref,
        "latestResetBoundaryRef": record.latest_reset_boundary_ref,
    }


def _build_record_session_snapshot(record: object) -> dict[str, object] | None:
    session_id = str(getattr(record, "session_id", "") or "").strip()
    if not session_id:
        return None
    return {
        "sessionId": session_id,
        "sessionEpoch": getattr(record, "session_epoch", None),
        "containerId": getattr(record, "container_id", None),
        "threadId": getattr(record, "thread_id", None),
        "activeTurnId": getattr(record, "active_turn_id", None),
    }


def _iter_spool_chunks(workspace_path: str | None) -> Iterator[dict[str, object]]:
    if not workspace_path:
        return
    spool_path = (Path(workspace_path) / "live_streams.spool").resolve()
    if not spool_path.is_file():
        return

    try:
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
    except OSError:
        return


def _coerce_utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).astimezone(
            UTC
        )

    if not value:
        return None

    raw_value = str(value).strip()
    if raw_value.endswith("Z"):
        raw_value = f"{raw_value[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(raw_value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).astimezone(
        UTC
    )


def _iter_run_spool_chunks(
    workspace_path: str | None,
    *,
    started_at: object = None,
) -> Iterator[dict[str, object]]:
    run_started_at = _coerce_utc_datetime(started_at)
    filtering = run_started_at is not None
    for payload in _iter_spool_chunks(workspace_path):
        if filtering:
            chunk_started_at = _coerce_utc_datetime(payload.get("timestamp"))
            if chunk_started_at is not None and chunk_started_at < run_started_at:
                continue
            filtering = False
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
    return _iter_grouped_rendered_content(
        _iter_run_spool_chunks(workspace_path, started_at=started_at),
        header_for_item=lambda chunk: str(chunk.get("stream", "system")),
        text_for_item=lambda chunk: str(chunk.get("text", "")),
    )


def _iter_grouped_rendered_content(
    items: Iterable[dict[str, object]],
    *,
    header_for_item: Callable[[dict[str, object]], str],
    text_for_item: Callable[[dict[str, object]], str],
) -> Iterator[bytes]:
    current_stream: str | None = None
    emitted_any = False

    for item in items:
        stream = header_for_item(item)
        text = text_for_item(item)
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
    if not isinstance(ref, str):
        return None
    normalized_ref = ref.strip()
    if not normalized_ref:
        return None
    artifact_path = (artifacts_root / normalized_ref).resolve()
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


def _iter_text_chunks(
    path: Path,
    *,
    chunk_size: int = _HISTORICAL_EVENT_CHUNK_SIZE,
) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _read_json_payload(path: Path | None) -> dict[str, object] | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_sequence(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _normalize_live_event(payload: dict[str, object]) -> dict[str, object] | None:
    try:
        chunk = RunObservabilityEvent.model_validate(payload)
    except Exception:
        return None
    return chunk.model_dump(mode="json", by_alias=True, exclude_none=True)


def _iter_diagnostics_observability_events(
    diagnostics_path: Path | None,
) -> Iterator[dict[str, object]]:
    diagnostics = _read_json_payload(diagnostics_path)
    if diagnostics is None:
        return

    seen_system_annotations: set[tuple[object, object, object]] = set()
    observed_events = diagnostics.get("observability_events")
    if isinstance(observed_events, list):
        for raw_event in observed_events:
            if not isinstance(raw_event, dict):
                continue
            normalized = _normalize_live_event(raw_event)
            if normalized is not None:
                if normalized.get("kind") == "system_annotation":
                    seen_system_annotations.add(
                        (
                            normalized.get("sequence"),
                            normalized.get("timestamp"),
                            normalized.get("text"),
                        )
                    )
                yield normalized

    annotations = diagnostics.get("annotations")
    if not isinstance(annotations, list):
        return

    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        normalized = _normalize_live_event(
            {
                "sequence": annotation.get("sequence") or 0,
                "timestamp": annotation.get("timestamp")
                or datetime.now(tz=UTC).isoformat(),
                "stream": "system",
                "text": str(annotation.get("text") or ""),
                "kind": "system_annotation",
                "metadata": annotation.get("metadata") or {},
            }
        )
        if normalized is None:
            continue
        dedupe_key = (
            normalized.get("sequence"),
            normalized.get("timestamp"),
            normalized.get("text"),
        )
        if dedupe_key in seen_system_annotations:
            continue
        yield normalized


def _iter_event_journal(
    event_path: Path | None,
    *,
    run_id: str | None = None,
) -> Iterator[dict[str, object]]:
    if event_path is None or not event_path.is_file():
        return
    normalized_run_id = str(run_id or "").strip() or None
    try:
        with event_path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                normalized = _normalize_live_event(payload)
                if normalized is None:
                    continue
                payload_run_id = str(
                    normalized.get("run_id") or normalized.get("runId") or ""
                ).strip()
                if normalized_run_id and payload_run_id and payload_run_id != normalized_run_id:
                    continue
                yield normalized
    except OSError:
        return


def _iter_text_artifact_events(
    ref: str | None,
    artifacts_root: Path,
    *,
    stream: str,
    kind: str,
    timestamp: str | None,
    limit_per_stream: int | None,
) -> Iterator[dict[str, object]]:
    artifact_path = _resolve_safe_artifact_path(ref, artifacts_root)
    if artifact_path is None:
        return

    chunk_events: deque[dict[str, object]] = deque(maxlen=limit_per_stream)
    offset = 0
    normalized_timestamp = timestamp or datetime.now(tz=UTC).isoformat()
    try:
        for text_chunk in _iter_text_chunks(artifact_path):
            normalized = _normalize_live_event(
                {
                    "sequence": 0,
                    "timestamp": normalized_timestamp,
                    "stream": stream,
                    "text": text_chunk,
                    "offset": offset,
                    "kind": kind,
                }
            )
            offset += len(text_chunk.encode("utf-8", errors="replace"))
            if normalized is not None:
                chunk_events.append(normalized)
    except OSError:
        return

    yield from chunk_events


def _build_session_artifact_event(
    *,
    kind: str,
    text: str,
    timestamp: str | None,
    session_record: CodexManagedSessionRecord,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    if isinstance(timestamp, datetime):
        normalized_timestamp = timestamp.isoformat()
    else:
        normalized_timestamp = str(timestamp or (session_record.updated_at or session_record.started_at).isoformat())
    payload = {
        "sequence": 0,
        "timestamp": normalized_timestamp,
        "stream": "session",
        "text": text,
        "kind": kind,
        "session_id": session_record.session_id,
        "session_epoch": session_record.session_epoch,
        "container_id": session_record.container_id,
        "thread_id": session_record.thread_id,
        "active_turn_id": session_record.active_turn_id,
        "metadata": metadata or {},
    }
    normalized = _normalize_live_event(payload)
    if normalized is None:
        raise ValueError("failed to normalize session artifact event")
    return normalized


def _iter_historical_artifact_events(
    record: object,
    session_record: CodexManagedSessionRecord | None,
    *,
    limit_per_stream: int | None = None,
) -> Iterator[dict[str, object]]:
    artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
    diagnostics_path = _resolve_safe_artifact_path(
        getattr(record, "diagnostics_ref", None),
        artifacts_root,
    )
    yield from _iter_diagnostics_observability_events(diagnostics_path)

    stdout_timestamp = getattr(record, "started_at", None)
    if isinstance(stdout_timestamp, datetime):
        stdout_timestamp = stdout_timestamp.isoformat()
    yield from _iter_text_artifact_events(
        getattr(record, "stdout_artifact_ref", None),
        artifacts_root,
        stream="stdout",
        kind="stdout_chunk",
        timestamp=stdout_timestamp,
        limit_per_stream=limit_per_stream,
    )

    stderr_timestamp = getattr(record, "started_at", None)
    if isinstance(stderr_timestamp, datetime):
        stderr_timestamp = stderr_timestamp.isoformat()
    yield from _iter_text_artifact_events(
        getattr(record, "stderr_artifact_ref", None),
        artifacts_root,
        stream="stderr",
        kind="stderr_chunk",
        timestamp=stderr_timestamp,
        limit_per_stream=limit_per_stream,
    )

    if session_record is None:
        return

    yield _build_session_artifact_event(
        kind="session_started",
        text=(
            f"Session started. Epoch {session_record.session_epoch} "
            f"thread {session_record.thread_id}."
        ),
        timestamp=session_record.started_at.isoformat(),
        session_record=session_record,
        metadata={"status": session_record.status},
    )

    control_artifact_path = _resolve_safe_artifact_path(
        session_record.latest_control_event_ref,
        artifacts_root,
    )
    reset_boundary_artifact_path = _resolve_safe_artifact_path(
        session_record.latest_reset_boundary_ref,
        artifacts_root,
    )

    control_payload = _read_json_payload(control_artifact_path)
    if control_payload is not None:
        action = str(control_payload.get("action") or "").strip().lower()
        if action == "clear_session":
            control_metadata = {
                **control_payload,
                "artifactRef": session_record.latest_control_event_ref,
                "controlEventRef": session_record.latest_control_event_ref,
            }
            if reset_boundary_artifact_path is not None:
                control_metadata["resetBoundaryRef"] = session_record.latest_reset_boundary_ref
            yield _build_session_artifact_event(
                kind="session_cleared",
                text=(
                    f"Session cleared. Epoch {control_payload.get('previousSessionEpoch')} "
                    f"-> {control_payload.get('newSessionEpoch')}; thread "
                    f"{control_payload.get('previousThreadId')} -> {control_payload.get('newThreadId')}."
                ),
                timestamp=str(control_payload.get("recordedAt") or ""),
                session_record=session_record,
                metadata=control_metadata,
            )

    boundary_payload = _read_json_payload(reset_boundary_artifact_path)
    if boundary_payload is not None:
        boundary_metadata = {
            **boundary_payload,
            "artifactRef": session_record.latest_reset_boundary_ref,
            "resetBoundaryRef": session_record.latest_reset_boundary_ref,
        }
        if control_artifact_path is not None:
            boundary_metadata["controlEventRef"] = session_record.latest_control_event_ref
        yield _build_session_artifact_event(
            kind="session_reset_boundary",
            text=(
                f"Epoch boundary reached. Session {session_record.session_id} is now on "
                f"epoch {boundary_payload.get('sessionEpoch')} thread {boundary_payload.get('threadId')}."
            ),
            timestamp=str(boundary_payload.get("recordedAt") or ""),
            session_record=session_record,
            metadata=boundary_metadata,
        )

    for kind, ref_attr, label, ref_key in (
        (
            "summary_published",
            session_record.latest_summary_ref,
            "Session summary published.",
            "summaryRef",
        ),
        (
            "checkpoint_published",
            session_record.latest_checkpoint_ref,
            "Session checkpoint published.",
            "checkpointRef",
        ),
    ):
        artifact_path = _resolve_safe_artifact_path(ref_attr, artifacts_root)
        if artifact_path is None:
            continue
        yield _build_session_artifact_event(
            kind=kind,
            text=label,
            timestamp=(session_record.updated_at or session_record.started_at).isoformat(),
            session_record=session_record,
            metadata={"artifactRef": ref_attr, ref_key: ref_attr},
        )


def _load_task_run_observability_events(
    *,
    record: object,
    session_record: CodexManagedSessionRecord | None,
    limit: int,
    since: int | None = None,
    streams: set[str] | None = None,
    kinds: set[str] | None = None,
) -> tuple[list[dict[str, object]], str]:
    workspace_path = getattr(record, "workspace_path", None)
    started_at = getattr(record, "started_at", None)

    events: list[dict[str, object]] = []
    artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
    event_journal_path = _resolve_safe_artifact_path(
        getattr(record, "observability_events_ref", None),
        artifacts_root,
    )
    raw_record_run_id = getattr(record, "run_id", None)
    record_run_id = (
        raw_record_run_id.strip()
        if isinstance(raw_record_run_id, str) and raw_record_run_id.strip()
        else None
    )
    if event_journal_path is not None:
        events.extend(
            _collect_matching_observability_events(
                _iter_event_journal(event_journal_path, run_id=record_run_id),
                limit=limit,
                since=since,
                streams=streams,
                kinds=kinds,
            )
        )
        source = "journal"
    elif _spool_contains_renderable_chunks(workspace_path, started_at=started_at):
        normalized_events = (
            normalized
            for payload in _iter_run_spool_chunks(workspace_path, started_at=started_at)
            if (normalized := _normalize_live_event(payload)) is not None
        )
        events.extend(
            _collect_matching_observability_events(
                normalized_events,
                limit=limit,
                since=since,
                streams=streams,
                kinds=kinds,
            )
        )
        source = "spool"
    else:
        events.extend(
            _iter_historical_artifact_events(
                record,
                session_record,
                limit_per_stream=limit,
            )
        )
        source = "artifacts"
    return events, source


def _event_matches_observability_filters(
    event: dict[str, object],
    *,
    since: int | None = None,
    streams: set[str] | None = None,
    kinds: set[str] | None = None,
) -> bool:
    sequence = _coerce_sequence(event.get("sequence"))
    if since is not None and sequence is not None and sequence > 0 and sequence <= since:
        return False
    stream = str(event.get("stream") or "").strip()
    if streams and stream not in streams:
        return False
    kind = str(event.get("kind") or "").strip()
    if kinds and kind not in kinds:
        return False
    return True


def _collect_matching_observability_events(
    events: Iterator[dict[str, object]],
    *,
    limit: int,
    since: int | None = None,
    streams: set[str] | None = None,
    kinds: set[str] | None = None,
) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    max_events = max(1, limit) + 1
    for event in events:
        if not _event_matches_observability_filters(
            event,
            since=since,
            streams=streams,
            kinds=kinds,
        ):
            continue
        collected.append(event)
        if len(collected) >= max_events:
            break
    return collected


def _filter_observability_events(
    events: list[dict[str, object]],
    *,
    since: int | None = None,
    streams: set[str] | None = None,
    kinds: set[str] | None = None,
) -> list[dict[str, object]]:
    filtered: list[dict[str, object]] = []
    for event in events:
        if not _event_matches_observability_filters(
            event,
            since=since,
            streams=streams,
            kinds=kinds,
        ):
            continue
        filtered.append(event)
    return filtered


def _event_sort_key(payload: dict[str, object]) -> tuple[datetime, int]:
    sequence = _coerce_sequence(payload.get("sequence"))
    timestamp = _coerce_utc_datetime(payload.get("timestamp")) or datetime.min.replace(tzinfo=UTC)
    return (timestamp, sequence if sequence is not None and sequence > 0 else 2**31 - 1)


def _merged_event_sort_key(payload: dict[str, object]) -> tuple[int, int | datetime, datetime]:
    sequence = _coerce_sequence(payload.get("sequence"))
    timestamp = _coerce_utc_datetime(payload.get("timestamp")) or datetime.min.replace(tzinfo=UTC)
    if sequence is not None and sequence > 0:
        return (0, sequence, timestamp)
    return (1, timestamp, timestamp)


def _merged_event_header(payload: dict[str, object]) -> str:
    stream = str(payload.get("stream") or "system").strip() or "system"
    kind = str(payload.get("kind") or "").strip()
    if stream == "session" and kind:
        return f"{stream} ({kind})"
    return stream


def _iter_event_rendered_content(events: Iterable[dict[str, object]]) -> Iterator[bytes]:
    return _iter_grouped_rendered_content(
        (event for event in events if str(event.get("text") or "")),
        header_for_item=_merged_event_header,
        text_for_item=lambda event: str(event.get("text") or ""),
    )


def _iter_merged_journal_events(
    record: object,
    artifacts_root: Path,
) -> Iterator[dict[str, object]]:
    yield from _load_merged_journal_events(record, artifacts_root)


def _load_merged_journal_events(
    record: object,
    artifacts_root: Path,
) -> list[dict[str, object]]:
    event_journal_path = _resolve_safe_artifact_path(
        getattr(record, "observability_events_ref", None),
        artifacts_root,
    )
    if event_journal_path is None:
        return []

    raw_record_run_id = getattr(record, "run_id", None)
    record_run_id = (
        raw_record_run_id.strip()
        if isinstance(raw_record_run_id, str) and raw_record_run_id.strip()
        else None
    )
    events = [
        event
        for event in _iter_event_journal(event_journal_path, run_id=record_run_id)
        if str(event.get("text") or "")
    ]
    events.sort(key=_merged_event_sort_key)
    return events


def _merged_journal_has_renderable_events(record: object, artifacts_root: Path) -> bool:
    return bool(_load_merged_journal_events(record, artifacts_root))


def _emit_livelogs_metric_increment(
    metric: str,
    *,
    value: int = 1,
    tags: dict[str, object] | None = None,
) -> None:
    try:
        get_metrics_emitter().increment(metric, value=value, tags=tags)
    except Exception:
        return


def _emit_livelogs_metric_observe(
    metric: str,
    *,
    value: float,
    tags: dict[str, object] | None = None,
) -> None:
    try:
        get_metrics_emitter().observe(metric, value=value, tags=tags)
    except Exception:
        return


def _iter_artifact_fallback_content(
    stdout_path: Path | None,
    stderr_path: Path | None,
    diagnostics_path: Path | None = None,
) -> Iterator[bytes]:
    annotations = list(_iter_diagnostics_annotations(diagnostics_path))

    yield b"--- system ---\n"
    yield b"[merged-order unavailable: spool metadata missing]\n"
    for annotation in annotations:
        yield f"{annotation}\n".encode("utf-8")

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


def _iter_diagnostics_annotations(
    diagnostics_path: Path | None,
) -> Iterator[str]:
    if diagnostics_path is None or not diagnostics_path.is_file():
        return
    try:
        raw = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return

    annotations = raw.get("annotations")
    if not isinstance(annotations, list):
        return

    def _sort_key(annotation: object) -> int:
        if not isinstance(annotation, dict):
            return 2**31 - 1
        sequence = annotation.get("sequence")
        return int(sequence) if isinstance(sequence, int) else 2**31 - 1

    for annotation in sorted(annotations, key=_sort_key):
        if not isinstance(annotation, dict):
            continue
        text = str(annotation.get("text") or "").strip()
        if not text:
            continue
        sequence = annotation.get("sequence")
        if isinstance(sequence, int):
            yield f"[sequence={sequence}] {text}"
        else:
            yield text


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
    started = time.perf_counter()

    try:
        record = await asyncio.to_thread(store.load, str(id))
        if not record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Observability record not found for this task run",
            )
        await _require_observability_access(record, _user)

        run_status = getattr(record, "status", None)
        is_terminal = run_status in _OBSERVABILITY_TERMINAL_STATUSES

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
        session_record = await asyncio.to_thread(_load_task_run_session_record, str(id))
        base["supportsLiveStreaming"] = supports_live
        base["liveStreamStatus"] = live_stream_status
        base["sessionSnapshot"] = _build_session_snapshot(session_record) or _build_record_session_snapshot(record)
        return {"summary": base}
    finally:
        _emit_livelogs_metric_observe(
            "livelogs.summary.latency",
            value=time.perf_counter() - started,
            tags={"stream": "livelogs"},
        )


@router.get(
    "/{task_run_id}/artifact-sessions/{session_id}",
    response_model=ArtifactSessionProjectionModel,
    responses={
        403: {"description": "You do not have permission to access this task run"},
        404: {"description": "Session projection not found for this task run"},
    },
)
async def get_task_run_artifact_session(
    task_run_id: str,
    session_id: str,
    _user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactSessionProjectionModel:
    await _require_task_run_access(task_run_id, _user)
    projection = await _build_task_run_artifact_session_projection(
        task_run_id=task_run_id,
        session_id=session_id,
        service=artifact_service,
    )
    if projection is None:
        _session_projection_not_found()
    return projection


@router.post(
    "/{task_run_id}/artifact-sessions/{session_id}/control",
    response_model=ArtifactSessionControlResponse,
    responses={
        403: {"description": "You do not have permission to access this task run"},
        404: {"description": "Session projection not found for this task run"},
        409: {"description": "The managed session cannot accept this control action"},
    },
)
async def control_task_run_artifact_session(
    task_run_id: str,
    session_id: str,
    payload: ArtifactSessionControlRequest,
    _user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
) -> ArtifactSessionControlResponse:
    await _require_task_run_access(task_run_id, _user)
    store = ManagedSessionStore(_get_managed_session_store_root())
    try:
        record = await asyncio.to_thread(store.load, session_id)
    except ValueError:
        record = None
    if record is None or record.task_run_id != task_run_id:
        _session_projection_not_found()
    if record.status in {"terminated", "degraded", "failed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This managed session is not available for control actions.",
        )

    client = get_temporal_client_adapter()
    workflow_id = _task_run_session_workflow_id(
        task_run_id=task_run_id,
        runtime_id=record.runtime_id,
    )
    if payload.action == "send_follow_up":
        await client.update_workflow(
            workflow_id,
            "SendFollowUp",
            {
                "message": payload.message,
                **({"reason": payload.reason} if payload.reason else {}),
            },
        )
    elif payload.action == "clear_session":
        await client.update_workflow(
            workflow_id,
            "ClearSession",
            {
                **({"reason": payload.reason} if payload.reason else {}),
            },
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported session control action: {payload.action}",
        )

    projection = await _build_task_run_artifact_session_projection(
        task_run_id=task_run_id,
        session_id=session_id,
        service=artifact_service,
    )
    if projection is None:
        _session_projection_not_found()
    return ArtifactSessionControlResponse(action=payload.action, projection=projection)


@router.get(
    "/{id}/observability/events",
    response_model=dict,
    responses={
        404: {"description": "Observability record not found for this task run"},
    },
)
async def get_task_run_observability_events(
    id: UUID,
    since: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=5000),
    stream: list[Literal["stdout", "stderr", "system", "session"]] | None = Query(default=None),
    kind: list[str] | None = Query(default=None),
    _user: User = Depends(get_current_user()),
) -> dict:
    """Return structured observability history for one task run."""
    store = ManagedRunStore(_get_agent_runtime_store_root())
    record = await asyncio.to_thread(store.load, str(id))
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Observability record not found for this task run",
        )
    await _require_observability_access(record, _user)
    started = time.perf_counter()
    stream_filters = set(stream or [])
    kind_filters = {item for item in (kind or []) if item}
    try:
        session_record = await asyncio.to_thread(_load_task_run_session_record, str(id))
        events, source = await asyncio.to_thread(
            _load_task_run_observability_events,
            record=record,
            session_record=session_record,
            limit=limit,
            since=since,
            streams=stream_filters,
            kinds=kind_filters,
        )
        if source == "artifacts":
            events = _filter_observability_events(
                events,
                since=since,
                streams=stream_filters,
                kinds=kind_filters,
            )
        events.sort(key=_event_sort_key)
        truncated = len(events) > limit
        if truncated:
            events = events[:limit]

        response = {
            "events": events,
            "truncated": truncated,
            "sessionSnapshot": _build_session_snapshot(session_record)
            or _build_record_session_snapshot(record),
        }
    except Exception:
        _emit_livelogs_metric_increment(
            "livelogs.history.error",
            tags={"stream": "livelogs"},
        )
        raise
    metric_tags = {"stream": "livelogs", "source": source}
    _emit_livelogs_metric_observe(
        "livelogs.history.latency",
        value=time.perf_counter() - started,
        tags=metric_tags,
    )
    _emit_livelogs_metric_increment("livelogs.history.source", tags=metric_tags)

    return response


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
    if getattr(record, "status", None) in _OBSERVABILITY_TERMINAL_STATUSES:
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

    tags = {"stream": "livelogs"}
    _emit_livelogs_metric_increment("livelogs.stream.connect", tags=tags)

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
                json_str = chunk.model_dump_json(by_alias=True, exclude_none=True)
                yield f"data: {json_str}\n\n".encode("utf-8")
                
                # Check terminal status once every few chunks if possible or dynamically,
                # but our reader naturally expires/stops if told so. We'll verify terminal 
                if is_terminal_agent_run_state(record.status):
                    # We continue generating until spool dries, then safely break
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            _emit_livelogs_metric_increment("livelogs.stream.error", tags=tags)
            raise
        finally:
            reader.stop()
            _emit_livelogs_metric_increment("livelogs.stream.disconnect", tags=tags)

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
        artifacts_root = Path(_get_agent_runtime_artifacts_root()).resolve()
        workspace_path = getattr(record, "workspace_path", None)
        started_at = getattr(record, "started_at", None)

        journal_events = _load_merged_journal_events(record, artifacts_root)
        if journal_events:
            content_stream = _iter_event_rendered_content(journal_events)
            order_source = "journal"
        elif _spool_contains_renderable_chunks(
            workspace_path,
            started_at=started_at,
        ):
            content_stream = _iter_spool_rendered_content(
                workspace_path,
                started_at=started_at,
            )
            order_source = "spool"
        else:
            merged_log_path = _resolve_safe_artifact_path(
                getattr(record, "merged_log_artifact_ref", None),
                artifacts_root,
            )
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
            diagnostics_path = _resolve_safe_artifact_path(
                getattr(record, "diagnostics_ref", None),
                artifacts_root,
            )
            if merged_log_path is not None:
                return FileResponse(
                    path=merged_log_path,
                    media_type="text/plain",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0",
                    },
                )
            elif legacy_log_path is not None:
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
                    diagnostics_path=diagnostics_path,
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
