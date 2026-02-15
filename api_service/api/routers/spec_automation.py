"""FastAPI router exposing Spec Automation monitoring endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config import settings
from moonmind.schemas.workflow_models import (
    SpecAutomationArtifactDetail,
    SpecAutomationArtifactSummary,
    SpecAutomationPhaseState,
    SpecAutomationRunDetail,
)
from moonmind.workflows import SpecAutomationRepository, get_spec_automation_repository
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
    skill_meta = state.get_skill_execution_metadata() or {}
    return SpecAutomationPhaseState(
        phase=state.phase,
        status=state.status,
        attempt=state.attempt,
        started_at=state.started_at,
        completed_at=state.completed_at,
        stdout_path=state.stdout_path,
        stderr_path=state.stderr_path,
        metadata=state.get_metadata(),
        selected_skill=skill_meta.get("selectedSkill"),
        execution_path=skill_meta.get("executionPath"),
        used_skills=skill_meta.get("usedSkills"),
        used_fallback=skill_meta.get("usedFallback"),
        shadow_mode_requested=skill_meta.get("shadowModeRequested"),
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


def _artifact_download_hint(
    request: Request, artifact: models.SpecAutomationArtifact
) -> str | None:
    """Return a download endpoint URL for the artifact."""

    return str(
        request.url_for(
            "download_spec_automation_artifact",
            run_id=str(artifact.run_id),
            artifact_id=str(artifact.id),
        )
    )


def _serialize_artifact_detail(
    artifact: models.SpecAutomationArtifact,
    *,
    request: Request | None = None,
) -> SpecAutomationArtifactDetail:
    summary = _serialize_artifact_summary(artifact)
    return SpecAutomationArtifactDetail(
        **summary.model_dump(),
        download_url=_artifact_download_hint(request, artifact) if request else None,
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
        _serialize_artifact_summary(artifact) for artifact in artifacts
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


def _resolve_allowed_repositories(user: User) -> set[str] | None:
    """Return the repository slugs the user is permitted to access."""

    raw_allowed = getattr(user, "allowed_repositories", None)
    if raw_allowed is not None:
        if isinstance(raw_allowed, str):
            raw_iterable = (value.strip() for value in raw_allowed.split(","))
        else:
            try:
                iterator = iter(raw_allowed)
            except TypeError:
                raw_iterable = (str(raw_allowed).strip(),)
            else:
                raw_iterable = (str(value).strip() for value in iterator)
        allowed = {slug.lower() for slug in raw_iterable if slug}
        return allowed

    configured = settings.github.github_repos
    if configured:
        allowed = {
            slug.strip().lower() for slug in configured.split(",") if slug.strip()
        }
        if allowed:
            return allowed

    default_repo = settings.spec_workflow.github_repository
    if default_repo:
        slug = default_repo.strip().lower()
        if slug:
            return {slug}

    return None


def _ensure_run_access(run: models.SpecAutomationRun, user: User) -> None:
    """Guard against users accessing runs outside their allowed repositories."""

    allowed = _resolve_allowed_repositories(user)
    if allowed is None:
        return

    repository = (run.repository or "").lower()
    if repository not in allowed and not getattr(user, "is_superuser", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "run_access_forbidden",
                "message": "You do not have permission to access this automation run.",
            },
        )


def _resolve_artifact_file(storage_path: str) -> Path:
    """Resolve an artifact storage path to a filesystem location within the artifact root."""

    base = Path(settings.spec_workflow.artifacts_root).resolve()
    candidate = (
        (base / storage_path).resolve()
        if not Path(storage_path).is_absolute()
        else Path(storage_path).resolve()
    )
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "invalid_artifact_path",
                "message": "Artifact path is invalid.",
            },
        ) from exc
    return candidate


@router.get("/runs/{run_id}", response_model=SpecAutomationRunDetail)
async def get_spec_automation_run(
    run_id: UUID,
    repo: SpecAutomationRepository = Depends(_get_repository),
    user: User = Depends(get_current_user()),
) -> SpecAutomationRunDetail:
    """Return run status, per-phase metadata, and artifact summaries."""

    run_detail = await repo.get_run_detail(run_id)
    if run_detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "run_not_found",
                "message": "Automation run not found.",
            },
        )

    run, task_states, artifacts = run_detail
    _ensure_run_access(run, user)
    return _serialize_run_detail(run, task_states, artifacts)


@router.get(
    "/runs/{run_id}/artifacts/{artifact_id}",
    response_model=SpecAutomationArtifactDetail,
)
async def get_spec_automation_artifact(
    request: Request,
    run_id: UUID,
    artifact_id: UUID,
    repo: SpecAutomationRepository = Depends(_get_repository),
    user: User = Depends(get_current_user()),
) -> SpecAutomationArtifactDetail:
    """Return detailed metadata for a specific automation artifact."""

    artifact = await repo.get_artifact(run_id=run_id, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "artifact_not_found",
                "message": "Artifact not found for the requested run.",
            },
        )

    run = getattr(artifact, "run", None)
    if run is None:
        run = await repo.get_run(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "run_not_found",
                    "message": "Automation run not found.",
                },
            )
    _ensure_run_access(run, user)

    return _serialize_artifact_detail(artifact, request=request)


@router.get(
    "/runs/{run_id}/artifacts/{artifact_id}/download",
    response_class=FileResponse,
)
async def download_spec_automation_artifact(
    run_id: UUID,
    artifact_id: UUID,
    repo: SpecAutomationRepository = Depends(_get_repository),
    user: User = Depends(get_current_user()),
) -> FileResponse:
    """Return the artifact file as a streamed download."""

    artifact = await repo.get_artifact(run_id=run_id, artifact_id=artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "artifact_not_found",
                "message": "Artifact not found for the requested run.",
            },
        )

    run = getattr(artifact, "run", None)
    if run is None:
        run = await repo.get_run(run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "run_not_found",
                    "message": "Automation run not found.",
                },
            )
    _ensure_run_access(run, user)

    file_path = _resolve_artifact_file(artifact.storage_path)
    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "artifact_file_missing",
                "message": "Artifact content is no longer available.",
            },
        )

    return FileResponse(
        path=str(file_path),
        filename=artifact.name,
        media_type=artifact.content_type or "application/octet-stream",
    )
