"""Pydantic schemas for Agent Queue REST endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from moonmind.workflows.agent_queue import models


class CreateJobRequest(BaseModel):
    """Request body for queue job creation."""

    model_config = ConfigDict(populate_by_name=True)

    type: str = Field(..., alias="type")
    priority: int = Field(0, alias="priority")
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    affinity_key: Optional[str] = Field(None, alias="affinityKey")
    max_attempts: int = Field(3, alias="maxAttempts", ge=1)


class ClaimJobRequest(BaseModel):
    """Request body for claiming queue jobs."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    lease_seconds: int = Field(..., alias="leaseSeconds", ge=1)
    allowed_types: Optional[list[str]] = Field(None, alias="allowedTypes")
    worker_capabilities: Optional[list[str]] = Field(None, alias="workerCapabilities")


class HeartbeatRequest(BaseModel):
    """Request body for lease extension."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    lease_seconds: int = Field(..., alias="leaseSeconds", ge=1)


class CompleteJobRequest(BaseModel):
    """Request body for job completion."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    result_summary: Optional[str] = Field(None, alias="resultSummary")


class FailJobRequest(BaseModel):
    """Request body for job failure."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    error_message: str = Field(..., alias="errorMessage")
    retryable: bool = Field(False, alias="retryable")


class CancelJobRequest(BaseModel):
    """Request body for cancellation requests."""

    model_config = ConfigDict(populate_by_name=True)

    reason: Optional[str] = Field(None, alias="reason")


class CancelJobAckRequest(BaseModel):
    """Request body for worker cancellation acknowledgement."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    message: Optional[str] = Field(None, alias="message")


class AppendJobEventRequest(BaseModel):
    """Request body for appending one queue job event."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    level: models.AgentJobEventLevel = Field(
        models.AgentJobEventLevel.INFO, alias="level"
    )
    message: str = Field(..., alias="message")
    payload: Optional[dict[str, Any]] = Field(None, alias="payload")


class CreateTaskRunLiveSessionRequest(BaseModel):
    """Request body for creating/enabling task-run live sessions."""

    model_config = ConfigDict(populate_by_name=True)


class GrantTaskRunLiveSessionWriteRequest(BaseModel):
    """Request body for temporary RW live-session reveal."""

    model_config = ConfigDict(populate_by_name=True)

    ttl_minutes: Optional[int] = Field(None, alias="ttlMinutes", ge=1, le=240)


class RevokeTaskRunLiveSessionRequest(BaseModel):
    """Request body for revoking one task-run live session."""

    model_config = ConfigDict(populate_by_name=True)

    reason: Optional[str] = Field(None, alias="reason")


class TaskRunControlRequest(BaseModel):
    """Request body for pause/resume/takeover controls."""

    model_config = ConfigDict(populate_by_name=True)

    action: str = Field(..., alias="action")


class TaskRunOperatorMessageRequest(BaseModel):
    """Request body for operator message submissions."""

    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(..., alias="message")


class WorkerReportTaskRunLiveSessionRequest(BaseModel):
    """Worker-authenticated report/update payload for task-run live sessions."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    worker_hostname: Optional[str] = Field(None, alias="workerHostname")
    status: models.AgentJobLiveSessionStatus = Field(..., alias="status")
    provider: Optional[models.AgentJobLiveSessionProvider] = Field(None, alias="provider")
    attach_ro: Optional[str] = Field(None, alias="attachRo")
    attach_rw: Optional[str] = Field(None, alias="attachRw")
    web_ro: Optional[str] = Field(None, alias="webRo")
    web_rw: Optional[str] = Field(None, alias="webRw")
    tmate_session_name: Optional[str] = Field(None, alias="tmateSessionName")
    tmate_socket_path: Optional[str] = Field(None, alias="tmateSocketPath")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    error_message: Optional[str] = Field(None, alias="errorMessage")


class CreateWorkerTokenRequest(BaseModel):
    """Request payload for creating a worker token."""

    model_config = ConfigDict(populate_by_name=True)

    worker_id: str = Field(..., alias="workerId")
    description: Optional[str] = Field(None, alias="description")
    allowed_repositories: Optional[list[str]] = Field(None, alias="allowedRepositories")
    allowed_job_types: Optional[list[str]] = Field(None, alias="allowedJobTypes")
    capabilities: Optional[list[str]] = Field(None, alias="capabilities")


class JobModel(BaseModel):
    """Serialized queue job model returned by API endpoints."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    type: str = Field(..., alias="type")
    status: models.AgentJobStatus = Field(..., alias="status")
    priority: int = Field(..., alias="priority")
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    created_by_user_id: Optional[UUID] = Field(None, alias="createdByUserId")
    requested_by_user_id: Optional[UUID] = Field(None, alias="requestedByUserId")
    cancel_requested_by_user_id: Optional[UUID] = Field(
        None,
        alias="cancelRequestedByUserId",
    )
    cancel_requested_at: Optional[datetime] = Field(None, alias="cancelRequestedAt")
    cancel_reason: Optional[str] = Field(None, alias="cancelReason")
    affinity_key: Optional[str] = Field(None, alias="affinityKey")
    claimed_by: Optional[str] = Field(None, alias="claimedBy")
    lease_expires_at: Optional[datetime] = Field(None, alias="leaseExpiresAt")
    next_attempt_at: Optional[datetime] = Field(None, alias="nextAttemptAt")
    attempt: int = Field(..., alias="attempt")
    max_attempts: int = Field(..., alias="maxAttempts")
    result_summary: Optional[str] = Field(None, alias="resultSummary")
    error_message: Optional[str] = Field(None, alias="errorMessage")
    artifacts_path: Optional[str] = Field(None, alias="artifactsPath")
    started_at: Optional[datetime] = Field(None, alias="startedAt")
    finished_at: Optional[datetime] = Field(None, alias="finishedAt")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")


class ClaimJobResponse(BaseModel):
    """Claim endpoint response envelope."""

    model_config = ConfigDict(populate_by_name=True)

    job: Optional[JobModel] = Field(None, alias="job")


class JobListResponse(BaseModel):
    """List endpoint response envelope."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[JobModel] = Field(default_factory=list, alias="items")


class ArtifactModel(BaseModel):
    """Serialized queue artifact metadata model returned by API endpoints."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    job_id: UUID = Field(..., alias="jobId")
    name: str = Field(..., alias="name")
    content_type: Optional[str] = Field(None, alias="contentType")
    size_bytes: int = Field(..., alias="sizeBytes")
    digest: Optional[str] = Field(None, alias="digest")
    storage_path: str = Field(..., alias="storagePath")
    created_at: datetime = Field(..., alias="createdAt")


class ArtifactListResponse(BaseModel):
    """List endpoint response envelope for queue artifacts."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ArtifactModel] = Field(default_factory=list, alias="items")


class JobEventModel(BaseModel):
    """Serialized queue event model."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    job_id: UUID = Field(..., alias="jobId")
    level: models.AgentJobEventLevel = Field(..., alias="level")
    message: str = Field(..., alias="message")
    payload: Optional[dict[str, Any]] = Field(None, alias="payload")
    created_at: datetime = Field(..., alias="createdAt")


class JobEventListResponse(BaseModel):
    """List endpoint response for queue job events."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[JobEventModel] = Field(default_factory=list, alias="items")


class TaskRunLiveSessionModel(BaseModel):
    """Serialized live-session state for one task run."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    task_run_id: UUID = Field(..., alias="taskRunId")
    provider: models.AgentJobLiveSessionProvider = Field(..., alias="provider")
    status: models.AgentJobLiveSessionStatus = Field(..., alias="status")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    ready_at: Optional[datetime] = Field(None, alias="readyAt")
    ended_at: Optional[datetime] = Field(None, alias="endedAt")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    rw_granted_until: Optional[datetime] = Field(None, alias="rwGrantedUntil")
    worker_id: Optional[str] = Field(None, alias="workerId")
    worker_hostname: Optional[str] = Field(None, alias="workerHostname")
    tmate_session_name: Optional[str] = Field(None, alias="tmateSessionName")
    tmate_socket_path: Optional[str] = Field(None, alias="tmateSocketPath")
    attach_ro: Optional[str] = Field(None, alias="attachRo")
    web_ro: Optional[str] = Field(None, alias="webRo")
    last_heartbeat_at: Optional[datetime] = Field(None, alias="lastHeartbeatAt")
    error_message: Optional[str] = Field(None, alias="errorMessage")


class TaskRunLiveSessionResponse(BaseModel):
    """Response envelope for task-run live session fetch/create operations."""

    model_config = ConfigDict(populate_by_name=True)

    session: TaskRunLiveSessionModel = Field(..., alias="session")


class TaskRunLiveSessionWriteGrantResponse(BaseModel):
    """Response payload for temporary RW live-session grants."""

    model_config = ConfigDict(populate_by_name=True)

    session: TaskRunLiveSessionModel = Field(..., alias="session")
    attach_rw: str = Field(..., alias="attachRw")
    web_rw: Optional[str] = Field(None, alias="webRw")
    granted_until: datetime = Field(..., alias="grantedUntil")


class TaskRunControlEventModel(BaseModel):
    """Serialized task-run control event."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    task_run_id: UUID = Field(..., alias="taskRunId")
    actor_user_id: Optional[UUID] = Field(None, alias="actorUserId")
    action: str = Field(..., alias="action")
    metadata_json: Optional[dict[str, Any]] = Field(None, alias="metadataJson")
    created_at: datetime = Field(..., alias="createdAt")


class WorkerTokenModel(BaseModel):
    """Serialized worker token metadata."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: UUID = Field(..., alias="id")
    worker_id: str = Field(..., alias="workerId")
    description: Optional[str] = Field(None, alias="description")
    allowed_repositories: Optional[list[str]] = Field(None, alias="allowedRepositories")
    allowed_job_types: Optional[list[str]] = Field(None, alias="allowedJobTypes")
    capabilities: Optional[list[str]] = Field(None, alias="capabilities")
    is_active: bool = Field(..., alias="isActive")
    created_at: datetime = Field(..., alias="createdAt")


class WorkerTokenCreateResponse(BaseModel):
    """Create worker token response with one-time raw token value."""

    model_config = ConfigDict(populate_by_name=True)

    token: str = Field(..., alias="token")
    worker_token: WorkerTokenModel = Field(..., alias="workerToken")


class WorkerTokenListResponse(BaseModel):
    """List response for worker token metadata."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[WorkerTokenModel] = Field(default_factory=list, alias="items")


class MigrationFailureBucketModel(BaseModel):
    """Failure count grouped by runtime and stage."""

    model_config = ConfigDict(populate_by_name=True)

    runtime: str = Field(..., alias="runtime")
    stage: str = Field(..., alias="stage")
    count: int = Field(..., alias="count")


class MigrationPublishOutcomesModel(BaseModel):
    """Publish outcome totals and rates for task migration telemetry."""

    model_config = ConfigDict(populate_by_name=True)

    requested: int = Field(..., alias="requested")
    published: int = Field(..., alias="published")
    skipped: int = Field(..., alias="skipped")
    failed: int = Field(..., alias="failed")
    unknown: int = Field(..., alias="unknown")
    published_rate: float = Field(..., alias="publishedRate")
    skipped_rate: float = Field(..., alias="skippedRate")
    failed_rate: float = Field(..., alias="failedRate")


class MigrationTelemetryResponse(BaseModel):
    """Queue migration telemetry summary response."""

    model_config = ConfigDict(populate_by_name=True)

    generated_at: datetime = Field(..., alias="generatedAt")
    window_hours: int = Field(..., alias="windowHours")
    total_jobs: int = Field(..., alias="totalJobs")
    legacy_job_submissions: int = Field(..., alias="legacyJobSubmissions")
    events_truncated: bool = Field(..., alias="eventsTruncated")
    job_volume_by_type: dict[str, int] = Field(..., alias="jobVolumeByType")
    failure_counts_by_runtime_stage: list[MigrationFailureBucketModel] = Field(
        default_factory=list,
        alias="failureCountsByRuntimeStage",
    )
    publish_outcomes: MigrationPublishOutcomesModel = Field(
        ...,
        alias="publishOutcomes",
    )
