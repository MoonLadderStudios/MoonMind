"""REST router for task proposal queue operations."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.schemas.agent_queue_models import JobModel
from moonmind.schemas.task_proposal_models import (
    TaskProposalCreateRequest,
    TaskProposalDismissRequest,
    TaskProposalListResponse,
    TaskProposalModel,
    TaskProposalOriginModel,
    TaskProposalPriorityRequest,
    TaskProposalPromoteRequest,
    TaskProposalPromoteResponse,
    TaskProposalSnoozeRequest,
    TaskProposalTaskPreview,
)
from moonmind.workflows import get_task_proposal_service
from moonmind.workflows.agent_queue.service import WorkerAuthPolicy
from moonmind.workflows.task_proposals.models import (
    TaskProposal,
    TaskProposalOriginSource,
    TaskProposalStatus,
)
from moonmind.workflows.task_proposals.service import (
    TaskProposalError,
    TaskProposalService,
    TaskProposalStatusError,
    TaskProposalValidationError,
)

router = APIRouter(prefix="/api/proposals", tags=["task-proposals"])


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskProposalService:
    return get_task_proposal_service(session)


def _build_task_preview(
    task_request: dict[str, object]
) -> TaskProposalTaskPreview | None:
    payload = task_request.get("payload") if isinstance(task_request, dict) else None
    if not isinstance(payload, dict):
        return None
    repository = str(payload.get("repository") or "").strip()
    if not repository:
        return None
    task_node = payload.get("task")
    task = task_node if isinstance(task_node, dict) else {}
    runtime_node = task.get("runtime")
    runtime = runtime_node if isinstance(runtime_node, dict) else {}
    git_node = task.get("git")
    git = git_node if isinstance(git_node, dict) else {}
    publish_node = task.get("publish")
    publish = publish_node if isinstance(publish_node, dict) else {}
    skill_node = task.get("skill")
    skill = skill_node if isinstance(skill_node, dict) else {}

    runtime_mode = runtime.get("mode") or payload.get("targetRuntime")
    skill_id = skill.get("id")
    publish_mode = publish.get("mode")
    starting_branch = git.get("startingBranch")
    new_branch = git.get("newBranch")
    instructions = task.get("instructions") or payload.get("instruction")

    runtime_value = (
        (str(runtime_mode).strip() or None) if runtime_mode is not None else None
    )
    skill_value = (str(skill_id).strip() or None) if skill_id is not None else None
    publish_value = (
        (str(publish_mode).strip() or None) if publish_mode is not None else None
    )
    starting_value = (
        (str(starting_branch).strip() or None) if starting_branch is not None else None
    )
    new_branch_value = (
        (str(new_branch).strip() or None) if new_branch is not None else None
    )
    instructions_value = (
        (str(instructions).strip() or None) if instructions is not None else None
    )

    return TaskProposalTaskPreview(
        repository=repository,
        runtimeMode=runtime_value,
        skillId=skill_value,
        publishMode=publish_value,
        startingBranch=starting_value,
        newBranch=new_branch_value,
        instructions=instructions_value,
    )


def _serialize_similar(similar: list[TaskProposal] | None) -> list[dict[str, object]]:
    if not similar:
        return []
    items: list[dict[str, object]] = []
    for row in similar:
        items.append(
            {
                "id": row.id,
                "title": row.title,
                "category": row.category,
                "repository": row.repository,
                "createdAt": row.created_at,
            }
        )
    return items


def _serialize_proposal(
    proposal: TaskProposal, *, similar: list[TaskProposal] | None = None
) -> TaskProposalModel:
    origin = TaskProposalOriginModel(
        source=proposal.origin_source,
        id=proposal.origin_id,
        metadata=proposal.origin_metadata or {},
    )
    preview = _build_task_preview(proposal.task_create_request or {})
    snooze_history = proposal.snooze_history or []
    data = {
        "id": proposal.id,
        "status": proposal.status,
        "title": proposal.title,
        "summary": proposal.summary,
        "category": proposal.category,
        "tags": proposal.tags or [],
        "repository": proposal.repository,
        "dedupKey": proposal.dedup_key,
        "dedupHash": proposal.dedup_hash,
        "reviewPriority": proposal.review_priority,
        "priorityOverrideReason": proposal.priority_override_reason,
        "proposedByWorkerId": proposal.proposed_by_worker_id,
        "proposedByUserId": proposal.proposed_by_user_id,
        "promotedJobId": proposal.promoted_job_id,
        "promotedAt": proposal.promoted_at,
        "promotedByUserId": proposal.promoted_by_user_id,
        "decidedByUserId": proposal.decided_by_user_id,
        "decisionNote": proposal.decision_note,
        "createdAt": proposal.created_at,
        "updatedAt": proposal.updated_at,
        "origin": origin,
        "taskCreateRequest": proposal.task_create_request or {},
        "taskPreview": preview,
        "snoozedUntil": proposal.snoozed_until,
        "snoozedByUserId": proposal.snoozed_by_user_id,
        "snoozeNote": proposal.snooze_note,
        "snoozeHistory": snooze_history,
        "similar": _serialize_similar(similar),
    }
    return TaskProposalModel.model_validate(data)


async def _resolve_actor(
    *,
    service: TaskProposalService,
    worker_token: Optional[str],
    user: Optional[User],
) -> tuple[Optional[UUID], Optional[str]]:
    if worker_token:
        try:
            policy: WorkerAuthPolicy = await service.resolve_worker_token(worker_token)
        except TaskProposalValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "worker_not_authorized", "message": str(exc)},
            ) from exc
        return None, policy.worker_id
    if user is not None:
        return getattr(user, "id", None), None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "authentication_required",
            "message": "User or worker authentication is required.",
        },
    )


@router.post("", response_model=TaskProposalModel, status_code=status.HTTP_201_CREATED)
async def create_proposal(
    payload: TaskProposalCreateRequest = Body(...),
    service: TaskProposalService = Depends(_get_service),
    worker_token: Optional[str] = Header(None, alias="X-MoonMind-Worker-Token"),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> TaskProposalModel:
    proposed_by_user_id, proposed_by_worker_id = await _resolve_actor(
        service=service,
        worker_token=worker_token,
        user=user,
    )
    try:
        proposal = await service.create_proposal(
            title=payload.title,
            summary=payload.summary,
            category=payload.category,
            tags=payload.tags,
            task_create_request=payload.task_create_request.model_dump(by_alias=True),
            origin_source=payload.origin.source,
            origin_id=payload.origin.id,
            origin_metadata=payload.origin.metadata,
            proposed_by_worker_id=proposed_by_worker_id,
            proposed_by_user_id=proposed_by_user_id,
            review_priority=payload.review_priority,
        )
    except TaskProposalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_proposal", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)


@router.get("", response_model=TaskProposalListResponse)
async def list_proposals(
    *,
    service: TaskProposalService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
    status_filter: Optional[str] = Query("open", alias="status"),
    category: Optional[str] = Query(None, alias="category"),
    repository: Optional[str] = Query(None, alias="repository"),
    origin_source: Optional[str] = Query(None, alias="originSource"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None, alias="cursor"),
    include_snoozed: bool = Query(False, alias="includeSnoozed"),
) -> TaskProposalListResponse:
    status_value = None
    only_snoozed = False
    if status_filter:
        normalized_status = status_filter.lower()
        if normalized_status == "snoozed":
            only_snoozed = True
            status_value = TaskProposalStatus.OPEN
        else:
            try:
                status_value = TaskProposalStatus(normalized_status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={"code": "invalid_status", "message": str(exc)},
                ) from exc
    origin_value = None
    if origin_source:
        try:
            origin_value = TaskProposalOriginSource(origin_source.lower())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "invalid_origin", "message": str(exc)},
            ) from exc
    try:
        proposals, next_cursor = await service.list_proposals(
            status=status_value,
            category=category,
            repository=repository,
            origin_source=origin_value,
            cursor=cursor,
            limit=limit,
            include_snoozed=include_snoozed,
            only_snoozed=only_snoozed,
        )
    except TaskProposalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc
    items = [_serialize_proposal(item) for item in proposals]
    return TaskProposalListResponse(items=items, next_cursor=next_cursor)


@router.get("/{proposal_id}", response_model=TaskProposalModel)
async def get_proposal(
    *,
    proposal_id: UUID,
    service: TaskProposalService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
    include_similars: bool = Query(True, alias="includeSimilars"),
) -> TaskProposalModel:
    try:
        proposal = await service.get_proposal(proposal_id)
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    similar_rows: list[TaskProposal] | None = None
    if include_similars:
        similar_rows = await service.get_similar_proposals(proposal)
    return _serialize_proposal(proposal, similar=similar_rows)


@router.post("/{proposal_id}/promote", response_model=TaskProposalPromoteResponse)
async def promote_proposal(
    *,
    proposal_id: UUID,
    payload: TaskProposalPromoteRequest = Body(
        default_factory=TaskProposalPromoteRequest
    ),
    service: TaskProposalService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskProposalPromoteResponse:
    try:
        override_payload = (
            payload.task_create_request_override.model_dump(by_alias=True)
            if payload.task_create_request_override is not None
            else None
        )
        proposal, job = await service.promote_proposal(
            proposal_id=proposal_id,
            promoted_by_user_id=getattr(user, "id"),
            priority_override=payload.priority,
            max_attempts_override=payload.max_attempts,
            note=payload.note,
            task_create_request_override=override_payload,
        )
    except TaskProposalStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_state", "message": str(exc)},
        ) from exc
    except TaskProposalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return TaskProposalPromoteResponse(
        proposal=_serialize_proposal(proposal),
        job=JobModel.model_validate(job),
    )


@router.post("/{proposal_id}/dismiss", response_model=TaskProposalModel)
async def dismiss_proposal(
    *,
    proposal_id: UUID,
    payload: TaskProposalDismissRequest = Body(
        default_factory=TaskProposalDismissRequest
    ),
    service: TaskProposalService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.dismiss_proposal(
            proposal_id=proposal_id,
            dismissed_by_user_id=getattr(user, "id"),
            note=payload.note,
        )
    except TaskProposalStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_state", "message": str(exc)},
        ) from exc
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)


@router.post("/{proposal_id}/priority", response_model=TaskProposalModel)
async def update_priority(
    *,
    proposal_id: UUID,
    payload: TaskProposalPriorityRequest = Body(...),
    service: TaskProposalService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.update_review_priority(
            proposal_id=proposal_id,
            priority=payload.priority,
            updated_by_user_id=getattr(user, "id"),
        )
    except TaskProposalStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_state", "message": str(exc)},
        ) from exc
    except TaskProposalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)


@router.post("/{proposal_id}/snooze", response_model=TaskProposalModel)
async def snooze_proposal(
    *,
    proposal_id: UUID,
    payload: TaskProposalSnoozeRequest = Body(...),
    service: TaskProposalService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.snooze_proposal(
            proposal_id=proposal_id,
            until=payload.until,
            note=payload.note,
            user_id=getattr(user, "id"),
        )
    except TaskProposalStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_state", "message": str(exc)},
        ) from exc
    except TaskProposalValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request", "message": str(exc)},
        ) from exc
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)


@router.post("/{proposal_id}/unsnooze", response_model=TaskProposalModel)
async def unsnooze_proposal(
    *,
    proposal_id: UUID,
    service: TaskProposalService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.unsnooze_proposal(
            proposal_id=proposal_id,
            user_id=getattr(user, "id"),
        )
    except TaskProposalStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_state", "message": str(exc)},
        ) from exc
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)
