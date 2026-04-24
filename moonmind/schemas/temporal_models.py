"""Schemas for Temporal execution lifecycle APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from moonmind.schemas.temporal_artifact_models import (
    ArtifactRefModel,
    CompactArtifactRefModel,
)
from moonmind.schemas.temporal_payload_policy import validate_compact_temporal_mapping

SUPPORTED_WORKFLOW_TYPES = (
    "MoonMind.Run",
    "MoonMind.ManifestIngest",
    "MoonMind.MergeAutomation",
)
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
    "Cancel",
    "Approve",
)
SUPPORTED_SIGNAL_NAMES = (
    "ExternalEvent",
    "Pause",
    "Resume",
    "Approve",
    "SkipDependencyWait",
    "SendMessage",
    "DependencyResolved",
    "BypassDependencies",
)
TASK_RUN_ID_MEMO_KEYS = ("taskRunId", "task_run_id")
TASK_RUN_ID_SEARCH_ATTR_KEYS = ("mm_task_run_id",)
TASK_RUN_ID_PARAM_KEYS = ("taskRunId", "task_run_id")

def normalize_dependency_ids(raw_value: Any) -> list[str]:
    """Normalize a list of dependency IDs, stripping whitespace and removing duplicates/non-strings."""
    if not isinstance(raw_value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if not isinstance(item, str):
            continue
        candidate = item.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


class PullRequestRefModel(BaseModel):
    """Compact pull request identity carried through merge automation history."""

    model_config = ConfigDict(populate_by_name=True)

    repo: str = Field(..., alias="repo")
    number: int = Field(..., alias="number")
    url: str = Field(..., alias="url")
    head_sha: str = Field(..., alias="headSha")
    head_branch: str | None = Field(None, alias="headBranch")
    base_branch: str | None = Field(None, alias="baseBranch")

    @field_validator("repo", "url", "head_sha")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("number")
    @classmethod
    def _positive_number(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("number must be positive")
        return value


MergeAutomationPolicySetting = Literal["required", "optional", "disabled"]
MergeMethod = Literal["merge", "squash", "rebase"]
PostMergeJiraStrategy = Literal["done_category"]


class MergeAutomationGitHubGateModel(BaseModel):
    """GitHub readiness policy for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    checks: MergeAutomationPolicySetting = Field("required", alias="checks")
    automated_review: MergeAutomationPolicySetting = Field(
        "required", alias="automatedReview"
    )


class MergeAutomationJiraGateModel(BaseModel):
    """Jira readiness policy for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(False, alias="enabled")
    issue_key: str | None = Field(None, alias="issueKey")
    allowed_statuses: list[str] = Field(default_factory=list, alias="allowedStatuses")
    status: MergeAutomationPolicySetting = Field("optional", alias="status")


class MergeAutomationGateModel(BaseModel):
    """Readiness policy for one merge automation workflow."""

    model_config = ConfigDict(populate_by_name=True)

    github: MergeAutomationGitHubGateModel = Field(
        default_factory=MergeAutomationGitHubGateModel,
        alias="github",
    )
    jira: MergeAutomationJiraGateModel = Field(
        default_factory=MergeAutomationJiraGateModel,
        alias="jira",
    )


class MergeAutomationResolverModel(BaseModel):
    """Resolver configuration for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    skill: str = Field("pr-resolver", alias="skill")
    merge_method: MergeMethod = Field("squash", alias="mergeMethod")


class MergeAutomationTimeoutsModel(BaseModel):
    """Bounded wait settings for merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    fallback_poll_seconds: int = Field(120, alias="fallbackPollSeconds")
    expire_after_seconds: int | None = Field(None, alias="expireAfterSeconds")

    @field_validator("fallback_poll_seconds", mode="before")
    @classmethod
    def _normalize_fallback_poll_seconds(cls, value: Any) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return 120
        if candidate <= 0:
            return 120
        return min(candidate, 3600)

    @field_validator("expire_after_seconds", mode="before")
    @classmethod
    def _normalize_expire_after_seconds(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return None
        if candidate <= 0:
            return None
        return min(candidate, 2_592_000)


class MergeAutomationPostMergeJiraModel(BaseModel):
    """Runtime policy for Jira completion after verified merge success."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = Field(False, alias="enabled")
    issue_key: str | None = Field(None, alias="issueKey")
    transition_id: str | None = Field(None, alias="transitionId")
    transition_name: str | None = Field(None, alias="transitionName")
    strategy: PostMergeJiraStrategy = Field("done_category", alias="strategy")
    required: bool = Field(True, alias="required")
    fields: dict[str, Any] = Field(default_factory=dict, alias="fields")

    @field_validator("issue_key", "transition_id", "transition_name", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        candidate = str(value).strip()
        return candidate or None

    @field_validator("fields", mode="before")
    @classmethod
    def _validate_fields(cls, value: Any) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="postMergeJira.fields")


class MergeAutomationConfigModel(BaseModel):
    """Full merge automation configuration carried by workflow input."""

    model_config = ConfigDict(populate_by_name=True)

    gate: MergeAutomationGateModel = Field(
        default_factory=MergeAutomationGateModel,
        alias="gate",
    )
    resolver: MergeAutomationResolverModel = Field(
        default_factory=MergeAutomationResolverModel,
        alias="resolver",
    )
    timeouts: MergeAutomationTimeoutsModel = Field(
        default_factory=MergeAutomationTimeoutsModel,
        alias="timeouts",
    )
    post_merge_jira: MergeAutomationPostMergeJiraModel = Field(
        default_factory=MergeAutomationPostMergeJiraModel,
        alias="postMergeJira",
    )


ReadinessBlockerKind = Literal[
    "checks_running",
    "checks_failed",
    "automated_review_pending",
    "jira_status_pending",
    "pull_request_closed",
    "stale_revision",
    "policy_denied",
    "external_state_unavailable",
    "manual_review",
    "failed",
    "resolver_disposition_invalid",
]


class ReadinessBlockerModel(BaseModel):
    """Bounded operator-visible reason a merge gate is not open."""

    model_config = ConfigDict(populate_by_name=True)

    kind: ReadinessBlockerKind = Field(..., alias="kind")
    summary: str = Field(..., alias="summary")
    retryable: bool = Field(True, alias="retryable")
    source: str | None = Field(None, alias="source")

    @field_validator("summary")
    @classmethod
    def _summary_required(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("summary must be a non-empty string")
        return candidate[:500]


class ReadinessEvidenceModel(BaseModel):
    """Compact result of evaluating external PR readiness."""

    model_config = ConfigDict(populate_by_name=True)

    head_sha: str = Field(..., alias="headSha")
    ready: bool = Field(False, alias="ready")
    pull_request_open: bool | None = Field(None, alias="pullRequestOpen")
    pull_request_merged: bool | None = Field(None, alias="pullRequestMerged")
    checks_complete: bool | None = Field(None, alias="checksComplete")
    checks_passing: bool | None = Field(None, alias="checksPassing")
    automated_review_complete: bool | None = Field(
        None, alias="automatedReviewComplete"
    )
    jira_status_allowed: bool | None = Field(None, alias="jiraStatusAllowed")
    policy_allowed: bool | None = Field(None, alias="policyAllowed")
    blockers: list[ReadinessBlockerModel] = Field(default_factory=list, alias="blockers")

    @field_validator("head_sha")
    @classmethod
    def _head_sha_required(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("headSha must be a non-empty string")
        return candidate

    @model_validator(mode="after")
    def _ready_requires_no_blockers(self) -> "ReadinessEvidenceModel":
        if self.ready and self.blockers:
            raise ValueError("ready evidence cannot include blockers")
        return self


class ResolverRunRefModel(BaseModel):
    """Reference to the resolver follow-up run launched by merge automation."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    run_id: str | None = Field(None, alias="runId")
    created: bool = Field(True, alias="created")


class MergeAutomationStartInput(BaseModel):
    """Start input for ``MoonMind.MergeAutomation``."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: Literal["MoonMind.MergeAutomation"] = Field(..., alias="workflowType")
    parent_workflow_id: str = Field(..., alias="parentWorkflowId")
    parent_run_id: str | None = Field(None, alias="parentRunId")
    principal: str | None = Field(None, alias="principal")
    publish_context_ref: str = Field(..., alias="publishContextRef")
    pull_request: PullRequestRefModel = Field(..., alias="pullRequest")
    jira_issue_key: str | None = Field(None, alias="jiraIssueKey")
    config: MergeAutomationConfigModel = Field(
        default_factory=MergeAutomationConfigModel,
        alias="mergeAutomationConfig",
    )
    resolver_template: dict[str, Any] = Field(
        default_factory=dict,
        alias="resolverTemplate",
    )
    blockers: list[ReadinessBlockerModel] = Field(default_factory=list, alias="blockers")
    cycle_count: int = Field(0, alias="cycleCount")
    resolver_history: list[ResolverRunRefModel] = Field(
        default_factory=list,
        alias="resolverHistory",
    )
    expire_at: str | None = Field(None, alias="expireAt")
    idempotency_key: str | None = Field(None, alias="idempotencyKey")

    @field_validator("parent_workflow_id", "publish_context_ref")
    @classmethod
    def _required_text(cls, value: str) -> str:
        candidate = str(value or "").strip()
        if not candidate:
            raise ValueError("field must be a non-empty string")
        return candidate

    @field_validator("cycle_count")
    @classmethod
    def _non_negative_cycle_count(cls, value: int) -> int:
        if value < 0:
            raise ValueError("cycleCount must be non-negative")
        return value


class DependencyResolvedSignalPayload(BaseModel):
    """Signal payload emitted when a prerequisite execution reaches a terminal state."""

    model_config = ConfigDict(populate_by_name=True)

    prerequisite_workflow_id: str = Field(..., alias="prerequisiteWorkflowId")
    terminal_state: str = Field(..., alias="terminalState")
    close_status: str | None = Field(None, alias="closeStatus")
    resolved_at: datetime = Field(..., alias="resolvedAt")
    failure_category: str | None = Field(None, alias="failureCategory")
    message: str | None = Field(None, alias="message")


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

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )


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

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )


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

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )


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

    @field_validator("provider_summary", mode="after")
    @classmethod
    def _validate_provider_summary(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            value,
            field_name="providerSummary",
        )


class CancelExecutionRequest(BaseModel):
    """Request payload for cancellation."""

    model_config = ConfigDict(populate_by_name=True)

    action: Literal["cancel", "reject"] = Field("cancel", alias="action")
    reason: Optional[str] = Field(None, alias="reason")
    graceful: bool = Field(True, alias="graceful")


class RescheduleExecutionRequest(BaseModel):
    """Request payload for rescheduling a deferred execution."""

    model_config = ConfigDict(populate_by_name=True)

    scheduled_for: datetime = Field(..., alias="scheduledFor")


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
    can_reject: bool = Field(False, alias="canReject")
    can_send_message: bool = Field(False, alias="canSendMessage")
    can_bypass_dependencies: bool = Field(False, alias="canBypassDependencies")
    can_skip_dependency_wait: bool = Field(False, alias="canSkipDependencyWait")
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )


class ExecutionInterventionAuditEntryModel(BaseModel):
    """One explicit operator intervention record shown outside stdout/stderr logs."""

    model_config = ConfigDict(populate_by_name=True)

    action: str = Field(..., alias="action")
    transport: str = Field(..., alias="transport")
    summary: str = Field(..., alias="summary")
    detail: Optional[str] = Field(None, alias="detail")
    created_at: datetime = Field(..., alias="createdAt")


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


class ExecutionDependencyOutcomeModel(BaseModel):
    """One resolved prerequisite outcome surfaced on execution detail."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    terminal_state: Optional[str] = Field(None, alias="terminalState")
    close_status: Optional[str] = Field(None, alias="closeStatus")
    resolved_at: Optional[datetime] = Field(None, alias="resolvedAt")
    failure_category: Optional[str] = Field(None, alias="failureCategory")
    message: Optional[str] = Field(None, alias="message")


class ExecutionDependencySummaryModel(BaseModel):
    """Compact linked execution metadata for prerequisites or dependents."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    title: Optional[str] = Field(None, alias="title")
    summary: Optional[str] = Field(None, alias="summary")
    state: Optional[str] = Field(None, alias="state")
    close_status: Optional[str] = Field(None, alias="closeStatus")
    workflow_type: Optional[str] = Field(None, alias="workflowType")


class ExecutionSkillVersionSummaryModel(BaseModel):
    """Compact operator-safe summary of one selected skill version."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    version: Optional[str] = Field(None, alias="version")
    source_kind: Optional[str] = Field(None, alias="sourceKind")
    source_path: Optional[str] = Field(None, alias="sourcePath")
    content_ref: Optional[str] = Field(None, alias="contentRef")
    content_digest: Optional[str] = Field(None, alias="contentDigest")


class ExecutionSkillProvenanceModel(BaseModel):
    """Compact source provenance for one selected skill."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    source_kind: Optional[str] = Field(None, alias="sourceKind")
    source_path: Optional[str] = Field(None, alias="sourcePath")


class ExecutionProjectionDiagnosticModel(BaseModel):
    """Sanitized projection diagnostic for operator-visible skill failures."""

    model_config = ConfigDict(populate_by_name=True)

    path: Optional[str] = Field(None, alias="path")
    object_kind: Optional[str] = Field(None, alias="objectKind")
    attempted_action: Optional[str] = Field(None, alias="attemptedAction")
    remediation: Optional[str] = Field(None, alias="remediation")
    cause: Optional[str] = Field(None, alias="cause")


class ExecutionSkillLifecycleIntentModel(BaseModel):
    """How skill intent or snapshot reuse is preserved across lifecycle paths."""

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(..., alias="source")
    selectors: list[str] = Field(default_factory=list, alias="selectors")
    resolved_skillset_ref: Optional[str] = Field(None, alias="resolvedSkillsetRef")
    resolution_mode: str = Field(..., alias="resolutionMode")
    explanation: str = Field(..., alias="explanation")


class ExecutionSkillRuntimeModel(BaseModel):
    """Compact skill runtime evidence exposed by execution detail APIs."""

    model_config = ConfigDict(populate_by_name=True)

    resolved_skillset_ref: Optional[str] = Field(None, alias="resolvedSkillsetRef")
    selected_skills: list[str] = Field(default_factory=list, alias="selectedSkills")
    selected_versions: list[ExecutionSkillVersionSummaryModel] = Field(
        default_factory=list, alias="selectedVersions"
    )
    source_provenance: list[ExecutionSkillProvenanceModel] = Field(
        default_factory=list, alias="sourceProvenance"
    )
    materialization_mode: Optional[str] = Field(None, alias="materializationMode")
    visible_path: Optional[str] = Field(None, alias="visiblePath")
    backing_path: Optional[str] = Field(None, alias="backingPath")
    read_only: Optional[bool] = Field(None, alias="readOnly")
    manifest_ref: Optional[str] = Field(None, alias="manifestRef")
    prompt_index_ref: Optional[str] = Field(None, alias="promptIndexRef")
    activation_summary_ref: Optional[str] = Field(None, alias="activationSummaryRef")
    diagnostics: Optional[ExecutionProjectionDiagnosticModel] = Field(
        None, alias="diagnostics"
    )
    lifecycle_intent: Optional[ExecutionSkillLifecycleIntentModel] = Field(
        None, alias="lifecycleIntent"
    )


class ExecutionProgressModel(BaseModel):
    """Bounded latest-run progress summary derived from workflow-owned step state."""

    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(0, alias="total", ge=0)
    pending: int = Field(0, alias="pending", ge=0)
    ready: int = Field(0, alias="ready", ge=0)
    running: int = Field(0, alias="running", ge=0)
    awaiting_external: int = Field(0, alias="awaitingExternal", ge=0)
    reviewing: int = Field(0, alias="reviewing", ge=0)
    succeeded: int = Field(0, alias="succeeded", ge=0)
    failed: int = Field(0, alias="failed", ge=0)
    skipped: int = Field(0, alias="skipped", ge=0)
    canceled: int = Field(0, alias="canceled", ge=0)
    current_step_title: str | None = Field(None, alias="currentStepTitle")
    updated_at: datetime = Field(..., alias="updatedAt")


class ExecutionMergeAutomationBlockerModel(BaseModel):
    """Operator-visible merge automation blocker."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    kind: str | None = Field(None, alias="kind")
    summary: str | None = Field(None, alias="summary")
    retryable: bool | None = Field(None, alias="retryable")
    source: str | None = Field(None, alias="source")


class ExecutionMergeAutomationArtifactRefsModel(BaseModel):
    """Artifact refs produced by merge automation."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    summary: str | None = Field(None, alias="summary")
    gate_snapshots: list[str] = Field(default_factory=list, alias="gateSnapshots")
    resolver_attempts: list[str] = Field(default_factory=list, alias="resolverAttempts")


class ExecutionMergeAutomationResolverChildModel(BaseModel):
    """Resolver child workflow reference plus observability binding when known."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId")
    task_run_id: str | None = Field(None, alias="taskRunId")
    status: str | None = Field(None, alias="status")
    detail_href: str | None = Field(None, alias="detailHref")


class ExecutionMergeAutomationModel(BaseModel):
    """Live or terminal merge automation visibility for an execution."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    enabled: bool = Field(True, alias="enabled")
    workflow_id: str | None = Field(None, alias="workflowId")
    child_workflow_id: str | None = Field(None, alias="childWorkflowId")
    status: str | None = Field(None, alias="status")
    pr_number: int | str | None = Field(None, alias="prNumber")
    pr_url: str | None = Field(None, alias="prUrl")
    latest_head_sha: str | None = Field(None, alias="latestHeadSha")
    cycles: int | str | None = Field(None, alias="cycles")
    blockers: list[ExecutionMergeAutomationBlockerModel] = Field(
        default_factory=list,
        alias="blockers",
    )
    artifact_refs: ExecutionMergeAutomationArtifactRefsModel | None = Field(
        None,
        alias="artifactRefs",
    )
    resolver_child_workflow_ids: list[str] = Field(
        default_factory=list,
        alias="resolverChildWorkflowIds",
    )
    resolver_children: list[ExecutionMergeAutomationResolverChildModel] = Field(
        default_factory=list,
        alias="resolverChildren",
    )

    @model_validator(mode="after")
    def _mirror_child_workflow_id(self) -> "ExecutionMergeAutomationModel":
        if not self.workflow_id and self.child_workflow_id:
            self.workflow_id = self.child_workflow_id
        if not self.child_workflow_id and self.workflow_id:
            self.child_workflow_id = self.workflow_id
        return self


class TaskInputSnapshotDescriptorModel(BaseModel):
    """Compact pointer to the authoritative original task input snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    available: bool = Field(False, alias="available")
    artifact_ref: str | None = Field(None, alias="artifactRef")
    snapshot_version: int | None = Field(None, alias="snapshotVersion")
    source_kind: Literal["create", "edit", "rerun", "unknown"] = Field(
        "unknown", alias="sourceKind"
    )
    reconstruction_mode: Literal[
        "authoritative",
        "degraded_read_only",
        "unavailable",
    ] = Field("unavailable", alias="reconstructionMode")
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )
    fallback_evidence_refs: list[str] = Field(
        default_factory=list, alias="fallbackEvidenceRefs"
    )

    @model_validator(mode="after")
    def _authoritative_requires_ref(self) -> "TaskInputSnapshotDescriptorModel":
        if self.reconstruction_mode == "authoritative" and not self.artifact_ref:
            raise ValueError("authoritative reconstruction requires artifactRef")
        return self


class StepLedgerCheckModel(BaseModel):
    """Structured step-level review or check result."""

    model_config = ConfigDict(populate_by_name=True)

    kind: str = Field(..., alias="kind", min_length=1)
    status: str = Field(..., alias="status", min_length=1)
    summary: str | None = Field(None, alias="summary")
    retry_count: int = Field(0, alias="retryCount", ge=0)
    artifact_ref: str | None = Field(None, alias="artifactRef")


class StepLedgerRefsModel(BaseModel):
    """Stable ref slots for child workflow and task-run linkage."""

    model_config = ConfigDict(populate_by_name=True)

    child_workflow_id: str | None = Field(None, alias="childWorkflowId")
    child_run_id: str | None = Field(None, alias="childRunId")
    task_run_id: str | None = Field(None, alias="taskRunId")


class StepLedgerArtifactsModel(BaseModel):
    """Stable semantic artifact slots for step evidence."""

    model_config = ConfigDict(populate_by_name=True)

    output_summary: str | None = Field(None, alias="outputSummary")
    output_primary: str | None = Field(None, alias="outputPrimary")
    runtime_stdout: str | None = Field(None, alias="runtimeStdout")
    runtime_stderr: str | None = Field(None, alias="runtimeStderr")
    runtime_merged_logs: str | None = Field(None, alias="runtimeMergedLogs")
    runtime_diagnostics: str | None = Field(None, alias="runtimeDiagnostics")
    provider_snapshot: str | None = Field(None, alias="providerSnapshot")


class StepLedgerWorkloadModel(BaseModel):
    """Bounded Docker-backed workload metadata linked to a producing step."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    task_run_id: str | None = Field(None, alias="taskRunId")
    step_id: str | None = Field(None, alias="stepId")
    attempt: int | None = Field(None, alias="attempt", ge=1)
    tool_name: str | None = Field(None, alias="toolName")
    profile_id: str | None = Field(None, alias="profileId")
    image_ref: str | None = Field(None, alias="imageRef")
    status: str | None = Field(None, alias="status")
    exit_code: int | None = Field(None, alias="exitCode")
    duration_seconds: float | None = Field(None, alias="durationSeconds", ge=0)
    timeout_reason: str | None = Field(None, alias="timeoutReason")
    cancel_reason: str | None = Field(None, alias="cancelReason")
    session_context: dict[str, Any] | None = Field(None, alias="sessionContext")
    artifact_publication: dict[str, Any] | None = Field(
        None,
        alias="artifactPublication",
    )


class StepLedgerRowModel(BaseModel):
    """Current/latest attempt state for one logical step in the active run."""

    model_config = ConfigDict(populate_by_name=True)

    logical_step_id: str = Field(..., alias="logicalStepId", min_length=1)
    order: int = Field(..., alias="order", ge=1)
    title: str = Field(..., alias="title", min_length=1)
    tool: dict[str, Any] = Field(default_factory=dict, alias="tool")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    status: Literal[
        "pending",
        "ready",
        "running",
        "awaiting_external",
        "reviewing",
        "succeeded",
        "failed",
        "skipped",
        "canceled",
    ] = Field(..., alias="status")
    waiting_reason: str | None = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")
    attempt: int = Field(0, alias="attempt", ge=0)
    started_at: datetime | None = Field(None, alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    summary: str | None = Field(None, alias="summary")
    checks: list[StepLedgerCheckModel] = Field(default_factory=list, alias="checks")
    refs: StepLedgerRefsModel = Field(default_factory=StepLedgerRefsModel, alias="refs")
    artifacts: StepLedgerArtifactsModel = Field(
        default_factory=StepLedgerArtifactsModel, alias="artifacts"
    )
    workload: StepLedgerWorkloadModel | None = Field(None, alias="workload")
    last_error: str | None = Field(None, alias="lastError")


class StepLedgerSnapshotModel(BaseModel):
    """Latest-run step-ledger query payload."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_id: str = Field(..., alias="workflowId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    run_scope: Literal["latest"] = Field("latest", alias="runScope")
    steps: list[StepLedgerRowModel] = Field(default_factory=list, alias="steps")


class ExecutionReportProjectionModel(BaseModel):
    """Bounded report summary surfaced on execution detail responses."""

    model_config = ConfigDict(populate_by_name=True)

    has_report: bool = Field(..., alias="hasReport")
    latest_report_ref: CompactArtifactRefModel | None = Field(
        None, alias="latestReportRef"
    )
    latest_report_summary_ref: CompactArtifactRefModel | None = Field(
        None, alias="latestReportSummaryRef"
    )
    report_type: str | None = Field(None, alias="reportType")
    report_status: str | None = Field(None, alias="reportStatus")
    finding_counts: dict[str, int] | None = Field(None, alias="findingCounts")
    severity_counts: dict[str, int] | None = Field(None, alias="severityCounts")

    @model_serializer(mode="wrap")
    def _serialize_without_nulls(self, handler):
        payload = handler(self)
        return {key: value for key, value in payload.items() if value is not None}


class ExecutionModel(BaseModel):
    """Materialized execution view returned by lifecycle APIs."""

    model_config = ConfigDict(populate_by_name=True)

    source: Literal["temporal"] = Field("temporal", alias="source")
    task_id: str = Field(..., alias="taskId")
    task_run_id: Optional[str] = Field(None, alias="taskRunId")
    progress: ExecutionProgressModel | None = Field(None, alias="progress")
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
    task_instructions: Optional[str] = Field(None, alias="taskInstructions")
    status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    ] = Field(..., alias="status")
    dashboard_status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "waiting",
        "completed",
        "failed",
        "canceled",
    ] = Field(..., alias="dashboardStatus")
    state: str = Field(..., alias="state")
    raw_state: str = Field(..., alias="rawState")
    temporal_status: Literal["running", "completed", "failed", "canceled"] = Field(
        ..., alias="temporalStatus"
    )
    close_status: Optional[str] = Field(None, alias="closeStatus")
    waiting_reason: Optional[str] = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")
    intervention_audit: list[ExecutionInterventionAuditEntryModel] = Field(
        default_factory=list, alias="interventionAudit"
    )
    search_attributes: dict[str, Any] = Field(
        default_factory=dict, alias="searchAttributes"
    )
    memo: dict[str, Any] = Field(default_factory=dict, alias="memo")
    input_parameters: dict[str, Any] = Field(
        default_factory=dict, alias="inputParameters"
    )
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    task_input_snapshot: TaskInputSnapshotDescriptorModel = Field(
        default_factory=TaskInputSnapshotDescriptorModel,
        alias="taskInputSnapshot",
    )
    target_runtime: Optional[str] = Field(None, alias="targetRuntime")
    target_skill: Optional[str] = Field(None, alias="targetSkill")
    model: Optional[str] = Field(None, alias="model")
    requested_model: Optional[str] = Field(None, alias="requestedModel")
    resolved_model: Optional[str] = Field(None, alias="resolvedModel")
    model_source: Optional[str] = Field(None, alias="modelSource")
    profile_id: Optional[str] = Field(None, alias="profileId")
    provider_id: Optional[str] = Field(None, alias="providerId")
    provider_label: Optional[str] = Field(None, alias="providerLabel")
    effort: Optional[str] = Field(None, alias="effort")
    starting_branch: Optional[str] = Field(None, alias="startingBranch")
    target_branch: Optional[str] = Field(None, alias="targetBranch")
    repository: Optional[str] = Field(None, alias="repository")
    pr_url: Optional[str] = Field(
        None,
        alias="prUrl",
        description="URL of the pull request associated with this execution.",
    )
    publish_mode: Optional[str] = Field(None, alias="publishMode")
    merge_automation_selected: bool = Field(False, alias="mergeAutomationSelected")
    merge_automation: ExecutionMergeAutomationModel | None = Field(
        None,
        alias="mergeAutomation",
    )
    resolved_skillset_ref: Optional[str] = Field(None, alias="resolvedSkillsetRef")
    task_skills: Optional[list[str]] = Field(None, alias="taskSkills")
    skill_runtime: Optional[ExecutionSkillRuntimeModel] = Field(
        None, alias="skillRuntime"
    )
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    report_projection: ExecutionReportProjectionModel | None = Field(
        None, alias="reportProjection"
    )
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
    steps_href: Optional[str] = Field(None, alias="stepsHref")
    integration: Optional[IntegrationStateModel] = Field(None, alias="integration")
    latest_run_view: bool = Field(True, alias="latestRunView")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    depends_on: list[str] = Field(default_factory=list, alias="dependsOn")
    has_dependencies: bool = Field(False, alias="hasDependencies")
    dependency_wait_occurred: bool = Field(False, alias="dependencyWaitOccurred")
    dependency_wait_duration_ms: Optional[int] = Field(
        None, alias="dependencyWaitDurationMs"
    )
    dependency_resolution: Optional[str] = Field(None, alias="dependencyResolution")
    failed_dependency_id: Optional[str] = Field(None, alias="failedDependencyId")
    blocked_on_dependencies: bool = Field(False, alias="blockedOnDependencies")
    dependency_outcomes: list[ExecutionDependencyOutcomeModel] = Field(
        default_factory=list, alias="dependencyOutcomes"
    )
    prerequisites: list[ExecutionDependencySummaryModel] = Field(
        default_factory=list, alias="prerequisites"
    )
    dependents: list[ExecutionDependencySummaryModel] = Field(
        default_factory=list, alias="dependents"
    )
    started_at: datetime | None = Field(None, alias="startedAt")
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
