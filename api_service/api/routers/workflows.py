"""API endpoints for interacting with Spec Kit Celery workflows."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.schemas.workflow_models import (
    CreateWorkflowRunRequest,
    SpecWorkflowRunModel,
    WorkflowRunCollectionResponse,
)
from moonmind.workflows import (
    SpecWorkflowRepository,
    TriggeredWorkflow,
    WorkflowConflictError,
    get_spec_workflow_repository,
    trigger_spec_workflow_run,
)
from moonmind.workflows.speckit_celery import models
from moonmind.workflows.speckit_celery.serializers import serialize_run

router = APIRouter(prefix="/api/workflows/speckit", tags=["speckit-workflows"])


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

    try:
        triggered: TriggeredWorkflow = await trigger_spec_workflow_run(
            feature_key=payload.feature_key,
            created_by=payload.created_by,
            force_phase=payload.force_phase,
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

    runs = await repo.list_runs(
        status=status_enum,
        feature_key=feature_key,
        created_by=created_by,
        limit=limit,
        with_relations=False,
    )
    task_state_map = await repo.list_task_states_for_runs(run.id for run in runs)
    items = [
        _serialize_run_model(
            run,
            include_tasks=include_tasks,
            include_artifacts=False,
            include_credential_audit=False,
            task_states=task_state_map.get(run.id, []),
        )
        for run in runs
    ]
    # Cursor support is not yet implemented; placeholder for future pagination.
    _ = cursor
    return WorkflowRunCollectionResponse(items=items, nextCursor=None)


@router.get("/runs/{run_id}", response_model=SpecWorkflowRunModel)
async def get_workflow_run(
    run_id: UUID,
    include_tasks: bool = Query(True, alias="includeTasks"),
    include_artifacts: bool = Query(True, alias="includeArtifacts"),
    include_credential_audit: bool = Query(
        True, alias="includeCredentialAudit"
    ),
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


__all__ = ["router"]
