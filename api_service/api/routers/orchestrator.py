"""FastAPI routes for orchestrator runs."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db import models as db_models
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    OrchestratorApprovalRequest,
    OrchestratorArtifactListResponse,
    OrchestratorCreateRunRequest,
    OrchestratorTaskStepInputModel,
    OrchestratorRetryRequest,
    OrchestratorRunDetailModel,
    OrchestratorRunListResponse,
    OrchestratorRunStatus,
    OrchestratorRunSummaryModel,
)
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.workflows.orchestrator.action_plan import (
    generate_action_plan,
    generate_skill_action_plan,
)
from moonmind.workflows.orchestrator.metrics import record_run_queued
from moonmind.workflows.orchestrator.policies import (
    resolve_policy,
    validate_approval_token,
)
from moonmind.workflows.orchestrator.queue_dispatch import (
    enqueue_orchestrator_run_job,
    enqueue_orchestrator_task_job,
)
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.orchestrator.serializers import (
    serialize_artifacts,
    serialize_run_detail,
    serialize_run_list,
    serialize_run_summary,
)
from moonmind.workflows.orchestrator.service_profiles import get_service_profile
from moonmind.workflows.orchestrator.services import OrchestratorService
from moonmind.workflows.orchestrator.skill_executor import (
    is_runnable_skill,
    list_runnable_skill_names,
)
from moonmind.workflows.orchestrator.storage import (
    ArtifactStorage,
    ArtifactStorageError,
    resolve_artifact_root,
)

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _extract_step_names(plan_steps: Sequence[object] | None) -> list[str]:
    steps: list[str] = []
    for entry in plan_steps or []:
        name = None
        if isinstance(entry, dict):
            name = entry.get("name")
        else:
            name = getattr(entry, "name", entry)
        if name:
            steps.append(str(name))
    return steps


def _queue_service(session: AsyncSession) -> AgentQueueService:
    return AgentQueueService(AgentQueueRepository(session))


def _normalize_task_step_queue_payload(
    steps: Sequence[OrchestratorTaskStepInputModel],
) -> list[dict[str, object]]:
    payload_steps: list[dict[str, object]] = []
    for step in steps:
        payload_steps.append(
            {
                "stepId": step.id,
                "title": step.title,
                "instructions": step.instructions,
                "skillId": step.skill.id,
                "skillArgs": dict(step.skill.args or {}),
            }
        )
    return payload_steps


def _run_uses_task_runtime_steps(run: db_models.OrchestratorRun) -> bool:
    return bool(getattr(run, "task_steps", None))


def _serialize_persisted_task_steps_for_queue(
    steps: Sequence[db_models.OrchestratorTaskStep],
) -> list[dict[str, object]]:
    payload_steps: list[dict[str, object]] = []
    for step in sorted(steps, key=lambda item: item.step_index):
        payload_steps.append(
            {
                "stepId": step.step_id,
                "title": step.title,
                "instructions": step.instructions,
                "skillId": step.skill_id,
                "skillArgs": dict(step.skill_args or {}),
            }
        )
    return payload_steps


@router.post(
    "/runs",
    response_model=OrchestratorRunSummaryModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_orchestrator_run(
    payload: OrchestratorCreateRunRequest,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
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

    task_steps = list(payload.steps or [])
    try:
        available_skills = set(list_runnable_skill_names())
        if task_steps:
            if payload.target_service != "orchestrator":
                raise ValueError(
                    "Step-based orchestrator tasks are only supported for targetService=orchestrator."
                )
            seen_step_ids: set[str] = set()
            for step in task_steps:
                normalized_step_id = step.id.strip()
                normalized_skill_id = step.skill.id.strip()
                if not normalized_step_id:
                    raise ValueError("Orchestrator task step id must not be empty.")
                if normalized_step_id in seen_step_ids:
                    raise ValueError(
                        f"Duplicate orchestrator task step id: '{normalized_step_id}'."
                    )
                if not normalized_skill_id:
                    raise ValueError(
                        f"Orchestrator task step '{normalized_step_id}' must include a skill id."
                    )
                if normalized_skill_id == "auto":
                    raise ValueError(
                        "Orchestrator task steps require explicit skill ids, not 'auto'."
                    )
                if normalized_skill_id not in available_skills:
                    raise ValueError(
                        f"Selected skill '{normalized_skill_id}' is not runnable from configured mirrors."
                    )
                if not is_runnable_skill(normalized_skill_id):
                    raise ValueError(
                        f"Selected skill '{normalized_skill_id}' does not expose a runnable script."
                    )
                step.id = normalized_step_id
                step.skill.id = normalized_skill_id
                seen_step_ids.add(normalized_step_id)
            # Preserve action-plan compatibility while task runtime steps execute from dedicated rows.
            plan = generate_action_plan(payload.instruction, profile)
        else:
            requested_skill = (payload.skill_id or "").strip()
            if requested_skill:
                if payload.target_service != "orchestrator":
                    raise ValueError(
                        "Explicit skill runs are only supported for targetService=orchestrator."
                    )
                if requested_skill == "auto":
                    raise ValueError(
                        "Orchestrator skill runs require an explicit skill id, not 'auto'."
                    )
                if requested_skill not in available_skills:
                    raise ValueError(
                        f"Selected skill '{requested_skill}' is not runnable from configured mirrors."
                    )
                if not is_runnable_skill(requested_skill):
                    raise ValueError(
                        f"Selected skill '{requested_skill}' does not expose a runnable script."
                    )
                plan = generate_skill_action_plan(
                    payload.instruction,
                    profile,
                    skill_id=requested_skill,
                    skill_args=payload.skill_args,
                )
            else:
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
    queue_service = _queue_service(session)
    run = await service.create_run(
        plan,
        approval_token=payload.approval_token,
        priority=payload.priority,
        task_steps=task_steps or None,
    )

    if run.status != db_models.OrchestratorRunStatus.AWAITING_APPROVAL:
        if task_steps:
            await enqueue_orchestrator_task_job(
                queue_service=queue_service,
                task_id=run.id,
                steps=_normalize_task_step_queue_payload(task_steps),
            )
        else:
            step_sequence = [step.name for step in plan.steps]
            await enqueue_orchestrator_run_job(
                queue_service=queue_service,
                run_id=run.id,
                steps=step_sequence,
                include_rollback=True,
            )
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
    _user: User = Depends(get_current_user()),
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
    _user: User = Depends(get_current_user()),
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
    _user: User = Depends(get_current_user()),
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


@router.post(
    "/runs/{run_id}/approvals",
    response_model=OrchestratorRunSummaryModel,
)
async def provide_orchestrator_approval(
    run_id: UUID,
    payload: OrchestratorApprovalRequest,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunSummaryModel:
    """Record approval for a protected run and enqueue execution if needed."""

    repo = OrchestratorRepository(session)
    run = await repo.get_run(run_id, with_relations=True)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "run_not_found", "message": "Run does not exist."},
        )

    if run.status != db_models.OrchestratorRunStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "approval_not_applicable",
                "message": "Run is not awaiting approval.",
            },
        )

    now = _utcnow()
    policy = await resolve_policy(repo, run.target_service)
    approved, reason = validate_approval_token(
        policy,
        payload.token,
        granted_at=now,
        expires_at=payload.expires_at,
    )
    if not approved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "approval_invalid", "message": reason},
        )

    await repo.record_approval(
        run,
        token=payload.token,
        approver={"id": payload.approver.id, "role": payload.approver.role},
        granted_at=now,
        expires_at=payload.expires_at,
    )
    await repo.commit()
    queue_service = _queue_service(session)

    enqueued = False
    if _run_uses_task_runtime_steps(run):
        payload_steps = _serialize_persisted_task_steps_for_queue(run.task_steps or [])
        if payload_steps:
            await enqueue_orchestrator_task_job(
                queue_service=queue_service,
                task_id=run.id,
                steps=payload_steps,
            )
            enqueued = True
    else:
        step_sequence = _extract_step_names(getattr(run.action_plan, "steps", None))
        if step_sequence:
            await enqueue_orchestrator_run_job(
                queue_service=queue_service,
                run_id=run.id,
                steps=step_sequence,
                include_rollback=True,
            )
            enqueued = True
    if enqueued:
        record_run_queued(run.target_service)

    return serialize_run_summary(run)


@router.post(
    "/runs/{run_id}/retry",
    response_model=OrchestratorRunSummaryModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_orchestrator_run(
    run_id: UUID,
    payload: OrchestratorRetryRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunSummaryModel:
    """Retry a failed orchestrator run from the requested step."""

    repo = OrchestratorRepository(session)
    queue_service = _queue_service(session)
    run = await repo.get_run(run_id, with_relations=True)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "run_not_found", "message": "Run does not exist."},
        )

    if run.status == db_models.OrchestratorRunStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "retry_not_allowed",
                "message": "Run is still in progress and cannot be retried.",
            },
        )
    if run.status == db_models.OrchestratorRunStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "retry_not_allowed",
                "message": "Run requires approval before it can be retried.",
            },
        )

    if _run_uses_task_runtime_steps(run):
        if payload and payload.resume_from_step is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_resume_step",
                    "message": "resumeFromStep is only supported for legacy orchestrator run plans.",
                },
            )

        if not run.task_steps:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "missing_task_steps",
                    "message": "Task cannot be retried without persisted runtime steps.",
                },
            )

        retry_reason = payload.reason if payload else None
        for step in run.task_steps:
            await repo.update_task_step_state(
                step,
                status=db_models.OrchestratorTaskStepStatus.QUEUED,
                message=retry_reason,
                started_at=None,
                finished_at=None,
                attempt=(step.attempt or 0) + 1,
                artifact_refs=[],
            )
        await repo.update_run(
            run,
            status=db_models.OrchestratorRunStatus.PENDING,
            started_at=None,
            completed_at=None,
        )
        await repo.commit()
        await enqueue_orchestrator_task_job(
            queue_service=queue_service,
            task_id=run.id,
            steps=_serialize_persisted_task_steps_for_queue(run.task_steps),
        )
        record_run_queued(run.target_service)
        return serialize_run_summary(run)

    if run.action_plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "missing_action_plan",
                "message": "Run cannot be retried without an action plan.",
            },
        )

    plan_steps = _extract_step_names(run.action_plan.steps)
    if not plan_steps:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "missing_action_plan",
                "message": "Run cannot be retried without plan steps.",
            },
        )

    resume_from = getattr(payload, "resume_from_step", None)
    if resume_from is not None:
        try:
            start_index = plan_steps.index(resume_from.value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "invalid_resume_step",
                    "message": "Requested resume step is not part of the action plan.",
                },
            ) from exc
        step_sequence = plan_steps[start_index:]
    else:
        step_sequence = plan_steps

    await repo.reset_plan_steps(
        run,
        steps=[db_models.OrchestratorPlanStep(step) for step in step_sequence],
        bump_attempt=True,
        reason=(payload.reason if payload else None),
    )
    await repo.update_run(
        run,
        status=db_models.OrchestratorRunStatus.PENDING,
        started_at=None,
        completed_at=None,
    )
    await repo.commit()

    await enqueue_orchestrator_run_job(
        queue_service=queue_service,
        run_id=run.id,
        steps=step_sequence,
        include_rollback=True,
    )
    record_run_queued(run.target_service)
    return serialize_run_summary(run)


@router.post(
    "/tasks",
    response_model=OrchestratorRunSummaryModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_orchestrator_task(
    payload: OrchestratorCreateRunRequest,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunSummaryModel:
    """Alias for creating orchestrator tasks with task-centric naming."""

    return await create_orchestrator_run(payload, session=session, _user=_user)


@router.get(
    "/tasks",
    response_model=OrchestratorRunListResponse,
)
async def list_orchestrator_tasks(
    *,
    status_filter: OrchestratorRunStatus | None = Query(
        None, alias="status", description="Filter by task status"
    ),
    service: str | None = Query(
        None, alias="service", description="Filter by target service"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunListResponse:
    """Alias for listing orchestrator tasks."""

    return await list_orchestrator_runs(
        status_filter=status_filter,
        service=service,
        limit=limit,
        offset=offset,
        session=session,
        _user=_user,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=OrchestratorRunDetailModel,
)
async def get_orchestrator_task(
    task_id: UUID,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunDetailModel:
    """Alias for orchestrator task detail retrieval."""

    return await get_orchestrator_run(task_id, session=session, _user=_user)


@router.get(
    "/tasks/{task_id}/artifacts",
    response_model=OrchestratorArtifactListResponse,
)
async def list_orchestrator_task_artifacts(
    task_id: UUID,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorArtifactListResponse:
    """Alias for orchestrator task artifacts."""

    return await list_orchestrator_artifacts(task_id, session=session, _user=_user)


@router.post(
    "/tasks/{task_id}/approvals",
    response_model=OrchestratorRunSummaryModel,
)
async def provide_orchestrator_task_approval(
    task_id: UUID,
    payload: OrchestratorApprovalRequest,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunSummaryModel:
    """Alias for approval submission using task naming."""

    return await provide_orchestrator_approval(
        task_id,
        payload,
        session=session,
        _user=_user,
    )


@router.post(
    "/tasks/{task_id}/retry",
    response_model=OrchestratorRunSummaryModel,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_orchestrator_task(
    task_id: UUID,
    payload: OrchestratorRetryRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
    _user: User = Depends(get_current_user()),
) -> OrchestratorRunSummaryModel:
    """Alias for retrying orchestrator tasks."""

    return await retry_orchestrator_run(
        task_id,
        payload,
        session=session,
        _user=_user,
    )
