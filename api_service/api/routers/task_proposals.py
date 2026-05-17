"""REST router for task proposal queue operations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user, get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.schemas.task_proposal_models import (
    TaskProposalCreateRequest,
    TaskProposalDismissRequest,
    TaskProposalListResponse,
    TaskProposalModel,
    TaskProposalOriginModel,
    TaskProposalProviderDecisionRequest,
    TaskProposalProviderDecisionResponse,
    TaskProposalPriorityRequest,
    TaskProposalPromoteRequest,
    TaskProposalPromoteResponse,
    TaskProposalTaskPreview,
)
from moonmind.workflows import get_task_proposal_service
from moonmind.workflows.temporal import TemporalExecutionService
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
from moonmind.workflows.task_proposals.delivery import ProviderDecisionEvent

router = APIRouter(prefix="/api/proposals", tags=["task-proposals"])

_PRESET_SOURCE_KINDS = frozenset({"preset-derived", "preset-include", "detached"})


async def _get_service(
    session: AsyncSession = Depends(get_async_session),
) -> TaskProposalService:
    return get_task_proposal_service(session)


def _build_task_preview(
    task_request: dict[str, object],
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
    priority = task_request.get("priority")
    max_attempts = task_request.get("maxAttempts")
    starting_branch = git.get("startingBranch")
    target_branch = git.get("targetBranch")
    raw_skills = task.get("skills") or payload.get("skills")
    task_skills = raw_skills if isinstance(raw_skills, list) else None
    instructions = task.get("instructions") or payload.get("instruction")
    authored_presets = task.get("authoredPresets")
    authored_preset_count = (
        len(authored_presets) if isinstance(authored_presets, list) else 0
    )
    raw_steps = task.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    step_source_kinds: list[str] = []
    preset_source_metadata: list[dict[str, object]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        source_node = step.get("source")
        if not isinstance(source_node, dict):
            continue
        kind = str(source_node.get("kind") or "").strip()
        if kind:
            step_source_kinds.append(kind)
        if kind in _PRESET_SOURCE_KINDS:
            metadata: dict[str, object] = {"kind": kind}
            for key in (
                "presetId",
                "presetSlug",
                "presetVersion",
                "includePath",
                "originalStepId",
            ):
                value = source_node.get(key)
                if value is None or value == "":
                    continue
                if key == "includePath" and not isinstance(value, list):
                    continue
                metadata[key] = value
            preset_source_metadata.append(metadata)
    preset_provenance = "manual"
    if authored_preset_count > 0:
        preset_provenance = "preserved-binding"
    elif any(kind in _PRESET_SOURCE_KINDS for kind in step_source_kinds):
        preset_provenance = "flattened-only"

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
    target_branch_value = (
        (str(target_branch).strip() or None) if target_branch is not None else None
    )
    instructions_value = (
        (str(instructions).strip() or None) if instructions is not None else None
    )

    return TaskProposalTaskPreview(
        repository=repository,
        runtimeMode=runtime_value,
        skillId=skill_value,
        taskSkills=task_skills,
        publishMode=publish_value,
        priority=priority if isinstance(priority, int) else None,
        maxAttempts=max_attempts if isinstance(max_attempts, int) else None,
        startingBranch=starting_value,
        targetBranch=target_branch_value,
        instructions=instructions_value,
        presetProvenance=preset_provenance,
        authoredPresetCount=authored_preset_count,
        stepSourceKinds=step_source_kinds,
        presetSourceMetadata=preset_source_metadata,
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

def _serialize_review_delivery(proposal: TaskProposal) -> dict[str, object]:
    provider_metadata = getattr(proposal, "provider_metadata", None)
    delivery_node = (
        provider_metadata.get("delivery")
        if isinstance(provider_metadata, dict)
        else None
    )
    delivery = dict(delivery_node) if isinstance(delivery_node, dict) else {}
    status_value = str(delivery.get("status") or "").strip()
    if not status_value:
        status_value = "delivered" if getattr(proposal, "external_url", None) else "pending"
    result: dict[str, object] = {
        "provider": getattr(proposal, "provider", "github") or "github",
        "status": status_value,
        "externalKey": getattr(proposal, "external_key", None),
        "externalUrl": getattr(proposal, "external_url", None),
        "deliveredAt": getattr(proposal, "delivered_at", None),
        "lastSyncedAt": getattr(proposal, "last_synced_at", None),
        "taskSnapshotRef": getattr(proposal, "task_snapshot_ref", None),
        "storedSnapshotNotice": bool(delivery.get("storedSnapshotNotice")),
    }
    for key in ("created", "duplicateSource", "warnings", "error"):
        if key in delivery:
            result[key] = delivery[key]
    return result


def _serialize_promotion_result(proposal: TaskProposal) -> dict[str, object] | None:
    provider_metadata = getattr(proposal, "provider_metadata", None)
    decision_rows = (
        provider_metadata.get("providerDecisions")
        if isinstance(provider_metadata, dict)
        else None
    )
    if not isinstance(decision_rows, list):
        return None
    for row in reversed(decision_rows):
        if not isinstance(row, dict):
            continue
        promoted_execution_id = str(row.get("promotedExecutionId") or "").strip()
        if not promoted_execution_id:
            continue
        result: dict[str, object] = {
            "promotedExecutionId": promoted_execution_id,
            "promotedExecutionUrl": f"/tasks/temporal/{promoted_execution_id}",
        }
        for source_key, target_key in (
            ("providerEventId", "providerEventId"),
            ("resultingExternalState", "resultingExternalState"),
            ("promotedAt", "promotedAt"),
        ):
            value = row.get(source_key)
            if value not in (None, ""):
                result[target_key] = value
        return result
    return None

def _serialize_proposal(
    proposal: TaskProposal, *, similar: list[TaskProposal] | None = None
) -> TaskProposalModel:
    origin_id_value = getattr(proposal, "origin_external_id", None)
    if origin_id_value is None and proposal.origin_id is not None:
        origin_id_value = str(proposal.origin_id)
    origin = TaskProposalOriginModel(
        source=proposal.origin_source,
        id=origin_id_value,
        metadata=proposal.origin_metadata or {},
    )
    preview = _build_task_preview(proposal.task_create_request or {})
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
        "provider": getattr(proposal, "provider", "github") or "github",
        "externalKey": getattr(proposal, "external_key", None),
        "externalUrl": getattr(proposal, "external_url", None),
        "deliveredAt": getattr(proposal, "delivered_at", None),
        "lastSyncedAt": getattr(proposal, "last_synced_at", None),
        "taskSnapshotRef": getattr(proposal, "task_snapshot_ref", None),
        "providerMetadata": getattr(proposal, "provider_metadata", None) or {},
        "resolvedPolicy": getattr(proposal, "resolved_policy", None) or {},
        "reviewDelivery": _serialize_review_delivery(proposal),
        "reviewPriority": proposal.review_priority,
        "priorityOverrideReason": proposal.priority_override_reason,
        "proposedByWorkerId": proposal.proposed_by_worker_id,
        "proposedByUserId": proposal.proposed_by_user_id,
        "promotedAt": proposal.promoted_at,
        "promotedByUserId": proposal.promoted_by_user_id,
        "decidedByUserId": proposal.decided_by_user_id,
        "decisionNote": proposal.decision_note,
        "createdAt": proposal.created_at,
        "updatedAt": proposal.updated_at,
        "origin": origin,
        "taskCreateRequest": proposal.task_create_request or {},
        "taskPreview": preview,
        "promotionResult": _serialize_promotion_result(proposal),
        "similar": _serialize_similar(similar),
    }
    return TaskProposalModel.model_validate(data)

async def _resolve_actor(
    *,
    user: Optional[User],
) -> tuple[Optional[UUID], Optional[str]]:
    if user is not None:
        return getattr(user, "id", None), None
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "authentication_required",
            "message": "User authentication is required.",
        },
    )

def _promotion_title(
    proposal: TaskProposal, initial_parameters: dict[str, object]
) -> str:
    task_node = initial_parameters.get("task")
    task = task_node if isinstance(task_node, dict) else {}
    instructions = str(task.get("instructions") or "")
    title_lines = [line.strip() for line in instructions.splitlines() if line.strip()]
    if title_lines:
        title = title_lines[0][:200]
    else:
        title = str(proposal.title or "").strip()[:200]
    return title or "Promoted Task"


def _decision_external_state(
    *,
    accepted: bool,
    decision: str | None,
    reason: str | None,
    existing: str | None,
    promoted_execution_id: str | None,
) -> str:
    if existing:
        return existing
    if not accepted:
        return reason or "ignored"
    if decision == "promote" and promoted_execution_id:
        return "promoted"
    if decision == "dismiss":
        return "dismissed"
    if decision == "defer":
        return "deferred"
    if decision == "reprioritize":
        return "reprioritized"
    if decision == "request_revision":
        return "revision_requested"
    return decision or "accepted"


@router.post("", response_model=TaskProposalModel, status_code=status.HTTP_201_CREATED)
async def create_proposal(
    payload: TaskProposalCreateRequest = Body(...),
    service: TaskProposalService = Depends(_get_service),
    user: Optional[User] = Depends(get_current_user_optional()),
) -> TaskProposalModel:
    proposed_by_user_id, proposed_by_worker_id = await _resolve_actor(user=user)
    try:
        origin_id_uuid: UUID | None = None
        origin_external_id: str | None = payload.origin.id
        if payload.origin.id:
            try:
                origin_id_uuid = UUID(str(payload.origin.id))
            except ValueError:
                origin_id_uuid = None
            else:
                origin_external_id = str(payload.origin.id)
        proposal = await service.create_proposal(
            title=payload.title,
            summary=payload.summary,
            category=payload.category,
            tags=payload.tags,
            task_create_request=payload.task_create_request,
            origin_source=payload.origin.source,
            origin_id=origin_id_uuid,
            origin_external_id=origin_external_id,
            origin_metadata=payload.origin.metadata,
            proposed_by_worker_id=proposed_by_worker_id,
            proposed_by_user_id=proposed_by_user_id,
            review_priority=payload.review_priority,
            provider=payload.provider,
            provider_metadata=payload.provider_metadata,
            resolved_policy=payload.resolved_policy,
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
    origin_id: Optional[UUID] = Query(None, alias="originId"),
    limit: int = Query(50, ge=1, le=200),
    cursor: Optional[str] = Query(None, alias="cursor"),
) -> TaskProposalListResponse:
    status_value = None
    if status_filter:
        normalized_status = status_filter.lower()
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
            origin_id=origin_id,
            cursor=cursor,
            limit=limit,
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

def _get_temporal_execution_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    from moonmind.config.settings import settings
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        integration_task_queue=settings.temporal.activity_integrations_task_queue,
        integration_poll_initial_seconds=(
            settings.temporal.integration_poll_initial_seconds
        ),
        integration_poll_max_seconds=settings.temporal.integration_poll_max_seconds,
        integration_poll_jitter_ratio=settings.temporal.integration_poll_jitter_ratio,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        run_continue_as_new_wait_cycle_threshold=(
            settings.temporal.run_continue_as_new_wait_cycle_threshold
        ),
    )


async def _create_promoted_execution(
    *,
    execution_service: TemporalExecutionService,
    proposal: TaskProposal,
    final_request: dict[str, object],
    user: User,
    idempotency_key: str,
) -> str:
    initial_parameters = dict(final_request.get("payload") or {})
    execution_record = await execution_service.create_execution(
        workflow_type="MoonMind.Run",
        owner_id=getattr(user, "id"),
        owner_type="user",
        title=_promotion_title(proposal, initial_parameters),
        input_artifact_ref=None,
        plan_artifact_ref=None,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters=initial_parameters,
        idempotency_key=idempotency_key,
        repository=proposal.repository,
        integration=None,
        summary=proposal.summary,
        start_delay=None,
        scheduled_for=None,
    )
    return execution_record.workflow_id

@router.post("/{proposal_id}/promote", response_model=TaskProposalPromoteResponse)
async def promote_proposal(
    *,
    proposal_id: UUID,
    payload: TaskProposalPromoteRequest = Body(
        default_factory=TaskProposalPromoteRequest
    ),
    service: TaskProposalService = Depends(_get_service),
    execution_service: TemporalExecutionService = Depends(
        _get_temporal_execution_service
    ),
    user: User = Depends(get_current_user()),
) -> TaskProposalPromoteResponse:
    try:
        runtime_mode_override = None
        if payload.runtime_mode:
            runtime_mode_override = payload.runtime_mode.strip()
            if not runtime_mode_override:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "invalid_request",
                        "message": "runtimeMode must be a non-empty string",
                    },
                )

        proposal, final_request = await service.promote_proposal(
            proposal_id=proposal_id,
            promoted_by_user_id=getattr(user, "id"),
            priority_override=payload.priority,
            max_attempts_override=payload.max_attempts,
            note=payload.note,
            runtime_mode_override=runtime_mode_override,
        )

        promoted_execution_id = await _create_promoted_execution(
            execution_service=execution_service,
            proposal=proposal,
            final_request=final_request,
            user=user,
            idempotency_key=f"proposal-promote-{proposal_id}",
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
        promoted_execution_id=promoted_execution_id,
    )


@router.post(
    "/{proposal_id}/provider-decision",
    response_model=TaskProposalProviderDecisionResponse,
)
async def provider_decision(
    *,
    proposal_id: UUID,
    payload: TaskProposalProviderDecisionRequest = Body(...),
    service: TaskProposalService = Depends(_get_service),
    execution_service: TemporalExecutionService = Depends(
        _get_temporal_execution_service
    ),
    user: User = Depends(get_current_user()),
) -> TaskProposalProviderDecisionResponse:
    try:
        result = await service.record_provider_decision_event(
            proposal_id=proposal_id,
            event=ProviderDecisionEvent(
                provider=payload.provider,
                external_key=payload.external_key,
                provider_event_id=payload.provider_event_id,
                actor=payload.actor,
                body=payload.body,
                action=payload.action,
                note=payload.note,
                observed_at=payload.observed_at or datetime.now(UTC),
                authenticity_verified=payload.authenticity.verified,
                runtime_mode=payload.runtime_mode,
                external_state=payload.external_state,
            ),
        )
        promoted_execution_id = result.promoted_execution_id
        if (
            result.accepted
            and result.decision == "promote"
            and not promoted_execution_id
        ):
            proposal, final_request = await service.promote_proposal(
                proposal_id=proposal_id,
                promoted_by_user_id=getattr(user, "id"),
                note=result.note,
                runtime_mode_override=result.runtime_mode,
            )
            promoted_execution_id = await _create_promoted_execution(
                execution_service=execution_service,
                proposal=proposal,
                final_request=final_request,
                user=user,
                idempotency_key=(
                    f"proposal-provider-{proposal_id}-{payload.provider_event_id}"
                ),
            )
            proposal = await service.attach_provider_decision_execution(
                proposal_id=proposal_id,
                provider_event_id=payload.provider_event_id,
                promoted_execution_id=promoted_execution_id,
            )
        else:
            proposal = await service.get_proposal(proposal_id)
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

    return TaskProposalProviderDecisionResponse(
        accepted=result.accepted,
        decision=result.decision,
        reason=result.reason,
        actor=result.actor,
        providerEventId=result.provider_event_id,
        note=result.note,
        priority=result.priority,
        deferUntil=result.defer_until,
        runtimeMode=result.runtime_mode,
        resultingExternalState=_decision_external_state(
            accepted=result.accepted,
            decision=result.decision,
            reason=result.reason,
            existing=result.external_state,
            promoted_execution_id=promoted_execution_id,
        ),
        promotedExecutionId=promoted_execution_id,
        proposal=_serialize_proposal(proposal),
    )


@router.get("/{proposal_id}/delivery", response_model=TaskProposalModel)
async def inspect_proposal_delivery(
    *,
    proposal_id: UUID,
    service: TaskProposalService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.get_proposal(proposal_id)
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)


@router.post("/{proposal_id}/redeliver", response_model=TaskProposalModel)
async def redeliver_proposal(
    *,
    proposal_id: UUID,
    service: TaskProposalService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.redeliver_proposal(proposal_id=proposal_id)
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


@router.post("/{proposal_id}/sync", response_model=TaskProposalModel)
async def sync_proposal_delivery(
    *,
    proposal_id: UUID,
    service: TaskProposalService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> TaskProposalModel:
    try:
        proposal = await service.sync_proposal_delivery(proposal_id=proposal_id)
    except TaskProposalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found", "message": str(exc)},
        ) from exc
    return _serialize_proposal(proposal)

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
