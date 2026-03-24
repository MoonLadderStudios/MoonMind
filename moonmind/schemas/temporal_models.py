"""Schemas for Temporal execution lifecycle APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPORTED_WORKFLOW_TYPES = ("MoonMind.Run", "MoonMind.ManifestIngest")
SUPPORTED_FAILURE_POLICIES = (
    "fail_fast",
    "continue_and_report",
    "best_effort",
)
SUPPORTED_UPDATE_NAMES = (
    "UpdateInputs",
    "SetTitle",
    "RequestRerun",
    "UpdateManifest",
    "SetConcurrency",
    "Pause",
    "Resume",
    "CancelNodes",
    "RetryNodes",
)
SUPPORTED_SIGNAL_NAMES = ("ExternalEvent", "Approve", "Pause", "Resume")

from moonmind.schemas.manifest_ingest_models import (
    ManifestExecutionPolicyModel,
    ManifestNodeCountsModel,
    RequestedByModel,
)

NormalizedIntegrationStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "canceled",
    "unknown",
]


class ScheduleParameters(BaseModel):
    """Inline scheduling parameters for deferred or recurring execution."""

    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["once", "recurring"] = Field(..., alias="mode")

    # mode=once fields
    scheduled_for: Optional[datetime] = Field(None, alias="scheduledFor")

    # mode=recurring fields
    name: Optional[str] = Field(None, alias="name")
    description: Optional[str] = Field(None, alias="description")
    cron: Optional[str] = Field(None, alias="cron")
    timezone: Optional[str] = Field(None, alias="timezone")
    enabled: bool = Field(True, alias="enabled")
    scope_type: Optional[Literal["personal", "global"]] = Field(
        None, alias="scopeType"
    )
    policy: Optional[dict[str, Any]] = Field(None, alias="policy")

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> "ScheduleParameters":
        if self.mode == "once" and self.scheduled_for is None:
            raise ValueError(
                "scheduledFor is required when schedule mode is 'once'"
            )
        if self.mode == "recurring" and not self.cron:
            raise ValueError(
                "cron is required when schedule mode is 'recurring'"
            )
        return self


class CreateExecutionRequest(BaseModel):
    """Request payload for starting a workflow execution."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: str = Field(
        ...,
        alias="workflowType",
        json_schema_extra={"enum": SUPPORTED_WORKFLOW_TYPES},
    )
    title: Optional[str] = Field(None, alias="title")
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    failure_policy: Optional[str] = Field(
        None,
        alias="failurePolicy",
        json_schema_extra={"enum": SUPPORTED_FAILURE_POLICIES},
    )
    initial_parameters: dict[str, Any] = Field(
        default_factory=dict, alias="initialParameters"
    )
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")
    schedule: Optional[ScheduleParameters] = Field(None, alias="schedule")

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "CreateExecutionRequest":
        if (
            self.workflow_type == "MoonMind.ManifestIngest"
            and not self.manifest_artifact_ref
        ):
            raise ValueError(
                "manifestArtifactRef is required when workflowType is "
                "MoonMind.ManifestIngest"
            )
        return self


class UpdateExecutionRequest(BaseModel):
    """Request payload for workflow updates."""

    model_config = ConfigDict(populate_by_name=True)

    update_name: str = Field(
        "UpdateInputs",
        alias="updateName",
        json_schema_extra={"enum": SUPPORTED_UPDATE_NAMES},
    )
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    parameters_patch: Optional[dict[str, Any]] = Field(None, alias="parametersPatch")
    title: Optional[str] = Field(None, alias="title")
    new_manifest_artifact_ref: Optional[str] = Field(
        None, alias="newManifestArtifactRef"
    )
    mode: Optional[Literal["REPLACE_FUTURE", "APPEND"]] = Field(None, alias="mode")
    max_concurrency: Optional[int] = Field(None, alias="maxConcurrency")
    node_ids: list[str] = Field(default_factory=list, alias="nodeIds")
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")


class SignalExecutionRequest(BaseModel):
    """Request payload for asynchronous workflow signals."""

    model_config = ConfigDict(populate_by_name=True)

    signal_name: str = Field(
        ...,
        alias="signalName",
        json_schema_extra={"enum": SUPPORTED_SIGNAL_NAMES},
    )
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    payload_artifact_ref: Optional[str] = Field(None, alias="payloadArtifactRef")


class ConfigureIntegrationMonitoringRequest(BaseModel):
    """Request payload for starting Temporal-side external monitoring."""

    model_config = ConfigDict(populate_by_name=True)

    integration_name: str = Field(..., alias="integrationName", min_length=1)
    correlation_id: Optional[str] = Field(None, alias="correlationId")
    external_operation_id: str = Field(..., alias="externalOperationId", min_length=1)
    normalized_status: NormalizedIntegrationStatus = Field(
        ..., alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    callback_supported: bool = Field(..., alias="callbackSupported")
    callback_correlation_key: Optional[str] = Field(
        None, alias="callbackCorrelationKey"
    )
    recommended_poll_seconds: Optional[int] = Field(
        None, alias="recommendedPollSeconds", ge=1
    )
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    result_refs: list[str] = Field(default_factory=list, alias="resultRefs")


class PollIntegrationRequest(BaseModel):
    """Request payload for polling updates while awaiting external completion."""

    model_config = ConfigDict(populate_by_name=True)

    normalized_status: NormalizedIntegrationStatus = Field(
        ..., alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    observed_at: Optional[datetime] = Field(None, alias="observedAt")
    recommended_poll_seconds: Optional[int] = Field(
        None, alias="recommendedPollSeconds", ge=1
    )
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    result_refs: list[str] = Field(default_factory=list, alias="resultRefs")
    completed_wait_cycles: int = Field(1, alias="completedWaitCycles", ge=0)


class IntegrationCallbackRequest(BaseModel):
    """Generic provider callback payload resolved through correlation storage."""

    model_config = ConfigDict(populate_by_name=True)

    source: Optional[str] = Field(None, alias="source")
    event_type: str = Field(..., alias="eventType", min_length=1)
    external_operation_id: Optional[str] = Field(None, alias="externalOperationId")
    provider_event_id: Optional[str] = Field(None, alias="providerEventId")
    normalized_status: Optional[NormalizedIntegrationStatus] = Field(
        None, alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    observed_at: Optional[datetime] = Field(None, alias="observedAt")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    payload_artifact_ref: Optional[str] = Field(None, alias="payloadArtifactRef")


class IntegrationStateModel(BaseModel):
    """Compact persisted state for a monitored external integration."""

    model_config = ConfigDict(populate_by_name=True)

    integration_name: str = Field(..., alias="integrationName")
    correlation_id: str = Field(..., alias="correlationId")
    external_operation_id: str = Field(..., alias="externalOperationId")
    normalized_status: NormalizedIntegrationStatus = Field(
        ..., alias="normalizedStatus"
    )
    provider_status: Optional[str] = Field(None, alias="providerStatus")
    started_at: datetime = Field(..., alias="startedAt")
    last_observed_at: datetime = Field(..., alias="lastObservedAt")
    monitor_attempt_count: int = Field(..., alias="monitorAttemptCount", ge=0)
    callback_supported: bool = Field(..., alias="callbackSupported")
    result_refs: list[str] = Field(default_factory=list, alias="resultRefs")
    callback_correlation_key: Optional[str] = Field(
        None, alias="callbackCorrelationKey"
    )
    provider_event_ids_seen: list[str] = Field(
        default_factory=list, alias="providerEventIdsSeen"
    )
    next_poll_at: Optional[datetime] = Field(None, alias="nextPollAt")
    poll_interval_seconds: Optional[int] = Field(None, alias="pollIntervalSeconds")
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )


class CancelExecutionRequest(BaseModel):
    """Request payload for cancellation."""

    model_config = ConfigDict(populate_by_name=True)

    reason: Optional[str] = Field(None, alias="reason")
    graceful: bool = Field(True, alias="graceful")


class ExecutionActionCapabilityModel(BaseModel):
    """State-aware Temporal action visibility returned to the dashboard."""

    model_config = ConfigDict(populate_by_name=True)

    can_set_title: bool = Field(False, alias="canSetTitle")
    can_update_inputs: bool = Field(False, alias="canUpdateInputs")
    can_rerun: bool = Field(False, alias="canRerun")
    can_approve: bool = Field(False, alias="canApprove")
    can_pause: bool = Field(False, alias="canPause")
    can_resume: bool = Field(False, alias="canResume")
    can_cancel: bool = Field(False, alias="canCancel")
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )


class ExecutionDebugFieldsModel(BaseModel):
    """Optional debug metadata gated by Temporal dashboard settings."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    temporal_run_id: str = Field(..., alias="temporalRunId")
    legacy_run_id: Optional[str] = Field(None, alias="legacyRunId")
    namespace: str = Field(..., alias="namespace")
    temporal_status: str = Field(..., alias="temporalStatus")
    raw_state: str = Field(..., alias="rawState")
    close_status: Optional[str] = Field(None, alias="closeStatus")
    waiting_reason: Optional[str] = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")


class ExecutionModel(BaseModel):
    """Materialized execution view returned by lifecycle APIs."""

    model_config = ConfigDict(populate_by_name=True)

    source: Literal["temporal"] = Field("temporal", alias="source")
    task_id: str = Field(..., alias="taskId")
    namespace: str = Field(..., alias="namespace")
    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    temporal_run_id: str = Field(..., alias="temporalRunId")
    legacy_run_id: Optional[str] = Field(None, alias="legacyRunId")
    workflow_type: str = Field(..., alias="workflowType")
    entry: Literal["run", "manifest"] = Field(..., alias="entry")
    owner_type: Literal["user", "system", "service"] = Field(..., alias="ownerType")
    owner_id: str = Field(..., alias="ownerId")
    title: str = Field(..., alias="title")
    summary: str = Field(..., alias="summary")
    status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "cancelled",
    ] = Field(..., alias="status")
    dashboard_status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "cancelled",
    ] = Field(..., alias="dashboardStatus")
    state: str = Field(..., alias="state")
    raw_state: str = Field(..., alias="rawState")
    temporal_status: Literal["running", "completed", "failed", "canceled"] = Field(
        ..., alias="temporalStatus"
    )
    close_status: Optional[str] = Field(None, alias="closeStatus")
    waiting_reason: Optional[str] = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")
    search_attributes: dict[str, Any] = Field(
        default_factory=dict, alias="searchAttributes"
    )
    memo: dict[str, Any] = Field(default_factory=dict, alias="memo")
    target_runtime: Optional[str] = Field(None, alias="targetRuntime")
    target_skill: Optional[str] = Field(None, alias="targetSkill")
    model: Optional[str] = Field(None, alias="model")
    effort: Optional[str] = Field(None, alias="effort")
    starting_branch: Optional[str] = Field(None, alias="startingBranch")
    target_branch: Optional[str] = Field(None, alias="targetBranch")
    publish_mode: Optional[str] = Field(None, alias="publishMode")
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    actions: ExecutionActionCapabilityModel = Field(
        default_factory=ExecutionActionCapabilityModel, alias="actions"
    )
    debug_fields: Optional[ExecutionDebugFieldsModel] = Field(None, alias="debugFields")
    redirect_path: Optional[str] = Field(None, alias="redirectPath")
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    summary_artifact_ref: Optional[str] = Field(None, alias="summaryArtifactRef")
    run_index_artifact_ref: Optional[str] = Field(None, alias="runIndexArtifactRef")
    checkpoint_artifact_ref: Optional[str] = Field(None, alias="checkpointArtifactRef")
    requested_by: Optional[RequestedByModel] = Field(None, alias="requestedBy")
    execution_policy: Optional[ManifestExecutionPolicyModel] = Field(
        None,
        alias="executionPolicy",
    )
    phase: Optional[str] = Field(None, alias="phase")
    paused: Optional[bool] = Field(None, alias="paused")
    counts: Optional[ManifestNodeCountsModel] = Field(None, alias="counts")
    artifacts_count: int = Field(0, alias="artifactsCount")
    scheduled_for: Optional[datetime] = Field(None, alias="scheduledFor")
    created_at: datetime = Field(..., alias="createdAt")
    integration: Optional[IntegrationStateModel] = Field(None, alias="integration")
    latest_run_view: bool = Field(True, alias="latestRunView")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    started_at: datetime = Field(..., alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    closed_at: datetime | None = Field(None, alias="closedAt")
    detail_href: str = Field(..., alias="detailHref")
    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    stale_state: bool = Field(False, alias="staleState")
    refreshed_at: datetime | None = Field(None, alias="refreshedAt")


class ExecutionRefreshEnvelope(BaseModel):
    """Compatibility metadata for patching one acted-on row and refetching lists."""

    model_config = ConfigDict(populate_by_name=True)

    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    patched_execution: bool = Field(..., alias="patchedExecution")
    list_stale: bool = Field(..., alias="listStale")
    refetch_suggested: bool = Field(..., alias="refetchSuggested")
    refreshed_at: datetime = Field(..., alias="refreshedAt")


class UpdateExecutionResponse(BaseModel):
    """Outcome from an update command."""

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool = Field(..., alias="accepted")
    applied: Literal["immediate", "next_safe_point", "continue_as_new"] = Field(
        ..., alias="applied"
    )
    message: str = Field(..., alias="message")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    execution: ExecutionModel | None = Field(None, alias="execution")
    refresh: ExecutionRefreshEnvelope | None = Field(None, alias="refresh")


class ScheduleCreatedResponse(BaseModel):
    """Response returned when a recurring schedule is created from the create endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled: Literal[True] = Field(True, alias="scheduled")
    definition_id: str = Field(..., alias="definitionId")
    name: str = Field(..., alias="name")
    cron: str = Field(..., alias="cron")
    timezone: str = Field(..., alias="timezone")
    next_run_at: datetime = Field(..., alias="nextRunAt")
    redirect_path: str = Field(..., alias="redirectPath")


class ExecutionListResponse(BaseModel):
    """Paginated list response for executions."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ExecutionModel] = Field(default_factory=list, alias="items")
    next_page_token: Optional[str] = Field(None, alias="nextPageToken")
    count: Optional[int] = Field(None, alias="count")
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
    ui_query_model: Literal["compatibility_adapter"] = Field(
        "compatibility_adapter", alias="uiQueryModel"
    )
    stale_state: bool = Field(False, alias="staleState")
    degraded_count: bool = Field(False, alias="degradedCount")
    refreshed_at: datetime | None = Field(None, alias="refreshedAt")
