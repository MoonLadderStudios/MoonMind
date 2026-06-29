"""API endpoints for interacting with workflow automation workflows."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
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
)
from moonmind.workflows import get_workflow_repository
from moonmind.workflows.automation.repositories import WorkflowRepository
from moonmind.workflows.automation import models
from moonmind.workflows.automation.preflight import run_codex_preflight_check
from moonmind.workflows.automation.router import get_codex_shard_router


router = APIRouter()
operations_router = APIRouter(prefix="/api/v1/operations/codex", tags=["operations"])
legacy_router = APIRouter(prefix="/api/workflows", tags=["workflows"])

_AFFINITY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_LEGACY_GONE_ROUTE_KWARGS = {
    "status_code": status.HTTP_410_GONE,
    "response_description": "Gone",
}

def _is_workflow_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))

def _run_owned_by_user(run: object, user: User | None) -> bool:
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False
    created_by = getattr(run, "created_by", None)
    requested_by_user_id = getattr(run, "requested_by_user_id", None)
    return created_by == user_id or requested_by_user_id == user_id

def _raise_workflow_not_found(run_id: UUID) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "workflow_not_found",
            "message": f"Workflow run {run_id} was not found",
        },
    )

def _assert_run_access(run: object | None, run_id: UUID, user: User | None) -> object:
    if run is None:
        _raise_workflow_not_found(run_id)
    if _is_workflow_admin(user) or _run_owned_by_user(run, user):
        return run
    _raise_workflow_not_found(run_id)

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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
) -> WorkflowRepository:
    return get_workflow_repository(session)

def _raise_legacy_workflow_runs_gone() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_workflow_runs_api_removed",
            "message": (
                "The /api/workflows/runs lifecycle API has been removed. "
                "Use /api/executions for execution lifecycle operations and "
                "/api/agent-runs for managed-run observability."
            ),
            "replacement": {
                "lifecycle": "/api/executions",
                "managedRunObservability": "/api/agent-runs",
            },
            "jiraIssue": "MM-1022",
        },
    )


@operations_router.get("/shards", response_model=CodexShardListResponse)
async def list_codex_shards(
    repo: WorkflowRepository = Depends(_get_repository),
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

@operations_router.post(
    "/preflight/{run_id}",
    response_model=CodexPreflightResultModel,
)
async def trigger_codex_preflight(
    run_id: UUID,
    payload: CodexPreflightRequest,
    repo: WorkflowRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> CodexPreflightResultModel:
    """Run the Codex login status check for the specified workflow run."""

    run = _assert_run_access(
        await repo.get_run(run_id, with_relations=True),
        run_id,
        _user,
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
        elif settings.workflow.codex_volume_name:
            volume_name = settings.workflow.codex_volume_name

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

@legacy_router.get("/runs", **_LEGACY_GONE_ROUTE_KWARGS)
async def legacy_list_workflow_runs_gone() -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.get("/runs/{run_id}", **_LEGACY_GONE_ROUTE_KWARGS)
async def legacy_get_workflow_run_gone(run_id: UUID) -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.get("/runs/{run_id}/tasks", **_LEGACY_GONE_ROUTE_KWARGS)
async def legacy_list_workflow_run_tasks_gone(run_id: UUID) -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.get("/runs/{run_id}/artifacts", **_LEGACY_GONE_ROUTE_KWARGS)
async def legacy_list_workflow_run_artifacts_gone(run_id: UUID) -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.get(
    "/runs/{run_id}/artifacts/{artifact_id}", **_LEGACY_GONE_ROUTE_KWARGS
)
async def legacy_get_workflow_run_artifact_gone(run_id: UUID, artifact_id: UUID) -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.get(
    "/runs/{run_id}/artifacts/{artifact_id}/download", **_LEGACY_GONE_ROUTE_KWARGS
)
async def legacy_download_workflow_run_artifact_gone(
    run_id: UUID, artifact_id: UUID
) -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.post("/runs/{run_id}/retry", **_LEGACY_GONE_ROUTE_KWARGS)
async def legacy_retry_workflow_run_gone(run_id: UUID) -> None:
    _raise_legacy_workflow_runs_gone()


@legacy_router.get("/codex/shards")
async def legacy_list_codex_shards_gone() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_codex_operations_route_removed",
            "message": (
                "Codex shard health moved out of the legacy workflow namespace. "
                "Use /api/v1/operations/codex/shards."
            ),
            "replacement": "/api/v1/operations/codex/shards",
            "jiraIssue": "MM-1022",
        },
    )


@legacy_router.post("/runs/{run_id}/codex/preflight")
async def legacy_trigger_codex_preflight_gone(run_id: UUID) -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail={
            "code": "legacy_codex_operations_route_removed",
            "message": (
                "Codex preflight moved out of the legacy workflow namespace. "
                "Use /api/v1/operations/codex/preflight/{run_id}."
            ),
            "replacement": f"/api/v1/operations/codex/preflight/{run_id}",
            "jiraIssue": "MM-1022",
        },
    )


router.include_router(operations_router)
router.include_router(legacy_router)

__all__ = ["router"]
