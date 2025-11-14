"""FastAPI routes for orchestrator runs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from api_service.db.base import get_async_session
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    OrchestratorApprovalStatus,
    OrchestratorCreateRunRequest,
    OrchestratorRunSummaryModel,
)
from moonmind.workflows.orchestrator.action_plan import generate_action_plan
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.orchestrator.service_profiles import get_service_profile
from moonmind.workflows.orchestrator.storage import ArtifactStorage
from moonmind.workflows.orchestrator.tasks import enqueue_action_plan

router = APIRouter(prefix="/orchestrator", tags=["Orchestrator"])


def _artifact_root() -> Path:
    configured = os.getenv("ORCHESTRATOR_ARTIFACT_ROOT")
    if configured:
        return Path(configured)
    return Path(settings.spec_workflow.artifacts_root)


def _resolve_approval_state(
    run: db_models.OrchestratorRun,
) -> Tuple[bool, OrchestratorApprovalStatus]:
    if run.approval_gate_id is None:
        return False, OrchestratorApprovalStatus.NOT_REQUIRED
    if run.approval_token:
        return True, OrchestratorApprovalStatus.GRANTED
    return True, OrchestratorApprovalStatus.AWAITING


def _serialize_run_summary(
    run: db_models.OrchestratorRun,
) -> OrchestratorRunSummaryModel:
    approval_required, approval_status = _resolve_approval_state(run)
    return OrchestratorRunSummaryModel(
        run_id=run.id,
        status=run.status,
        priority=run.priority,
        target_service=run.target_service,
        instruction=run.instruction,
        queued_at=run.queued_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        approval_required=approval_required,
        approval_status=approval_status,
    )


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
                "message": f"Service '{payload.target_service}' is not managed by the orchestrator.",
            },
        ) from exc

    try:
        plan = generate_action_plan(payload.instruction, profile)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_instruction", "message": str(exc)},
        ) from exc

    repo = OrchestratorRepository(session)
    action_plan = await repo.create_action_plan(
        steps=plan.steps_as_dict(),
        service_context=plan.service_context,
    )
    run = await repo.create_run(
        instruction=plan.instruction,
        target_service=profile.compose_service,
        action_plan=action_plan,
        approval_token=payload.approval_token,
        priority=payload.priority,
    )

    storage = ArtifactStorage(_artifact_root())
    artifact_directory = storage.ensure_run_directory(run.id)
    run.artifact_root = str(artifact_directory)

    await repo.initialize_plan_states(run, plan.steps_as_dict())
    await repo.commit()

    step_sequence = [step.name for step in plan.steps]
    enqueue_action_plan(run.id, step_sequence)

    return _serialize_run_summary(run)
