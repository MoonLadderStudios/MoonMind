"""Typed deployment operation endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.deployment_operations import (
    DeploymentOperationError,
    DeploymentOperationsService,
    DeploymentStackPolicy,
    DeploymentUpdateSubmission,
)
from moonmind.config.settings import settings
from moonmind.workflows.tasks.routing import TemporalSubmitDisabledError
from moonmind.workflows.temporal import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)


router = APIRouter(prefix="/api/v1/operations/deployment", tags=["deployment"])


class DeploymentImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    repository: str = Field(..., min_length=1)
    reference: str = Field(..., min_length=1)


class DeploymentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    stack: str = Field(..., min_length=1)
    image: DeploymentImageRequest
    mode: str = Field(..., min_length=1)
    remove_orphans: bool = Field(False, alias="removeOrphans")
    wait: bool = True
    run_smoke_check: bool = Field(False, alias="runSmokeCheck")
    pause_work: bool = Field(False, alias="pauseWork")
    prune_old_images: bool = Field(False, alias="pruneOldImages")
    reason: str = Field(..., min_length=1)
    operation_kind: Literal["update", "rollback"] = Field("update", alias="operationKind")
    rollback_source_action_id: str | None = Field(
        None, alias="rollbackSourceActionId"
    )
    confirmation: str | None = None


class DeploymentUpdateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    deployment_update_run_id: str = Field(..., alias="deploymentUpdateRunId")
    task_id: str = Field(..., alias="taskId")
    workflow_id: str = Field(..., alias="workflowId")
    status: Literal["QUEUED"]


class RunningImageModel(BaseModel):
    service: str
    image: str
    image_id: str | None = Field(None, alias="imageId")
    digest: str | None = None


class DeploymentServiceStateModel(BaseModel):
    name: str
    state: str
    health: str | None = None


class DeploymentStackStateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stack: str
    project_name: str = Field(..., alias="projectName")
    configured_image: str = Field(..., alias="configuredImage")
    running_images: list[RunningImageModel] = Field(..., alias="runningImages")
    services: list[DeploymentServiceStateModel]
    last_update_run_id: str | None = Field(None, alias="lastUpdateRunId")
    recent_actions: list["DeploymentRecentActionModel"] = Field(
        default_factory=list, alias="recentActions"
    )


class RollbackImageTargetModel(BaseModel):
    repository: str
    reference: str


class RollbackEligibilityModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    eligible: bool
    source_action_id: str | None = Field(None, alias="sourceActionId")
    target_image: RollbackImageTargetModel | None = Field(None, alias="targetImage")
    reason: str | None = None
    evidence_ref: str | None = Field(None, alias="evidenceRef")


class DeploymentRecentActionModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    kind: str
    status: str
    requested_image: str | None = Field(None, alias="requestedImage")
    resolved_digest: str | None = Field(None, alias="resolvedDigest")
    operator: str | None = None
    reason: str | None = None
    started_at: str | None = Field(None, alias="startedAt")
    completed_at: str | None = Field(None, alias="completedAt")
    run_detail_url: str | None = Field(None, alias="runDetailUrl")
    logs_artifact_url: str | None = Field(None, alias="logsArtifactUrl")
    raw_command_log_url: str | None = Field(None, alias="rawCommandLogUrl")
    raw_command_log_permitted: bool = Field(False, alias="rawCommandLogPermitted")
    run_id: str | None = Field(None, alias="runId")
    before_summary: str | None = Field(None, alias="beforeSummary")
    after_summary: str | None = Field(None, alias="afterSummary")
    rollback_eligibility: RollbackEligibilityModel | None = Field(
        None, alias="rollbackEligibility"
    )


class ImageTargetModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    repository: str
    allowed_references: list[str] = Field(..., alias="allowedReferences")
    recent_tags: list[str] = Field(..., alias="recentTags")
    digest_pinning_recommended: bool = Field(..., alias="digestPinningRecommended")


class ImageTargetsResponse(BaseModel):
    stack: str
    repositories: list[ImageTargetModel]


def _get_deployment_service() -> DeploymentOperationsService:
    return DeploymentOperationsService()


def _get_temporal_execution_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
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


def _require_admin(user: User) -> None:
    if bool(getattr(user, "is_superuser", False)):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "deployment_update_forbidden",
            "message": "Only administrators can submit deployment updates.",
            "failureClass": "authorization_failure",
        },
    )


def _policy_error(exc: DeploymentOperationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": exc.code, "message": exc.message},
    )


def _recent_action_model(action) -> DeploymentRecentActionModel:
    eligibility = action.rollback_eligibility
    return DeploymentRecentActionModel(
        id=action.id,
        kind=action.kind,
        status=action.status,
        requestedImage=action.requested_image,
        resolvedDigest=action.resolved_digest,
        operator=action.operator,
        reason=action.reason,
        startedAt=action.started_at,
        completedAt=action.completed_at,
        runDetailUrl=action.run_detail_url,
        logsArtifactUrl=action.logs_artifact_url,
        rawCommandLogUrl=(
            action.raw_command_log_url
            if action.raw_command_log_permitted
            else None
        ),
        rawCommandLogPermitted=action.raw_command_log_permitted,
        runId=action.run_id,
        beforeSummary=action.before_summary,
        afterSummary=action.after_summary,
        rollbackEligibility=(
            RollbackEligibilityModel(
                eligible=eligibility.eligible,
                sourceActionId=eligibility.source_action_id,
                targetImage=(
                    RollbackImageTargetModel(
                        repository=eligibility.target_image.repository,
                        reference=eligibility.target_image.reference,
                    )
                    if eligibility.target_image
                    else None
                ),
                reason=eligibility.reason,
                evidenceRef=eligibility.evidence_ref,
            )
            if eligibility
            else None
        ),
    )


def _stack_state(
    policy: DeploymentStackPolicy,
    service: DeploymentOperationsService,
) -> DeploymentStackStateResponse:
    return DeploymentStackStateResponse(
        stack=policy.stack,
        projectName=policy.project_name,
        configuredImage=policy.configured_image,
        runningImages=[
            RunningImageModel(
                service="api",
                image=policy.configured_image,
                imageId=None,
                digest=None,
            )
        ],
        services=[
            DeploymentServiceStateModel(
                name="api",
                state="unknown",
                health=None,
            )
        ],
        lastUpdateRunId=None,
        recentActions=[
            _recent_action_model(action)
            for action in service.recent_actions(policy.stack)
        ],
    )


@router.post(
    "/update",
    response_model=DeploymentUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_deployment_update(
    payload: DeploymentUpdateRequest,
    service: DeploymentOperationsService = Depends(_get_deployment_service),
    execution_service: TemporalExecutionService = Depends(_get_temporal_execution_service),
    user: User = Depends(get_current_user()),
) -> DeploymentUpdateResponse:
    _require_admin(user)
    try:
        policy = service.validate_update_request(
            stack=payload.stack,
            repository=payload.image.repository,
            reference=payload.image.reference,
            mode=payload.mode,
            reason=payload.reason,
            operation_kind=payload.operation_kind,
            confirmation=payload.confirmation,
            rollback_source_action_id=payload.rollback_source_action_id,
        )
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    try:
        queued = await service.queue_update(
            execution_service=execution_service,
            policy=policy,
            submission=DeploymentUpdateSubmission(
                stack=policy.stack,
                repository=payload.image.repository,
                reference=payload.image.reference,
                mode=payload.mode,
                remove_orphans=payload.remove_orphans,
                wait=payload.wait,
                run_smoke_check=payload.run_smoke_check,
                pause_work=payload.pause_work,
                prune_old_images=payload.prune_old_images,
                reason=payload.reason,
                requested_by_user_id=getattr(user, "id", None),
                operation_kind=payload.operation_kind,
                rollback_source_action_id=payload.rollback_source_action_id,
                confirmation=payload.confirmation,
            ),
        )
    except TemporalSubmitDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "temporal_submit_disabled", "message": str(exc)},
        ) from exc
    except TemporalExecutionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "deployment_update_queue_invalid", "message": str(exc)},
        ) from exc
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    return DeploymentUpdateResponse(**queued)


@router.get("/stacks/{stack}", response_model=DeploymentStackStateResponse)
async def get_deployment_stack_state(
    stack: str,
    service: DeploymentOperationsService = Depends(_get_deployment_service),
    _user: User = Depends(get_current_user()),
) -> DeploymentStackStateResponse:
    try:
        policy = service.get_policy(stack)
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    return _stack_state(policy, service)


@router.get("/image-targets", response_model=ImageTargetsResponse)
async def list_deployment_image_targets(
    stack: str = Query(..., min_length=1),
    service: DeploymentOperationsService = Depends(_get_deployment_service),
    _user: User = Depends(get_current_user()),
) -> ImageTargetsResponse:
    try:
        policy = service.get_policy(stack)
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    return ImageTargetsResponse(
        stack=policy.stack,
        repositories=[
            ImageTargetModel(
                repository=policy.repository,
                allowedReferences=list(policy.allowed_references),
                recentTags=list(policy.recent_tags),
                digestPinningRecommended=policy.allow_mutable_tags,
            )
        ],
    )
