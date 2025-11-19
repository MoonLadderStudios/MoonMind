"""FastAPI routes for orchestrator runs."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from api_service.db.base import get_async_session
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    OrchestratorCreateRunRequest,
    OrchestratorRunDetailModel,
    OrchestratorRunListResponse,
    OrchestratorRunSummaryModel,
    OrchestratorRunStatus,
    OrchestratorArtifactListResponse,
)
from moonmind.workflows.orchestrator.action_plan import generate_action_plan
from moonmind.workflows.orchestrator.metrics import record_run_queued
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.orchestrator.serializers import (
    serialize_artifacts,
    serialize_run_detail,
    serialize_run_list,
    serialize_run_summary,
)
from moonmind.workflows.orchestrator.service_profiles import get_service_profile
from moonmind.workflows.orchestrator.services import OrchestratorService
from moonmind.workflows.orchestrator.storage import (
    ArtifactStorage,
    ArtifactStorageError,
    resolve_artifact_root,
)
from moonmind.workflows.orchestrator.tasks import enqueue_action_plan

router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])


def _artifact_root() -> Path:
    default_root = Path(settings.spec_workflow.artifacts_root)
    configured = os.getenv("ORCHESTRATOR_ARTIFACT_ROOT")
    try:
        return resolve_artifact_root(default_root, configured)
    except ArtifactStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "invalid_artifact_root",
                "message": "Artifact storage root misconfigured.",
            },
        ) from exc


@router.post(
    "/runs",
    response_model=OrchestratorRunSummaryModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_orchestrator_run(
    payload: OrchestratorCreateRunRequest,
    session: AsyncSession = Depends(get_async_session),
) -> OrchestratorRunSummaryModel:
    """Create a new orchestrator run and enqueue the plan for execution."""

    try:
        profile = get_service_profile(payload.target_service)
    except KeyError as exc:  # pragma: no cover - defensive programming
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unknown_service",
                "message": "Requested service is not managed by the orchestrator.",
            },
        ) from exc

    try:
        plan = generate_action_plan(payload.instruction, profile)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_instruction", "message": str(exc)},
        ) from exc

    artifact_root = _artifact_root()
    repo = OrchestratorRepository(session)
    service = OrchestratorService(
        repository=repo,
        artifact_storage=ArtifactStorage(artifact_root),
    )
    run = await service.create_run(
        plan,
        approval_token=payload.approval_token,
        priority=payload.priority,
    )

    step_sequence = [step.name for step in plan.steps]
    enqueue_action_plan(run.id, step_sequence, include_rollback=True)
    record_run_queued(run.target_service)

    return serialize_run_summary(run)


@router.get(
    "/runs",
    response_model=OrchestratorRunListResponse,
)
async def list_orchestrator_runs(
    *,
    status_filter: OrchestratorRunStatus | None = Query(
        None, alias="status", description="Filter by run status"
    ),
    service: str | None = Query(
        None, alias="service", description="Filter by target service"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
) -> OrchestratorRunListResponse:
    repo = OrchestratorRepository(session)
    runs = await repo.list_runs(
        status=status_filter, target_service=service, limit=limit, offset=offset
    )
    return serialize_run_list(runs)


@router.get(
    "/runs/{run_id}",
    response_model=OrchestratorRunDetailModel,
)
async def get_orchestrator_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> OrchestratorRunDetailModel:
    repo = OrchestratorRepository(session)
    run = await repo.get_run(run_id, with_relations=True)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "run_not_found", "message": "Run does not exist."},
        )
    return serialize_run_detail(run)


@router.get(
    "/runs/{run_id}/artifacts",
    response_model=OrchestratorArtifactListResponse,
)
async def list_orchestrator_artifacts(
    run_id: UUID,
    session: AsyncSession = Depends(get_async_session),
) -> OrchestratorArtifactListResponse:
    repo = OrchestratorRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "run_not_found", "message": "Run does not exist."},
        )
    artifacts = await repo.list_artifacts(run_id)
    return OrchestratorArtifactListResponse(artifacts=serialize_artifacts(artifacts))
