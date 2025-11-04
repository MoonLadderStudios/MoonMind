"""FastAPI router exposing Spec Automation monitoring endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.schemas.workflow_models import (
    SpecAutomationArtifactDetail,
    SpecAutomationArtifactSummary,
    SpecAutomationPhaseState,
    SpecAutomationRunDetail,
)
from moonmind.workflows import (
    SpecAutomationRepository,
    get_spec_automation_repository,
)
from moonmind.workflows.speckit_celery import models

router = APIRouter(prefix="/api/spec-automation", tags=["SpecAutomation"])


async def _get_repository(
    session: AsyncSession = Depends(get_async_session),
) -> SpecAutomationRepository:
    """Dependency wiring the Spec Automation repository."""

    return get_spec_automation_repository(session)


def _phase_sort_key(state: models.SpecAutomationTaskState) -> tuple[datetime, str, int]:
    """Ordering helper prioritising chronological execution."""

    timestamp = state.created_at or state.started_at or state.completed_at
    if timestamp is None:
        timestamp = datetime.min.replace(tzinfo=UTC, microsecond=0)
    return (timestamp, state.phase.value, state.attempt)


def _serialize_phase_state(
    state: models.SpecAutomationTaskState,
) -> SpecAutomationPhaseState:
    return SpecAutomationPhaseState(
        phase=state.phase,
        status=state.status,
        attempt=state.attempt,
        started_at=state.started_at,
        completed_at=state.completed_at,
        stdout_path=state.stdout_path,
        stderr_path=state.stderr_path,
        metadata=state.get_metadata(),
    )


def _serialize_artifact_summary(
    artifact: models.SpecAutomationArtifact,
) -> SpecAutomationArtifactSummary:
    return SpecAutomationArtifactSummary(
        artifact_id=artifact.id,
        name=artifact.name,
        artifact_type=artifact.artifact_type,
        storage_path=artifact.storage_path,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        expires_at=artifact.expires_at,
        source_phase=artifact.source_phase,
    )


def _artifact_download_hint(artifact: models.SpecAutomationArtifact) -> str | None:
    """Return a best-effort download hint for the artifact."""

    return artifact.storage_path


def _serialize_artifact_detail(
    artifact: models.SpecAutomationArtifact,
) -> SpecAutomationArtifactDetail:
    summary = _serialize_artifact_summary(artifact)
    return SpecAutomationArtifactDetail(
        **summary.model_dump(),
        download_url=_artifact_download_hint(artifact),
    )


def _serialize_run_detail(
    run: models.SpecAutomationRun,
    task_states: Iterable[models.SpecAutomationTaskState],
    artifacts: Iterable[models.SpecAutomationArtifact],
) -> SpecAutomationRunDetail:
    phases = [
        _serialize_phase_state(state)
        for state in sorted(task_states, key=_phase_sort_key)
    ]
    artifact_summaries = [
        _serialize_artifact_summary(artifact)
        for artifact in artifacts
    ]
    return SpecAutomationRunDetail(
        run_id=run.id,
        status=run.status,
        branch_name=run.branch_name,
        pull_request_url=run.pull_request_url,
        result_summary=run.result_summary,
        started_at=run.started_at,
        completed_at=run.completed_at,
        phases=phases,
        artifacts=artifact_summaries,
    )


@router.get("/runs/{run_id}", response_model=SpecAutomationRunDetail)
async def get_spec_automation_run(
    run_id: UUID,
    repo: SpecAutomationRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> SpecAutomationRunDetail:
    """Return run status, per-phase metadata, and artifact summaries."""

    run_detail = await repo.get_run_detail(run_id)
    if run_detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "run_not_found", "message": f"Run {run_id} not found"},
        )

    run, task_states, artifacts = run_detail
    return _serialize_run_detail(run, task_states, artifacts)


@router.get(
    "/runs/{run_id}/artifacts/{artifact_id}",
    response_model=SpecAutomationArtifactDetail,
)
async def get_spec_automation_artifact(
    run_id: UUID,
    artifact_id: UUID,
    repo: SpecAutomationRepository = Depends(_get_repository),
    _user: User = Depends(get_current_user()),
) -> SpecAutomationArtifactDetail:
    """Return detailed metadata for a specific automation artifact."""

    artifact = await repo.get_artifact(run_id=run_id, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "artifact_not_found",
                "message": f"Artifact {artifact_id} not found for run {run_id}",
            },
        )

    return _serialize_artifact_detail(artifact)
