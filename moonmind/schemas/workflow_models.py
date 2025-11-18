"""Pydantic schemas for the Spec Kit workflow API."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_service.db.models import (
    OrchestratorPlanOrigin,
    OrchestratorPlanStep,
    OrchestratorPlanStepStatus,
    OrchestratorRunArtifactType,
    OrchestratorRunPriority,
    OrchestratorRunStatus,
    OrchestratorTaskState,
    SpecWorkflowTaskName,
)
from moonmind.workflows.speckit_celery import models


class WorkflowTaskStateModel(BaseModel):
    """Schema for individual Celery task states."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[UUID] = Field(None, alias="id")
    task_name: str = Field(..., alias="taskName")
    status: models.SpecWorkflowTaskStatus = Field(..., alias="status")
    attempt: int = Field(..., alias="attempt")
    payload: dict[str, Any] | None = Field(None, alias="payload")
    message: Optional[str] = Field(None, alias="message")
    artifact_paths: list[str] = Field(default_factory=list, alias="artifactPaths")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")


class WorkflowTaskSummaryModel(BaseModel):
    """Schema capturing the latest state per workflow task."""

    model_config = ConfigDict(populate_by_name=True)

    task_name: str = Field(..., alias="taskName")
    status: models.SpecWorkflowTaskStatus = Field(..., alias="status")
    attempt: int = Field(..., alias="attempt")
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")


class WorkflowArtifactModel(BaseModel):
    """Schema for stored workflow artifacts."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[UUID] = Field(None, alias="id")
    artifact_type: models.WorkflowArtifactType = Field(..., alias="artifactType")
    path: str = Field(..., alias="path")
    content_type: Optional[str] = Field(None, alias="contentType")
    size_bytes: Optional[int] = Field(None, alias="sizeBytes")
    digest: Optional[str] = Field(None, alias="digest")
    created_at: datetime | None = Field(None, alias="createdAt")


class WorkflowCredentialAuditModel(BaseModel):
    """Schema describing credential validation results."""

    model_config = ConfigDict(populate_by_name=True)

    codex_status: models.CodexCredentialStatus = Field(..., alias="codexStatus")
    github_status: models.GitHubCredentialStatus = Field(..., alias="githubStatus")
    checked_at: datetime | None = Field(None, alias="checkedAt")
    notes: Optional[str] = Field(None, alias="notes")


class SpecWorkflowRunModel(BaseModel):
    """Full representation of a workflow run exposed via the API."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    feature_key: str = Field(..., alias="featureKey")
    status: models.SpecWorkflowRunStatus = Field(..., alias="status")
    phase: models.SpecWorkflowRunPhase = Field(..., alias="phase")
    repository: Optional[str] = Field(None, alias="repository")
    branch_name: Optional[str] = Field(None, alias="branchName")
    pr_url: Optional[str] = Field(None, alias="prUrl")
    codex_task_id: Optional[str] = Field(None, alias="codexTaskId")
    codex_queue: Optional[str] = Field(None, alias="codexQueue")
    codex_volume: Optional[str] = Field(None, alias="codexVolume")
    codex_preflight_status: Optional[models.CodexPreflightStatus] = Field(
        None, alias="codexPreflightStatus"
    )
    codex_preflight_message: Optional[str] = Field(None, alias="codexPreflightMessage")
    codex_logs_path: Optional[str] = Field(None, alias="codexLogsPath")
    codex_patch_path: Optional[str] = Field(None, alias="codexPatchPath")
    celery_chain_id: Optional[str] = Field(None, alias="celeryChainId")
    requested_by: Optional[UUID] = Field(None, alias="requestedBy")
    created_by: Optional[UUID] = Field(None, alias="createdBy")
    current_task_name: Optional[SpecWorkflowTaskName] = Field(
        None, alias="currentTaskName"
    )
    started_at: datetime | None = Field(None, alias="startedAt")
    finished_at: datetime | None = Field(None, alias="finishedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    artifacts_path: Optional[str] = Field(None, alias="artifactsPath")
    created_at: datetime | None = Field(None, alias="createdAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")
    tasks: list[WorkflowTaskStateModel] = Field(default_factory=list, alias="tasks")
    task_summary: list[WorkflowTaskSummaryModel] = Field(
        default_factory=list, alias="taskSummary"
    )
    artifacts: list[WorkflowArtifactModel] = Field(
        default_factory=list, alias="artifacts"
    )
    credential_audit: WorkflowCredentialAuditModel | None = Field(
        None, alias="credentialAudit"
    )


class CreateWorkflowRunRequest(BaseModel):
    """Request payload for triggering a workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    repository: str = Field(
        ..., alias="repository", pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )
    feature_key: Optional[str] = Field(None, alias="featureKey")
    force_phase: Optional[Literal["discover", "submit", "apply", "publish"]] = Field(
        None, alias="forcePhase"
    )
    created_by: Optional[UUID] = Field(None, alias="createdBy")
    notes: Optional[str] = Field(None, alias="notes", max_length=1024)


class WorkflowRunCollectionResponse(BaseModel):
    """Envelope returned when listing workflow runs."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[SpecWorkflowRunModel] = Field(default_factory=list, alias="items")
    next_cursor: Optional[str] = Field(None, alias="nextCursor")


class CodexShardHealthModel(BaseModel):
    """Schema describing the health of a Codex shard and its auth volume."""

    model_config = ConfigDict(populate_by_name=True)

    queue_name: str = Field(..., alias="queueName")
    status: models.CodexWorkerShardStatus = Field(..., alias="status")
    hash_modulo: int = Field(..., alias="hashModulo", ge=1)
    worker_hostname: Optional[str] = Field(None, alias="workerHostname")
    volume_name: Optional[str] = Field(None, alias="volumeName")
    volume_status: Optional[models.CodexAuthVolumeStatus] = Field(
        None, alias="volumeStatus"
    )
    volume_last_verified_at: datetime | None = Field(None, alias="volumeLastVerifiedAt")
    volume_worker_affinity: Optional[str] = Field(None, alias="volumeWorkerAffinity")
    volume_notes: Optional[str] = Field(None, alias="volumeNotes")
    latest_run_id: Optional[UUID] = Field(None, alias="latestRunId")
    latest_run_status: Optional[models.SpecWorkflowRunStatus] = Field(
        None, alias="latestRunStatus"
    )
    latest_preflight_status: Optional[models.CodexPreflightStatus] = Field(
        None, alias="latestPreflightStatus"
    )
    latest_preflight_message: Optional[str] = Field(
        None, alias="latestPreflightMessage"
    )
    latest_preflight_checked_at: datetime | None = Field(
        None, alias="latestPreflightCheckedAt"
    )


class CodexShardListResponse(BaseModel):
    """Envelope returned when listing Codex shard health."""

    model_config = ConfigDict(populate_by_name=True)

    shards: list[CodexShardHealthModel] = Field(default_factory=list, alias="shards")


class CodexPreflightRequest(BaseModel):
    """Request payload accepted by the Codex pre-flight endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    affinity_key: Optional[str] = Field(None, alias="affinityKey")
    force_refresh: bool = Field(False, alias="forceRefresh")


class CodexPreflightResultModel(BaseModel):
    """Response returned from the Codex pre-flight endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: UUID = Field(..., alias="runId")
    queue_name: Optional[str] = Field(None, alias="queueName")
    volume_name: Optional[str] = Field(None, alias="volumeName")
    status: models.CodexPreflightStatus = Field(..., alias="status")
    checked_at: datetime | None = Field(None, alias="checkedAt")
    message: Optional[str] = Field(None, alias="message")


class RetryWorkflowMode(str, enum.Enum):
    """Retry semantics supported by the workflow API."""

    RESUME_FAILED_TASK = "resume_failed_task"
    RESTART_FROM_DISCOVERY = "restart_from_discovery"


class RetryWorkflowRunRequest(BaseModel):
    """Request payload for retrying a failed workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    mode: RetryWorkflowMode = Field(
        RetryWorkflowMode.RESUME_FAILED_TASK, alias="mode"
    )
    notes: Optional[str] = Field(None, alias="notes", max_length=1024)


class OrchestratorApprovalStatus(str, enum.Enum):
    """High-level approval state surfaced in API responses."""

    NOT_REQUIRED = "not_required"
    AWAITING = "awaiting"
    GRANTED = "granted"
    EXPIRED = "expired"


class OrchestratorPlanStepDefinition(BaseModel):
    """Plan step definition shared with clients."""

    model_config = ConfigDict(populate_by_name=True)

    name: OrchestratorPlanStep = Field(..., alias="name")
    parameters: dict[str, Any] | None = Field(default=None, alias="parameters")


class OrchestratorActionPlanModel(BaseModel):
    """Serialized action plan returned via the API."""

    model_config = ConfigDict(populate_by_name=True)

    plan_id: UUID = Field(..., alias="planId")
    generated_at: datetime | None = Field(None, alias="generatedAt")
    generated_by: OrchestratorPlanOrigin = Field(
        OrchestratorPlanOrigin.SYSTEM, alias="generatedBy"
    )
    steps: list[OrchestratorPlanStepDefinition] = Field(
        default_factory=list, alias="steps"
    )


class OrchestratorRunArtifactModel(BaseModel):
    """Artifact metadata surfaced to orchestrator clients."""

    model_config = ConfigDict(populate_by_name=True)

    artifact_id: UUID = Field(..., alias="artifactId")
    type: OrchestratorRunArtifactType = Field(..., alias="type")
    path: str = Field(..., alias="path")
    checksum: Optional[str] = Field(None, alias="checksum")
    size_bytes: Optional[int] = Field(None, alias="sizeBytes")
    created_at: datetime | None = Field(None, alias="createdAt")


class OrchestratorPlanStepStateModel(BaseModel):
    """Execution status for a concrete plan step."""

    model_config = ConfigDict(populate_by_name=True)

    name: OrchestratorPlanStep = Field(..., alias="name")
    status: OrchestratorPlanStepStatus | None = Field(None, alias="status")
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    celery_task_id: Optional[str] = Field(None, alias="celeryTaskId")
    celery_state: Optional[OrchestratorTaskState] = Field(None, alias="celeryState")
    message: Optional[str] = Field(None, alias="message")
    artifact_refs: list[UUID] = Field(default_factory=list, alias="artifactRefs")


class OrchestratorRunSummaryModel(BaseModel):
    """Compact representation of an orchestrator run."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: UUID = Field(..., alias="runId")
    status: OrchestratorRunStatus = Field(..., alias="status")
    priority: OrchestratorRunPriority = Field(
        OrchestratorRunPriority.NORMAL, alias="priority"
    )
    target_service: str = Field(..., alias="targetService")
    instruction: str = Field(..., alias="instruction")
    queued_at: datetime | None = Field(None, alias="queuedAt")
    started_at: datetime | None = Field(None, alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")
    approval_required: bool = Field(False, alias="approvalRequired")
    approval_status: OrchestratorApprovalStatus = Field(
        OrchestratorApprovalStatus.NOT_REQUIRED, alias="approvalStatus"
    )


class OrchestratorRunDetailModel(OrchestratorRunSummaryModel):
    """Full orchestrator run payload including plan and artifacts."""

    action_plan: Optional[OrchestratorActionPlanModel] = Field(None, alias="actionPlan")
    steps: list[OrchestratorPlanStepStateModel] = Field(
        default_factory=list, alias="steps"
    )
    artifacts: list[OrchestratorRunArtifactModel] = Field(
        default_factory=list, alias="artifacts"
    )
    metrics_snapshot: dict[str, Any] | None = Field(None, alias="metricsSnapshot")


class OrchestratorRunListResponse(BaseModel):
    """Response wrapper for run listings."""

    model_config = ConfigDict(populate_by_name=True)

    runs: list[OrchestratorRunSummaryModel] = Field(default_factory=list, alias="runs")


class OrchestratorArtifactListResponse(BaseModel):
    """Response wrapper for artifact listings."""

    model_config = ConfigDict(populate_by_name=True)

    artifacts: list[OrchestratorRunArtifactModel] = Field(
        default_factory=list, alias="artifacts"
    )


class OrchestratorCreateRunRequest(BaseModel):
    """Request payload for queueing orchestrator runs."""

    model_config = ConfigDict(populate_by_name=True)

    instruction: str = Field(..., alias="instruction")
    target_service: str = Field(..., alias="targetService")
    approval_token: Optional[str] = Field(None, alias="approvalToken")
    priority: OrchestratorRunPriority = Field(
        OrchestratorRunPriority.NORMAL, alias="priority"
    )


class OrchestratorApprovalActorModel(BaseModel):
    """Identity payload embedded inside approval requests."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., alias="id")
    role: str = Field(..., alias="role")


class OrchestratorApprovalRequest(BaseModel):
    """Body accepted by the approval endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    approver: OrchestratorApprovalActorModel = Field(..., alias="approver")
    token: str = Field(..., alias="token")
    expires_at: datetime | None = Field(None, alias="expiresAt")


class OrchestratorRetryStep(str, enum.Enum):
    """Valid resume points for orchestrator retries."""

    PATCH = "patch"
    BUILD = "build"
    RESTART = "restart"
    VERIFY = "verify"
    ROLLBACK = "rollback"


class OrchestratorRetryRequest(BaseModel):
    """Request payload for orchestrator retries."""

    model_config = ConfigDict(populate_by_name=True)

    resume_from_step: OrchestratorRetryStep | None = Field(None, alias="resumeFromStep")
    reason: Optional[str] = Field(None, alias="reason")


class SpecAutomationPhaseState(BaseModel):
    """Schema describing a single Spec Automation phase execution."""

    model_config = ConfigDict(from_attributes=True)

    phase: models.SpecAutomationPhase
    status: models.SpecAutomationTaskStatus
    attempt: int = Field(ge=1)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    metadata: dict[str, Any] | None = None


class SpecAutomationArtifactSummary(BaseModel):
    """Summary information for an automation artifact."""

    model_config = ConfigDict(from_attributes=True)

    artifact_id: UUID
    name: str
    artifact_type: models.SpecAutomationArtifactType
    storage_path: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    expires_at: datetime | None = None
    source_phase: models.SpecAutomationPhase | None = None


class SpecAutomationArtifactDetail(SpecAutomationArtifactSummary):
    """Extended artifact detail including download metadata."""

    download_url: str | None = None


class SpecAutomationRunResponse(BaseModel):
    """Acknowledgement returned when a run is accepted."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: models.SpecAutomationRunStatus
    accepted_at: datetime | None = None


class SpecAutomationRunDetail(BaseModel):
    """Detailed representation of a Spec Automation run."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: models.SpecAutomationRunStatus
    branch_name: str | None = None
    pull_request_url: str | None = None
    result_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    phases: list[SpecAutomationPhaseState] = Field(default_factory=list)
    artifacts: list[SpecAutomationArtifactSummary] = Field(default_factory=list)


__all__ = [
    "SpecWorkflowRunModel",
    "WorkflowTaskStateModel",
    "WorkflowTaskSummaryModel",
    "WorkflowArtifactModel",
    "WorkflowCredentialAuditModel",
    "CreateWorkflowRunRequest",
    "WorkflowRunCollectionResponse",
    "CodexShardHealthModel",
    "CodexShardListResponse",
    "CodexPreflightRequest",
    "CodexPreflightResultModel",
    "RetryWorkflowMode",
    "RetryWorkflowRunRequest",
    "OrchestratorApprovalStatus",
    "OrchestratorPlanStepDefinition",
    "OrchestratorActionPlanModel",
    "OrchestratorRunArtifactModel",
    "OrchestratorPlanStepStateModel",
    "OrchestratorRunSummaryModel",
    "OrchestratorRunDetailModel",
    "OrchestratorRunListResponse",
    "OrchestratorArtifactListResponse",
    "OrchestratorCreateRunRequest",
    "OrchestratorApprovalActorModel",
    "OrchestratorApprovalRequest",
    "OrchestratorRetryStep",
    "OrchestratorRetryRequest",
    "SpecAutomationPhaseState",
    "SpecAutomationArtifactSummary",
    "SpecAutomationArtifactDetail",
    "SpecAutomationRunResponse",
    "SpecAutomationRunDetail",
]
