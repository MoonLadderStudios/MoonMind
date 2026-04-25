"""Typed deployment operation endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from api_service.services.deployment_operations import (
    DeploymentOperationError,
    DeploymentOperationsService,
    DeploymentStackPolicy,
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


def _require_admin(user: User) -> None:
    if bool(getattr(user, "is_superuser", False)):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "deployment_update_forbidden",
            "message": "Only administrators can submit deployment updates.",
        },
    )


def _policy_error(exc: DeploymentOperationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": exc.code, "message": exc.message},
    )


def _stack_state(policy: DeploymentStackPolicy) -> DeploymentStackStateResponse:
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
    )


@router.post(
    "/update",
    response_model=DeploymentUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_deployment_update(
    payload: DeploymentUpdateRequest,
    service: DeploymentOperationsService = Depends(_get_deployment_service),
    user: User = Depends(get_current_user()),
) -> DeploymentUpdateResponse:
    _require_admin(user)
    try:
        service.validate_update_request(
            stack=payload.stack,
            repository=payload.image.repository,
            reference=payload.image.reference,
            mode=payload.mode,
            reason=payload.reason,
        )
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    return DeploymentUpdateResponse(**service.queue_update(stack=payload.stack))


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
    return _stack_state(policy)


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
