"""API endpoints for interacting with Spec Kit Celery workflows."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    CodexPreflightRequest,
    CodexPreflightResultModel,
    CodexShardHealthModel,
    CodexShardListResponse,
    CreateWorkflowRunRequest,
    RetryWorkflowMode,
    RetryWorkflowRunRequest,
    SpecWorkflowRunModel,
    WorkflowArtifactListResponse,
    WorkflowRunCollectionResponse,
    WorkflowTaskStateListResponse,
)
from moonmind.workflows import (
    SpecWorkflowRepository,
    TriggeredWorkflow,
    WorkflowConflictError,
    WorkflowRetryError,
    get_spec_workflow_repository,
    retry_spec_workflow_run,
    trigger_spec_workflow_run,
)
from moonmind.workflows.speckit_celery import models
from moonmind.workflows.speckit_celery.celeryconfig import get_codex_shard_router
from moonmind.workflows.speckit_celery.serializers import (
    serialize_artifact_collection,
    serialize_run,
    serialize_task_collection,
)
from moonmind.workflows.speckit_celery.tasks import run_codex_preflight_check

router = APIRouter(prefix="/api/workflows/speckit", tags=["speckit-workflows"])


_AFFINITY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _ensure_utc_timestamp(timestamp: datetime | None) -> datetime:
    if timestamp is None:
        return datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _normalize_affinity_key(raw: str | None) -> str | None:
    """Validate and normalize a user supplied affinity key."""

    if raw is None:
        return None

    candidate = raw.strip()
    if not candidate:
        return None

    if not _AFFINITY_KEY_PATTERN.fullmatch(candidate):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_affinity_key",
                "message": (
                    "affinityKey must be 1-128 characters and contain only "
                    "letters, numbers, period, underscore, colon, or hyphen."
                ),
            },
        )

    return candidate


async def _get_repository(
    session: AsyncSession = Depends(get_async_session),
) -> SpecWorkflowRepository:
    return get_spec_workflow_repository(session)


def _serialize_run_model(
    run,
    *,
    include_tasks: bool = True,
    include_artifacts: bool = True,
    include_credential_audit: bool = True,
    task_states: Iterable[models.SpecWorkflowTaskState] | None = None,
) -> SpecWorkflowRunModel:
    serialized = serialize_run(
        run,
        include_tasks=include_tasks,
        include_artifacts=include_artifacts,
        include_credential_audit=include_credential_audit,
        task_states=task_states,
    )
    return SpecWorkflowRunModel.model_validate(serialized)


@router.get("/codex/shards", response_model=CodexShardListResponse)
async def list_codex_shards(
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> CodexShardListResponse:
    """Return Codex shard health and associated volume metadata."""

    shard_health = await repo.list_codex_shard_health()
    shards = [
        CodexShardHealthModel(
            queue_name=entry.queue_name,
            status=entry.shard_status,
            hash_modulo=entry.hash_modulo,
            worker_hostname=entry.worker_hostname,
            volume_name=entry.volume_name,
            volume_status=entry.volume_status,
            volume_last_verified_at=entry.volume_last_verified_at,
            volume_worker_affinity=entry.volume_worker_affinity,
            volume_notes=entry.volume_notes,
            latest_run_id=entry.latest_run_id,
            latest_run_status=entry.latest_run_status,
            latest_preflight_status=entry.latest_preflight_status,
            latest_preflight_message=entry.latest_preflight_message,
            latest_preflight_checked_at=entry.latest_preflight_checked_at,
        )
        for entry in shard_health
    ]
    return CodexShardListResponse(shards=shards)


@router.post(
    "/runs/{run_id}/codex/preflight",
    response_model=CodexPreflightResultModel,
)
async def trigger_codex_preflight(
    run_id: UUID,
    payload: CodexPreflightRequest,
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> CodexPreflightResultModel:
    """Run the Codex login status check for the specified workflow run."""

    run = await repo.get_run(run_id, with_relations=True)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "workflow_not_found",
                "message": f"Workflow run {run_id} was not found",
            },
        )

    router = get_codex_shard_router()
    queue_name = run.codex_queue
    shard = run.codex_shard
    volume_name = run.codex_volume
    affinity_key = _normalize_affinity_key(payload.affinity_key)

    if not queue_name:
        affinity_source = (
            affinity_key or run.feature_key or (run.codex_task_id or str(run.id))
        )
        queue_name = router.queue_for_key(affinity_source)

    if shard is None or (queue_name and shard.queue_name != queue_name):
        shard = await repo.get_codex_shard(queue_name or "", with_volume=True)

    if volume_name is None:
        if run.codex_auth_volume is not None:
            volume_name = run.codex_auth_volume.name
        elif shard is not None and shard.volume_name:
            volume_name = shard.volume_name
        elif settings.spec_workflow.codex_volume_name:
            volume_name = settings.spec_workflow.codex_volume_name

    if not volume_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "codex_volume_missing",
                "message": "Codex auth volume could not be resolved for the requested run.",
            },
        )

    reuse_preflight = (
        not payload.force_refresh
        and run.codex_preflight_status is not None
        and run.codex_volume == volume_name
    )

    if reuse_preflight:
        preflight_status = run.codex_preflight_status
        preflight_message = run.codex_preflight_message
        checked_at = _ensure_utc_timestamp(run.updated_at)
    else:
        checked_at = datetime.now(UTC)
        preflight_result = await asyncio.to_thread(
            run_codex_preflight_check, volume_name=volume_name
        )
        preflight_status = preflight_result.status
        preflight_message = preflight_result.message

    updates: dict[str, object] = {}
    if queue_name and queue_name != run.codex_queue:
        updates["codex_queue"] = queue_name
    if volume_name and volume_name != run.codex_volume:
        updates["codex_volume"] = volume_name

    if not reuse_preflight:
        updates.update(
            {
                "codex_preflight_status": preflight_status,
                "codex_preflight_message": preflight_message,
            }
        )

    if updates:
        await repo.update_run(run_id, **updates)

    if volume_name and not reuse_preflight:
        volume_updates: dict[str, object] = {}
        if preflight_status is models.CodexPreflightStatus.PASSED:
            volume_updates["status"] = models.CodexAuthVolumeStatus.READY
            volume_updates["last_verified_at"] = checked_at
        elif preflight_status is models.CodexPreflightStatus.FAILED:
            volume_updates["status"] = models.CodexAuthVolumeStatus.NEEDS_AUTH
        if volume_updates:
            updated_volume = await repo.update_codex_auth_volume(
                volume_name, **volume_updates
            )
            if updated_volume is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "codex_volume_not_registered",
                        "message": (
                            "The resolved Codex auth volume is no longer registered. "
                            "Re-authenticate the volume before retrying."
                        ),
                    },
                )

    await repo.commit()

    return CodexPreflightResultModel(
        run_id=run.id,
        queue_name=queue_name,
        volume_name=volume_name,
        status=preflight_status,
        checked_at=checked_at,
        message=preflight_message,
    )


@router.post(
    "/runs",
    response_model=SpecWorkflowRunModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_workflow_run(
    payload: CreateWorkflowRunRequest,
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> SpecWorkflowRunModel:
    """Trigger a new workflow run for the requested feature."""

    feature_key = (payload.feature_key or "").strip() or None
    repository = payload.repository.strip() or None
    authenticated_user_id = _user.id if _user else None
    created_by = authenticated_user_id if authenticated_user_id else payload.created_by
    requested_by_user_id = authenticated_user_id

    try:
        triggered: TriggeredWorkflow = await trigger_spec_workflow_run(
            feature_key=feature_key,
            created_by=created_by,
            requested_by_user_id=requested_by_user_id,
            force_phase=payload.force_phase,
            repository=repository,
        )
    except WorkflowConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "workflow_conflict",
                "message": str(exc),
                "runId": str(exc.run_id),
            },
        ) from exc

    refreshed = await repo.get_run(triggered.run_id, with_relations=True)
    run = refreshed or triggered.run

    return _serialize_run_model(run)


@router.get("/runs", response_model=WorkflowRunCollectionResponse)
async def list_workflow_runs(
    *,
    status: Optional[str] = Query(None, alias="status"),
    feature_key: Optional[str] = Query(None, alias="featureKey"),
    created_by: Optional[UUID] = Query(None, alias="createdBy"),
    limit: int = Query(25, ge=1, le=100),
    cursor: Optional[str] = Query(None, alias="cursor"),
    include_tasks: bool = Query(False, alias="includeTasks"),
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> WorkflowRunCollectionResponse:
    """Return workflow runs filtered by query parameters."""

    status_enum = None
    if status is not None:
        try:
            status_enum = models.SpecWorkflowRunStatus(status)
        except ValueError as exc:  # pragma: no cover - FastAPI should validate enum
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_status", "message": str(exc)},
            ) from exc

    try:
        paginated_runs = await repo.list_runs(
            status=status_enum,
            feature_key=feature_key,
            created_by=created_by,
            cursor=cursor,
            limit=limit,
            with_relations=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_cursor",
                "message": "Invalid cursor token",
            },
        ) from exc

    items_source = (
        paginated_runs.items
        if hasattr(paginated_runs, "items")
        else list(paginated_runs)
    )
    task_state_map = await repo.list_task_states_for_runs(
        run.id for run in items_source
    )
    items = [
        _serialize_run_model(
            run,
            include_tasks=include_tasks,
            include_artifacts=False,
            include_credential_audit=False,
            task_states=task_state_map.get(run.id, []),
        )
        for run in items_source
    ]
    return WorkflowRunCollectionResponse(
        items=items,
        nextCursor=getattr(paginated_runs, "next_cursor", None),
    )


@router.get("/runs/{run_id}", response_model=SpecWorkflowRunModel)
async def get_workflow_run(
    run_id: UUID,
    include_tasks: bool = Query(True, alias="includeTasks"),
    include_artifacts: bool = Query(True, alias="includeArtifacts"),
    include_credential_audit: bool = Query(True, alias="includeCredentialAudit"),
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> SpecWorkflowRunModel:
    """Retrieve a single workflow run with related metadata."""

    run = await repo.get_run(run_id, with_relations=True)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "workflow_not_found",
                "message": f"Workflow run {run_id} was not found",
            },
        )

    return _serialize_run_model(
        run,
        include_tasks=include_tasks,
        include_artifacts=include_artifacts,
        include_credential_audit=include_credential_audit,
    )


@router.get("/runs/{run_id}/tasks", response_model=WorkflowTaskStateListResponse)
async def list_workflow_run_tasks(
    run_id: UUID,
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> WorkflowTaskStateListResponse:
    """Return ordered task states for the specified workflow run."""

    try:
        states = await repo.list_task_states(run_id, require_run=True)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "workflow_not_found",
                "message": str(exc),
            },
        ) from exc

    payload = serialize_task_collection(run_id, states)
    return WorkflowTaskStateListResponse.model_validate(payload)


@router.get("/runs/{run_id}/artifacts", response_model=WorkflowArtifactListResponse)
async def list_workflow_run_artifacts(
    run_id: UUID,
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> WorkflowArtifactListResponse:
    """Return artifact metadata for the specified workflow run."""

    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "workflow_not_found",
                "message": f"Workflow run {run_id} was not found",
            },
        )

    artifacts = await repo.list_artifacts(run_id)
    payload = serialize_artifact_collection(run_id, artifacts)
    return WorkflowArtifactListResponse.model_validate(payload)


@router.post(
    "/runs/{run_id}/retry",
    response_model=SpecWorkflowRunModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_workflow_run(
    run_id: UUID,
    payload: RetryWorkflowRunRequest | None = None,
    repo: SpecWorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> SpecWorkflowRunModel:
    """Retry a failed workflow run starting from the failed stage."""

    request = payload or RetryWorkflowRunRequest()
    notes = request.notes
    mode = request.mode or RetryWorkflowMode.RESUME_FAILED_TASK

    try:
        triggered = await retry_spec_workflow_run(run_id, notes=notes, mode=mode)
    except WorkflowRetryError as exc:
        status_code_value = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "workflow_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=status_code_value,
            detail={
                "code": exc.code,
                "message": str(exc),
                "runId": str(exc.run_id),
            },
        ) from exc

    refreshed = await repo.get_run(triggered.run_id, with_relations=True)
    run = refreshed or triggered.run
    return _serialize_run_model(run)


__all__ = ["router"]
