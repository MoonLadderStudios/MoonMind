"""Typed deployment operation endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.deployment_operations import (
    DeploymentControllerObservation,
    DeploymentCurrentImage,
    DeploymentOperationError,
    DeploymentOperationsService,
    DeploymentRecentAction,
    DeploymentStackPolicy,
    DeploymentUpdateSubmission,
    RollbackEligibilityDecision,
    RollbackImageTarget,
    current_image_from_controller,
    mutable_references,
    resolve_current_deployment_image,
)
from moonmind.config.settings import settings
from moonmind.utils.build_info import resolve_moonmind_build_id
from moonmind.workflows.temporal import TemporalExecutionService
from moonmind.workflows.skills.deployment_tools import DEPLOYMENT_UPDATE_TOOL_NAME


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
    reason: str | None = None
    # One operator intent. Resubmitting it after a lost or failed response
    # reattaches to the same controller operation instead of starting another.
    request_id: str | None = Field(
        None, alias="requestId", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$"
    )


DeploymentActionStatus = Literal[
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "PARTIALLY_VERIFIED",
    "FAILED",
    "SUPERSEDED",
    "UNKNOWN",
]


class DeploymentUpdateResponse(BaseModel):
    """The accepted update, identified by its durable controller operation."""

    model_config = ConfigDict(populate_by_name=True)

    operation_id: str = Field(..., alias="operationId")
    owner: Literal["controller"]
    status: DeploymentActionStatus


class DeploymentControllerModel(BaseModel):
    """Whether the standalone controller is installed and answered this read."""

    installed: bool
    reachable: bool
    message: str | None = None


class DeploymentCurrentImageModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    requested_image: str | None = Field(None, alias="requestedImage")
    deployed_image: str | None = Field(None, alias="deployedImage")
    repository: str | None = None
    reference: str | None = None
    resolved_digest: str | None = Field(None, alias="resolvedDigest")
    source_run_id: str | None = Field(None, alias="sourceRunId")
    updated_at: str | None = Field(None, alias="updatedAt")
    evidence: Literal[
        "controller", "desired_state", "environment", "policy", "unavailable"
    ]


class DeploymentPolicyModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    repository: str
    default_reference: str = Field(..., alias="defaultReference")
    allowed_references: list[str] = Field(..., alias="allowedReferences")
    recent_tags: list[str] = Field(..., alias="recentTags")
    mutable_references: list[str] = Field(..., alias="mutableReferences")
    allowed_modes: list[str] = Field(..., alias="allowedModes")


class DeploymentStackStateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stack: str
    project_name: str = Field(..., alias="projectName")
    build_id: str | None = Field(None, alias="buildId")
    current_image: DeploymentCurrentImageModel = Field(..., alias="currentImage")
    latest_action: "DeploymentRecentActionModel | None" = Field(
        None, alias="latestAction"
    )
    recent_actions: list["DeploymentRecentActionModel"] = Field(
        default_factory=list, alias="recentActions"
    )
    controller: DeploymentControllerModel
    policy: DeploymentPolicyModel


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


class DeploymentAttemptModel(BaseModel):
    attempt: int
    error: str
    at: str | None = None


class DeploymentVerificationCheckModel(BaseModel):
    name: str
    status: str
    detail: str | None = None


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
    before_build_id: str | None = Field(None, alias="beforeBuildId")
    after_build_id: str | None = Field(None, alias="afterBuildId")
    rollback_eligibility: RollbackEligibilityModel | None = Field(
        None, alias="rollbackEligibility"
    )
    owner: Literal["controller", "workflow"] = "workflow"
    operation_id: str | None = Field(None, alias="operationId")
    installed_image: str | None = Field(None, alias="installedImage")
    error_summary: str | None = Field(None, alias="errorSummary")
    attempts: list[DeploymentAttemptModel] = Field(default_factory=list)
    attempt_group: int | None = Field(None, alias="attemptGroup")
    verification: list[DeploymentVerificationCheckModel] = Field(default_factory=list)
    retry_allowed: bool = Field(False, alias="retryAllowed")


class ImageTargetModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    repository: str
    allowed_references: list[str] = Field(..., alias="allowedReferences")
    recent_tags: list[str] = Field(..., alias="recentTags")
    digest_pinning_recommended: bool = Field(..., alias="digestPinningRecommended")
    allowed_modes: list[str] = Field(..., alias="allowedModes")


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
    # Authorization is checked after identity resolution in every mode:
    # the disabled local principal resolves with is_superuser, so no
    # mode-based bypass exists here.
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
        status_code=exc.status_code,
        detail={**exc.details, "code": exc.code, "message": exc.message},
    )


def _enum_text(value: object) -> str | None:
    resolved = getattr(value, "value", value)
    if resolved is None:
        return None
    text = str(resolved).strip()
    return text or None


def _iso_text(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    text = str(value).strip()
    return text or None


def _deployment_workflow_payload(record: object) -> dict[str, Any]:
    """Return the canonical workflow payload for a deployment execution record.

    ``create_execution`` normalizes legacy ``task`` initial parameters into the
    canonical ``parameters["workflow"]`` key, so deployment action readers must
    derive plan/operation metadata from ``workflow`` rather than ``task``.
    """

    parameters = getattr(record, "parameters", None)
    if not isinstance(parameters, dict):
        return {}
    workflow = parameters.get("workflow")
    return workflow if isinstance(workflow, dict) else {}


def _deployment_plan_inputs(record: object) -> dict[str, Any]:
    task = _deployment_workflow_payload(record)
    if not task:
        return {}
    plan = task.get("plan")
    if not isinstance(plan, list):
        return {}
    for step in plan:
        if not isinstance(step, dict):
            continue
        tool = step.get("tool")
        if not isinstance(tool, dict):
            continue
        if tool.get("name") != DEPLOYMENT_UPDATE_TOOL_NAME:
            continue
        inputs = step.get("inputs")
        return dict(inputs) if isinstance(inputs, dict) else {}
    return {}


def _deployment_operation(record: object) -> dict[str, Any]:
    task = _deployment_workflow_payload(record)
    if not task:
        return {}
    operation = task.get("operation")
    return dict(operation) if isinstance(operation, dict) else {}


def _artifact_url(ref: object) -> str | None:
    if not isinstance(ref, str) or not ref.strip():
        return None
    return f"/api/artifacts/{ref.strip()}"


def _rollback_target_from_summary(
    *,
    before_summary: str | None,
    policy: DeploymentStackPolicy,
) -> RollbackImageTarget | None:
    if not before_summary:
        return None
    candidate = before_summary.strip()
    prefix = f"{policy.repository}:"
    if not candidate.startswith(prefix):
        return None
    reference = candidate.removeprefix(prefix).strip()
    if not reference:
        return None
    try:
        policy_service = DeploymentOperationsService({policy.stack: policy})
        policy_service.validate_update_request(
            stack=policy.stack,
            repository=policy.repository,
            reference=reference,
            mode=policy.allowed_modes[0],
            reason="Validate earlier image",
        )
    except DeploymentOperationError:
        return None
    return RollbackImageTarget(repository=policy.repository, reference=reference)


def _recent_action_from_execution_record(
    record: object,
    *,
    policy: DeploymentStackPolicy,
) -> DeploymentRecentAction | None:
    inputs = _deployment_plan_inputs(record)
    if inputs.get("stack") != policy.stack:
        return None
    image = inputs.get("image")
    if not isinstance(image, dict):
        image = {}
    repository = str(image.get("repository") or policy.repository).strip()
    reference = str(image.get("reference") or "").strip()
    requested_image = f"{repository}:{reference}" if repository and reference else None
    operation = _deployment_operation(record)
    operation_kind = str(
        inputs.get("operationKind") or operation.get("kind") or "update"
    ).strip()
    state_text = (_enum_text(getattr(record, "state", None)) or "unknown").upper()
    close_status = _enum_text(getattr(record, "close_status", None))
    status_text = (close_status or state_text).upper()
    if operation_kind == "rollback":
        kind = "rollback"
    elif status_text in {"FAILED", "TERMINATED", "CANCELED", "CANCELLED", "FAILURE"}:
        kind = "failure"
    else:
        kind = "update"
    run_id = str(getattr(record, "run_id", "") or "").strip()
    action_id = (
        f"depupd_{run_id.replace('-', '')}"
        if run_id
        else str(getattr(record, "workflow_id", "deployment-update"))
    )
    artifact_refs = [
        ref for ref in getattr(record, "artifact_refs", []) or [] if isinstance(ref, str)
    ]
    before_summary = None
    after_summary = None
    after_build_id = None
    memo = getattr(record, "memo", None)
    if isinstance(memo, dict):
        before_summary = (
            memo.get("deploymentBeforeSummary")
            or memo.get("beforeSummary")
            or memo.get("before_summary")
        )
        after_summary = (
            memo.get("deploymentAfterSummary")
            or memo.get("afterSummary")
            or memo.get("after_summary")
            or memo.get("summary")
        )
        after_build_id = (
            memo.get("deploymentAfterBuildId")
            or memo.get("afterBuildId")
            or memo.get("after_build_id")
        )
    before_summary = str(before_summary).strip() if before_summary else None
    after_summary = str(after_summary).strip() if after_summary else None
    before_build_id = str(operation.get("beforeBuildId") or "").strip() or None
    after_build_id = str(after_build_id).strip() if after_build_id else None
    target_image = _rollback_target_from_summary(
        before_summary=before_summary,
        policy=policy,
    )
    evidence_ref = artifact_refs[0] if artifact_refs else None
    # Workflow-backed rows are read-only history: rolling back through the
    # retired workflow updater is unavailable. An earlier image is an
    # ordinary controller update.
    eligibility = RollbackEligibilityDecision(
        eligible=False,
        target_image=None,
        source_action_id=action_id,
        reason=(
            "Rollback through the retired workflow updater is unavailable; "
            + (
                f"update to {target_image.repository}:{target_image.reference} "
                "instead."
                if target_image
                else "update to an earlier image instead."
            )
        ),
        evidence_ref=evidence_ref,
    )
    return DeploymentRecentAction(
        id=action_id,
        kind=kind,
        status=status_text,
        requested_image=requested_image,
        resolved_digest=str(image.get("digest") or "").strip() or None,
        operator=str(getattr(record, "owner_id", "") or "").strip() or None,
        reason=str(inputs.get("reason") or "").strip() or None,
        started_at=_iso_text(getattr(record, "started_at", None)),
        completed_at=_iso_text(getattr(record, "closed_at", None)),
        run_detail_url=f"/workflows/{getattr(record, 'workflow_id', '')}",
        logs_artifact_url=_artifact_url(evidence_ref),
        raw_command_log_url=None,
        raw_command_log_permitted=False,
        run_id=run_id or None,
        before_summary=before_summary,
        after_summary=after_summary,
        before_build_id=before_build_id,
        after_build_id=after_build_id,
        rollback_eligibility=eligibility,
    )


async def _recent_actions_from_executions(
    *,
    execution_service: TemporalExecutionService,
    policy: DeploymentStackPolicy,
) -> tuple[DeploymentRecentAction, ...]:
    try:
        result = await execution_service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            integration=DEPLOYMENT_UPDATE_TOOL_NAME,
            page_size=10,
        )
    except Exception:
        return ()
    actions: list[DeploymentRecentAction] = []
    for record in getattr(result, "items", ()) or ():
        action = _recent_action_from_execution_record(record, policy=policy)
        if action is not None:
            actions.append(action)
    return tuple(actions)


def _recent_action_model(action: DeploymentRecentAction) -> DeploymentRecentActionModel:
    eligibility = action.rollback_eligibility
    return DeploymentRecentActionModel(
        id=action.id,
        kind=action.kind,
        status=action.status,
        requested_image=action.requested_image,
        resolved_digest=action.resolved_digest,
        operator=action.operator,
        reason=action.reason,
        started_at=action.started_at,
        completed_at=action.completed_at,
        run_detail_url=action.run_detail_url,
        logs_artifact_url=action.logs_artifact_url,
        raw_command_log_url=(
            action.raw_command_log_url
            if action.raw_command_log_permitted
            else None
        ),
        raw_command_log_permitted=action.raw_command_log_permitted,
        run_id=action.run_id,
        before_summary=action.before_summary,
        after_summary=action.after_summary,
        before_build_id=action.before_build_id,
        after_build_id=action.after_build_id,
        owner=action.owner,
        operation_id=action.operation_id,
        installed_image=action.installed_image,
        error_summary=action.error_summary,
        attempts=[
            DeploymentAttemptModel(attempt=item.attempt, error=item.error, at=item.at)
            for item in action.attempts
        ],
        attempt_group=action.attempt_group,
        verification=[
            DeploymentVerificationCheckModel(
                name=item.name, status=item.status, detail=item.detail
            )
            for item in action.verification
        ],
        retry_allowed=action.retry_allowed,
        rollback_eligibility=(
            RollbackEligibilityModel(
                eligible=eligibility.eligible,
                source_action_id=eligibility.source_action_id,
                target_image=(
                    RollbackImageTargetModel(
                        repository=eligibility.target_image.repository,
                        reference=eligibility.target_image.reference,
                    )
                    if eligibility.target_image
                    else None
                ),
                reason=eligibility.reason,
                evidence_ref=eligibility.evidence_ref,
            )
            if eligibility
            else None
        ),
    )


def _current_image_model(image: DeploymentCurrentImage) -> DeploymentCurrentImageModel:
    return DeploymentCurrentImageModel(
        requested_image=image.requested_image,
        deployed_image=image.deployed_image,
        repository=image.repository,
        reference=image.reference,
        resolved_digest=image.resolved_digest,
        source_run_id=image.source_run_id,
        updated_at=image.updated_at,
        evidence=image.evidence,
    )


def _policy_model(policy: DeploymentStackPolicy) -> DeploymentPolicyModel:
    return DeploymentPolicyModel(
        repository=policy.repository,
        default_reference=policy.configured_reference,
        allowed_references=list(policy.allowed_references),
        recent_tags=list(policy.recent_tags),
        mutable_references=list(mutable_references(policy)),
        allowed_modes=list(policy.allowed_modes),
    )


def _stack_state(
    policy: DeploymentStackPolicy,
    controller: DeploymentControllerObservation,
    recent_actions: tuple[DeploymentRecentAction, ...],
) -> DeploymentStackStateResponse:
    action_models = [_recent_action_model(action) for action in recent_actions]
    # A controller-confirmed installation is the current image; the legacy
    # desired state and environment describe only pre-controller updates.
    current_image = current_image_from_controller(
        controller.actions, policy
    ) or resolve_current_deployment_image(policy)
    return DeploymentStackStateResponse(
        stack=policy.stack,
        project_name=policy.project_name,
        build_id=resolve_moonmind_build_id(),
        current_image=_current_image_model(current_image),
        latest_action=action_models[0] if action_models else None,
        recent_actions=action_models,
        controller=DeploymentControllerModel(
            installed=controller.installed,
            reachable=controller.reachable,
            message=controller.message,
        ),
        policy=_policy_model(policy),
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
        policy = service.validate_update_request(
            stack=payload.stack,
            repository=payload.image.repository,
            reference=payload.image.reference,
            mode=payload.mode,
            reason=payload.reason,
        )
        queued = await service.queue_update(
            policy=policy,
            submission=DeploymentUpdateSubmission(
                stack=policy.stack,
                repository=payload.image.repository,
                reference=payload.image.reference,
                reason=payload.reason,
                request_id=payload.request_id,
            ),
        )
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    return DeploymentUpdateResponse(**queued)


@router.post(
    "/operations/{operation_id}/retry",
    response_model=DeploymentUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_deployment_operation(
    operation_id: str = Path(
        ..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    ),
    service: DeploymentOperationsService = Depends(_get_deployment_service),
    user: User = Depends(get_current_user()),
) -> DeploymentUpdateResponse:
    """Request the controller's fresh bounded attempt for a failed operation."""
    _require_admin(user)
    try:
        retried = await service.retry_operation(operation_id)
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    return DeploymentUpdateResponse(**retried)


@router.get("/stacks/{stack}", response_model=DeploymentStackStateResponse)
async def get_deployment_stack_state(
    stack: str,
    service: DeploymentOperationsService = Depends(_get_deployment_service),
    execution_service: TemporalExecutionService = Depends(
        _get_temporal_execution_service
    ),
    _user: User = Depends(get_current_user()),
) -> DeploymentStackStateResponse:
    try:
        policy = service.get_policy(stack)
    except DeploymentOperationError as exc:
        raise _policy_error(exc) from exc
    controller = await asyncio.to_thread(service.observe_controller, policy.stack)
    # Controller operations first; workflow-backed updates stay readable as
    # history of the retired workflow updater.
    history = service.recent_actions(policy.stack) or await _recent_actions_from_executions(
        execution_service=execution_service,
        policy=policy,
    )
    return _stack_state(policy, controller, (*controller.actions, *history))


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
                allowedModes=list(policy.allowed_modes),
            )
        ],
    )
