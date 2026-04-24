"""Pydantic schemas for the workflow API."""

from __future__ import annotations

import enum
import re
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api_service.db.models import WorkflowTaskName
from moonmind.schemas.agent_skill_models import SkillSelector
from moonmind.workflows.automation import models

_REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

def _validate_repository_slug(value: str) -> str:
    """Ensure repository strings avoid traversal sequences like ``../``."""

    if not _REPOSITORY_SLUG_PATTERN.fullmatch(value):
        raise ValueError("Repository must be in the form 'owner/repo'.")

    owner, repo = value.split("/", 1)
    for segment in (owner, repo):
        if segment in {".", ".."} or ".." in segment:
            raise ValueError(
                "Repository segments cannot contain path traversal tokens."
            )

    return value

class WorkflowTaskStateModel(BaseModel):
    """Schema for individual workflow task states."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[UUID] = Field(None, alias="id")
    task_name: str = Field(..., alias="taskName")
    status: models.WorkflowTaskStatus = Field(..., alias="status")
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
    status: models.WorkflowTaskStatus = Field(..., alias="status")
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

class WorkflowRunModel(BaseModel):
    """Full representation of a workflow run exposed via the API."""

    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(..., alias="id")
    feature_key: str = Field(..., alias="featureKey")
    status: models.WorkflowRunStatus = Field(..., alias="status")
    phase: models.WorkflowRunPhase = Field(..., alias="phase")
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
    legacy_chain_id: Optional[str] = Field(None, alias="legacyChainId")
    requested_by: Optional[UUID] = Field(None, alias="requestedBy")
    created_by: Optional[UUID] = Field(None, alias="createdBy")
    current_task_name: Optional[WorkflowTaskName] = Field(
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

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _validate_repository_slug(value)

class CreateWorkflowRunRequest(BaseModel):
    """Request payload for triggering a workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    repository: str = Field(..., alias="repository")
    feature_key: Optional[str] = Field(None, alias="featureKey")
    force_phase: Optional[Literal["discover", "submit", "apply", "publish"]] = Field(
        None, alias="forcePhase"
    )
    created_by: Optional[UUID] = Field(None, alias="createdBy")
    notes: Optional[str] = Field(None, alias="notes", max_length=1024)
    skills: Optional[SkillSelector] = Field(None, alias="skills")

    @field_validator("repository")
    @classmethod
    def _validate_repository(cls, value: str) -> str:
        return _validate_repository_slug(value)

class WorkflowRunCollectionResponse(BaseModel):
    """Envelope returned when listing workflow runs."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[WorkflowRunModel] = Field(default_factory=list, alias="items")
    next_cursor: Optional[str] = Field(None, alias="nextCursor")

class WorkflowTaskStateListResponse(BaseModel):
    """Envelope returned when listing task states for a workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: UUID = Field(..., alias="runId")
    tasks: list[WorkflowTaskStateModel] = Field(default_factory=list, alias="tasks")

class WorkflowArtifactListResponse(BaseModel):
    """Envelope returned when listing artifacts for a workflow run."""

    model_config = ConfigDict(populate_by_name=True)

    run_id: UUID = Field(..., alias="runId")
    artifacts: list[WorkflowArtifactModel] = Field(
        default_factory=list, alias="artifacts"
    )

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
    latest_run_status: Optional[models.WorkflowRunStatus] = Field(
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

    mode: RetryWorkflowMode = Field(RetryWorkflowMode.RESUME_FAILED_TASK, alias="mode")
    notes: Optional[str] = Field(None, alias="notes", max_length=1024)

class AutomationPhaseState(BaseModel):
    """Schema describing a single workflow automation phase execution."""

    model_config = ConfigDict(from_attributes=True)

    phase: models.AutomationPhase
    status: models.AutomationTaskStatus
    attempt: int = Field(ge=1)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    metadata: dict[str, Any] | None = None
    selected_skill: str | None = None
    adapter_id: str | None = None
    execution_path: Literal["skill", "direct_fallback", "direct_only"] | None = None
    used_skills: bool | None = None
    used_fallback: bool | None = None
    shadow_mode_requested: bool | None = None

class AutomationArtifactSummary(BaseModel):
    """Summary information for an automation artifact."""

    model_config = ConfigDict(from_attributes=True)

    artifact_id: UUID
    name: str
    artifact_type: models.AutomationArtifactType
    storage_path: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    expires_at: datetime | None = None
    source_phase: models.AutomationPhase | None = None

class AutomationArtifactDetail(AutomationArtifactSummary):
    """Extended artifact detail including download metadata."""

    download_url: str | None = None

class AutomationRunResponse(BaseModel):
    """Acknowledgement returned when a run is accepted."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: models.AutomationRunStatus
    accepted_at: datetime | None = None

class AutomationRunDetail(BaseModel):
    """Detailed representation of a workflow automation run."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: models.AutomationRunStatus
    branch_name: str | None = None
    pull_request_url: str | None = None
    result_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    phases: list[AutomationPhaseState] = Field(default_factory=list)
    artifacts: list[AutomationArtifactSummary] = Field(default_factory=list)

__all__ = [
    "WorkflowRunModel",
    "WorkflowTaskStateModel",
    "WorkflowTaskSummaryModel",
    "WorkflowArtifactModel",
    "WorkflowCredentialAuditModel",
    "CreateWorkflowRunRequest",
    "WorkflowRunCollectionResponse",
    "WorkflowTaskStateListResponse",
    "WorkflowArtifactListResponse",
    "CodexShardHealthModel",
    "CodexShardListResponse",
    "CodexPreflightRequest",
    "CodexPreflightResultModel",
    "RetryWorkflowMode",
    "RetryWorkflowRunRequest",
    "AutomationPhaseState",
    "AutomationArtifactSummary",
    "AutomationArtifactDetail",
    "AutomationRunResponse",
    "AutomationRunDetail",
]
