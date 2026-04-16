"""Canonical contracts for the Codex managed session plane."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from moonmind.schemas._validation import NonBlankStr, require_non_blank
from moonmind.schemas.temporal_payload_policy import (
    MAX_TEMPORAL_METADATA_STRING_CHARS,
    validate_compact_temporal_mapping,
)


ManagedSessionControlAction = Literal[
    "start_session",
    "resume_session",
    "send_turn",
    "steer_turn",
    "interrupt_turn",
    "clear_session",
    "cancel_session",
    "terminate_session",
]

ManagedSessionControlMode = Literal["remote_container"]
ManagedSessionProtocol = Literal["codex_app_server"]
ManagedSessionContainerBackend = Literal["docker"]
ManagedSessionHandleStatus = Literal[
    "launching",
    "ready",
    "busy",
    "clearing",
    "interrupted",
    "terminating",
    "terminated",
    "failed",
]
ManagedSessionTurnStatus = Literal[
    "accepted",
    "running",
    "completed",
    "interrupted",
    "failed",
]
ManagedSessionRecordStatus = Literal[
    "launching",
    "ready",
    "busy",
    "terminating",
    "terminated",
    "degraded",
    "failed",
]
ManagedSessionRequestTrackingStatus = Literal[
    "accepted",
    "completed",
    "failed",
    "superseded",
]

ClaudeRuntimeFamily = Literal["claude_code"]
ClaudeExecutionOwner = Literal["local_process", "anthropic_cloud_vm", "sdk_host"]
ClaudeSurfaceKind = Literal[
    "terminal",
    "vscode",
    "jetbrains",
    "desktop",
    "web",
    "mobile",
    "scheduler",
    "channel",
    "sdk",
]
ClaudeProjectionMode = Literal["primary", "remote_projection", "handoff"]
ClaudeSurfaceProjectionMode = Literal["primary", "remote_projection"]
ClaudeSessionState = Literal[
    "creating",
    "starting",
    "active",
    "waiting",
    "compacting",
    "rewinding",
    "archiving",
    "ended",
    "failed",
]
ClaudeTurnInputOrigin = Literal["human", "schedule", "channel", "sdk", "team_message"]
ClaudeTurnState = Literal[
    "submitted",
    "gathering_context",
    "pending_decision",
    "executing",
    "verifying",
    "interrupted",
    "completed",
    "failed",
]
ClaudeWorkItemKind = Literal[
    "context_read",
    "context_injection",
    "tool_call",
    "hook_call",
    "approval_request",
    "checkpoint",
    "compaction",
    "rewind",
    "subagent",
    "team_message",
    "summary",
    "telemetry_flush",
]
ClaudeWorkItemStatus = Literal[
    "queued",
    "in_progress",
    "completed",
    "failed",
    "declined",
    "canceled",
]
ClaudeSurfaceConnectionState = Literal[
    "connected",
    "disconnected",
    "reconnecting",
    "detached",
]
ClaudeSessionCreatedBy = Literal["user", "schedule", "channel", "sdk", "team_lead"]
ClaudeProviderMode = Literal[
    "anthropic_api",
    "bedrock",
    "vertex",
    "foundry",
    "custom_gateway",
]
ClaudeManagedSourceKind = Literal["none", "server_managed", "endpoint_managed"]
ClaudePolicySourceKind = Literal[
    "server_managed",
    "endpoint_managed",
    "local_project",
    "shared_project",
    "user",
    "cli",
]
ClaudePolicyFetchState = Literal[
    "not_applicable",
    "cache_hit",
    "fetched",
    "fetch_failed",
    "fail_closed",
]
ClaudePolicyTrustLevel = Literal[
    "endpoint_enforced",
    "server_managed_best_effort",
    "unmanaged",
]
ClaudePermissionMode = Literal[
    "default",
    "acceptEdits",
    "plan",
    "auto",
    "dontAsk",
    "bypassPermissions",
]
ClaudePolicyHandshakeState = Literal[
    "ready",
    "security_dialog_required",
    "security_dialog_accepted",
    "security_dialog_rejected",
    "fail_closed",
    "blocked",
]
ClaudePolicyEventType = Literal[
    "policy.fetch.started",
    "policy.fetch.succeeded",
    "policy.fetch.failed",
    "policy.dialog.required",
    "policy.dialog.accepted",
    "policy.dialog.rejected",
    "policy.compiled",
    "policy.version.changed",
]

CODEX_MANAGED_SESSION_CONTROL_ACTIONS: tuple[ManagedSessionControlAction, ...] = (
    "start_session",
    "resume_session",
    "send_turn",
    "steer_turn",
    "interrupt_turn",
    "clear_session",
    "cancel_session",
    "terminate_session",
)

CodexManagedSessionWorkflowStatus = Literal[
    "initializing",
    "active",
    "clearing",
    "terminating",
    "terminated",
]


def canonical_codex_managed_runtime_id(runtime_id: str) -> str | None:
    """Return the canonical managed-session runtime ID for Codex."""

    normalized = str(runtime_id or "").strip().lower().replace("-", "_")
    if normalized in {"codex", "codex_cli"}:
        return "codex_cli"
    return None


_ASSISTANT_TEXT_METADATA_KEYS = frozenset({"assistantText", "lastAssistantText"})
_ASSISTANT_TEXT_METADATA_MAX_BYTES = 8 * 1024


def _truncate_utf8_text(value: str, *, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _truncate_json_text(value: str, *, max_bytes: int) -> str:
    if len(json.dumps(value, allow_nan=False).encode("utf-8")) <= max_bytes:
        return value
    low = 0
    high = len(value)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = value[:midpoint]
        encoded_size = len(json.dumps(candidate, allow_nan=False).encode("utf-8"))
        if encoded_size <= max_bytes:
            low = midpoint
        else:
            high = midpoint - 1
    return value[:low]


def _compact_managed_session_metadata(
    value: dict[str, Any],
) -> dict[str, Any]:
    """Clamp provider assistant text snippets before Temporal payload validation."""

    normalized = dict(value)
    for key in _ASSISTANT_TEXT_METADATA_KEYS:
        raw_text = normalized.get(key)
        if not isinstance(raw_text, str):
            continue
        original_chars = len(raw_text)
        compact_text = raw_text[:MAX_TEMPORAL_METADATA_STRING_CHARS]
        compact_text = _truncate_utf8_text(
            compact_text,
            max_bytes=_ASSISTANT_TEXT_METADATA_MAX_BYTES,
        )
        compact_text = _truncate_json_text(
            compact_text,
            max_bytes=_ASSISTANT_TEXT_METADATA_MAX_BYTES,
        )
        if compact_text != raw_text:
            normalized[key] = compact_text
            normalized[f"{key}Truncated"] = True
            normalized[f"{key}OriginalChars"] = original_chars
    return normalized


class CodexManagedSessionPlaneContract(BaseModel):
    """Frozen Phase 1 MVP contract for the Codex managed session plane."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime_family: Literal["codex"] = "codex"
    protocol: ManagedSessionProtocol = "codex_app_server"
    container_backend: ManagedSessionContainerBackend = "docker"
    session_scope: Literal["task"] = "task"
    session_container_policy: Literal["one_container_per_task"] = (
        "one_container_per_task"
    )
    cross_task_reuse: Literal[False] = False
    log_authority: Literal["artifact_first"] = "artifact_first"
    continuity_authority: Literal["artifact_first"] = "artifact_first"
    durable_state_rule: Literal[
        "artifacts_and_bounded_workflow_metadata_are_authoritative"
    ] = "artifacts_and_bounded_workflow_metadata_are_authoritative"
    clear_behavior: Literal["new_thread_same_container_new_epoch"] = (
        "new_thread_same_container_new_epoch"
    )
    control_actions: tuple[ManagedSessionControlAction, ...] = (
        CODEX_MANAGED_SESSION_CONTROL_ACTIONS
    )

    @model_validator(mode="after")
    def _freeze_phase1_values(self) -> "CodexManagedSessionPlaneContract":
        if self.control_actions != CODEX_MANAGED_SESSION_CONTROL_ACTIONS:
            raise ValueError(
                "control_actions must match the canonical Phase 1 action set"
            )
        return self


class CodexManagedSessionState(BaseModel):
    """Identity and continuity state for one task-scoped Codex session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    container_id: NonBlankStr = Field(..., alias="containerId")
    thread_id: NonBlankStr = Field(..., alias="threadId")
    active_turn_id: NonBlankStr | None = Field(None, alias="activeTurnId")

    def clear_session(self, *, new_thread_id: str) -> "CodexManagedSessionState":
        """Advance to a new session epoch inside the existing container."""

        normalized_thread_id = require_non_blank(
            new_thread_id, field_name="threadId"
        )
        if normalized_thread_id == self.thread_id:
            raise ValueError("clear_session must create a new threadId")
        return self.model_copy(
            update={
                "session_epoch": self.session_epoch + 1,
                "thread_id": normalized_thread_id,
                "active_turn_id": None,
            }
        )


class _CodexManagedSessionRemoteContract(BaseModel):
    """Base model that freezes the remote-container managed-session boundary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    runtime_family: Literal["codex"] = Field("codex", alias="runtimeFamily")
    protocol: ManagedSessionProtocol = Field(
        "codex_app_server", alias="protocol"
    )
    container_backend: ManagedSessionContainerBackend = Field(
        "docker", alias="containerBackend"
    )
    control_mode: ManagedSessionControlMode = Field(
        "remote_container", alias="controlMode"
    )

    @field_validator("metadata", mode="after", check_fields=False)
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(
            _compact_managed_session_metadata(value),
            field_name="metadata",
        )


class CodexManagedSessionLocator(_CodexManagedSessionRemoteContract):
    """Canonical bounded identity for addressing one managed session remotely."""

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    container_id: NonBlankStr = Field(..., alias="containerId")
    thread_id: NonBlankStr = Field(..., alias="threadId")


class LaunchCodexManagedSessionRequest(_CodexManagedSessionRemoteContract):
    """Launch contract for a task-scoped remote Codex session container."""

    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    workflow_id: NonBlankStr | None = Field(None, alias="workflowId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(1, alias="sessionEpoch", ge=1)
    thread_id: NonBlankStr = Field(..., alias="threadId")
    workspace_path: NonBlankStr = Field(..., alias="workspacePath")
    session_workspace_path: NonBlankStr = Field(
        ..., alias="sessionWorkspacePath"
    )
    artifact_spool_path: NonBlankStr = Field(..., alias="artifactSpoolPath")
    codex_home_path: NonBlankStr = Field(..., alias="codexHomePath")
    image_ref: NonBlankStr = Field(..., alias="imageRef")
    turn_completion_timeout_seconds: int = Field(
        3600,
        alias="turnCompletionTimeoutSeconds",
        ge=1,
    )
    environment: dict[str, str] = Field(default_factory=dict, alias="environment")
    workspace_spec: dict[str, Any] = Field(default_factory=dict, alias="workspaceSpec")

    @model_validator(mode="after")
    def _normalize_environment(self) -> "LaunchCodexManagedSessionRequest":
        normalized: dict[str, str] = {}
        for raw_key, raw_value in self.environment.items():
            key = require_non_blank(str(raw_key), field_name="environment key")
            normalized[key] = str(raw_value)
        self.environment = normalized
        self.workspace_spec = (
            dict(self.workspace_spec)
            if isinstance(self.workspace_spec, dict)
            else {}
        )
        return self


class SendCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Send a new turn to the remote session container."""

    instructions: NonBlankStr = Field(..., alias="instructions")
    reason: NonBlankStr | None = Field(None, alias="reason")


class SteerCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Provide follow-up steering to an in-flight turn."""

    turn_id: NonBlankStr = Field(..., alias="turnId")
    instructions: NonBlankStr = Field(..., alias="instructions")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class InterruptCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Interrupt an in-flight turn on the remote session container."""

    turn_id: NonBlankStr = Field(..., alias="turnId")
    reason: NonBlankStr | None = Field(None, alias="reason")


class CodexManagedSessionClearRequest(CodexManagedSessionLocator):
    """Explicit session clear/reset request with a new thread boundary."""

    new_thread_id: NonBlankStr = Field(..., alias="newThreadId")
    reason: NonBlankStr | None = Field(None, alias="reason")

    @model_validator(mode="after")
    def _require_new_thread(self) -> "CodexManagedSessionClearRequest":
        if self.new_thread_id == self.thread_id:
            raise ValueError("newThreadId must differ from threadId")
        return self


class TerminateCodexManagedSessionRequest(CodexManagedSessionLocator):
    """Terminate a remote managed session container."""

    reason: NonBlankStr | None = Field(None, alias="reason")


class FetchCodexManagedSessionSummaryRequest(CodexManagedSessionLocator):
    """Fetch the latest continuity summary/checkpoint refs for one session."""

    include_artifact_refs: bool = Field(True, alias="includeArtifactRefs")


class PublishCodexManagedSessionArtifactsRequest(CodexManagedSessionLocator):
    """Publish continuity artifacts for the remote session container."""

    task_run_id: NonBlankStr | None = Field(None, alias="taskRunId")
    step_run_id: NonBlankStr | None = Field(None, alias="stepRunId")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionHandle(_CodexManagedSessionRemoteContract):
    """Remote managed-session handle returned by launch/status/clear/terminate."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    status: ManagedSessionHandleStatus = Field(..., alias="status")
    image_ref: NonBlankStr | None = Field(None, alias="imageRef")
    control_url: NonBlankStr | None = Field(None, alias="controlUrl")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionTurnResponse(_CodexManagedSessionRemoteContract):
    """Typed response for turn send/steer/interrupt activity calls."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    status: ManagedSessionTurnStatus = Field(..., alias="status")
    output_refs: tuple[NonBlankStr, ...] = Field(default=(), alias="outputRefs")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionSummary(_CodexManagedSessionRemoteContract):
    """Latest continuity projection refs for one managed session."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    latest_summary_ref: NonBlankStr | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: NonBlankStr | None = Field(
        None, alias="latestCheckpointRef"
    )
    latest_control_event_ref: NonBlankStr | None = Field(
        None, alias="latestControlEventRef"
    )
    latest_reset_boundary_ref: NonBlankStr | None = Field(
        None, alias="latestResetBoundaryRef"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionArtifactsPublication(_CodexManagedSessionRemoteContract):
    """Artifact publication result for one managed session."""

    session_state: CodexManagedSessionState = Field(..., alias="sessionState")
    published_artifact_refs: tuple[NonBlankStr, ...] = Field(
        default=(), alias="publishedArtifactRefs"
    )
    latest_summary_ref: NonBlankStr | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: NonBlankStr | None = Field(
        None, alias="latestCheckpointRef"
    )
    latest_control_event_ref: NonBlankStr | None = Field(
        None, alias="latestControlEventRef"
    )
    latest_reset_boundary_ref: NonBlankStr | None = Field(
        None, alias="latestResetBoundaryRef"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class CodexManagedSessionRecord(BaseModel):
    """Durable session-level supervision record for one managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    container_id: NonBlankStr = Field(..., alias="containerId")
    thread_id: NonBlankStr = Field(..., alias="threadId")
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    image_ref: NonBlankStr = Field(..., alias="imageRef")
    control_url: NonBlankStr = Field(..., alias="controlUrl")
    status: ManagedSessionRecordStatus = Field(..., alias="status")
    workspace_path: NonBlankStr = Field(..., alias="workspacePath")
    session_workspace_path: NonBlankStr = Field(..., alias="sessionWorkspacePath")
    artifact_spool_path: NonBlankStr = Field(..., alias="artifactSpoolPath")
    stdout_artifact_ref: str | None = Field(None, alias="stdoutArtifactRef")
    stderr_artifact_ref: str | None = Field(None, alias="stderrArtifactRef")
    diagnostics_ref: str | None = Field(None, alias="diagnosticsRef")
    observability_events_ref: str | None = Field(
        None,
        alias="observabilityEventsRef",
    )
    latest_summary_ref: str | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: str | None = Field(None, alias="latestCheckpointRef")
    latest_control_event_ref: str | None = Field(None, alias="latestControlEventRef")
    latest_reset_boundary_ref: str | None = Field(None, alias="latestResetBoundaryRef")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_log_offset: int | None = Field(None, alias="lastLogOffset", ge=0)
    stdout_log_offset: int | None = Field(None, alias="stdoutLogOffset", ge=0)
    stderr_log_offset: int | None = Field(None, alias="stderrLogOffset", ge=0)
    last_log_at: datetime | None = Field(None, alias="lastLogAt")
    error_message: str | None = Field(None, alias="errorMessage")
    started_at: datetime = Field(..., alias="startedAt")
    updated_at: datetime | None = Field(None, alias="updatedAt")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionRecord":
        self.session_id = require_non_blank(self.session_id, field_name="sessionId")
        self.task_run_id = require_non_blank(self.task_run_id, field_name="taskRunId")
        self.container_id = require_non_blank(self.container_id, field_name="containerId")
        self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        runtime_id = canonical_codex_managed_runtime_id(self.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
        self.runtime_id = runtime_id
        self.image_ref = require_non_blank(self.image_ref, field_name="imageRef")
        self.control_url = require_non_blank(self.control_url, field_name="controlUrl")
        self.workspace_path = require_non_blank(self.workspace_path, field_name="workspacePath")
        self.session_workspace_path = require_non_blank(
            self.session_workspace_path,
            field_name="sessionWorkspacePath",
        )
        self.artifact_spool_path = require_non_blank(
            self.artifact_spool_path,
            field_name="artifactSpoolPath",
        )
        if self.stdout_artifact_ref is not None:
            self.stdout_artifact_ref = require_non_blank(
                self.stdout_artifact_ref,
                field_name="stdoutArtifactRef",
            )
        if self.stderr_artifact_ref is not None:
            self.stderr_artifact_ref = require_non_blank(
                self.stderr_artifact_ref,
                field_name="stderrArtifactRef",
            )
        if self.diagnostics_ref is not None:
            self.diagnostics_ref = require_non_blank(
                self.diagnostics_ref,
                field_name="diagnosticsRef",
            )
        if self.observability_events_ref is not None:
            self.observability_events_ref = require_non_blank(
                self.observability_events_ref,
                field_name="observabilityEventsRef",
            )
        if self.latest_summary_ref is not None:
            self.latest_summary_ref = require_non_blank(
                self.latest_summary_ref,
                field_name="latestSummaryRef",
            )
        if self.latest_checkpoint_ref is not None:
            self.latest_checkpoint_ref = require_non_blank(
                self.latest_checkpoint_ref,
                field_name="latestCheckpointRef",
            )
        if self.latest_control_event_ref is not None:
            self.latest_control_event_ref = require_non_blank(
                self.latest_control_event_ref,
                field_name="latestControlEventRef",
            )
        if self.latest_reset_boundary_ref is not None:
            self.latest_reset_boundary_ref = require_non_blank(
                self.latest_reset_boundary_ref,
                field_name="latestResetBoundaryRef",
            )
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id,
                field_name="activeTurnId",
            )
        if self.error_message is not None:
            self.error_message = require_non_blank(
                self.error_message,
                field_name="errorMessage",
            )
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.last_log_at is not None and self.last_log_at.tzinfo is None:
            self.last_log_at = self.last_log_at.replace(tzinfo=UTC)
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=UTC)
        return self

    def session_state(self) -> CodexManagedSessionState:
        return CodexManagedSessionState(
            sessionId=self.session_id,
            sessionEpoch=self.session_epoch,
            containerId=self.container_id,
            threadId=self.thread_id,
            activeTurnId=self.active_turn_id,
        )

    def published_artifact_refs(self) -> tuple[str, ...]:
        refs: list[str] = []
        for ref in (
            self.stdout_artifact_ref,
            self.stderr_artifact_ref,
            self.diagnostics_ref,
            self.observability_events_ref,
            self.latest_summary_ref,
            self.latest_checkpoint_ref,
            self.latest_control_event_ref,
            self.latest_reset_boundary_ref,
        ):
            if ref and ref not in refs:
                refs.append(ref)
        return tuple(refs)


class CodexManagedSessionBinding(BaseModel):
    """Bounded task-scoped session binding passed across workflow boundaries."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workflow_id: NonBlankStr = Field(..., alias="workflowId")
    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int = Field(1, alias="sessionEpoch", ge=1)
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionBinding":
        self.workflow_id = require_non_blank(self.workflow_id, field_name="workflowId")
        self.task_run_id = require_non_blank(self.task_run_id, field_name="taskRunId")
        self.session_id = require_non_blank(self.session_id, field_name="sessionId")
        runtime_id = canonical_codex_managed_runtime_id(self.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
        self.runtime_id = runtime_id
        if self.execution_profile_ref is not None:
            self.execution_profile_ref = require_non_blank(
                self.execution_profile_ref,
                field_name="executionProfileRef",
            )
        return self

    @classmethod
    def from_input(
        cls,
        *,
        workflow_id: str,
        session_input: "CodexManagedSessionWorkflowInput",
    ) -> "CodexManagedSessionBinding":
        runtime_id = session_input.runtime_id
        session_id = session_input.session_id or f"sess:{session_input.task_run_id}:{runtime_id}"
        return cls(
            workflowId=workflow_id,
            taskRunId=session_input.task_run_id,
            sessionId=session_id,
            sessionEpoch=session_input.session_epoch,
            runtimeId=runtime_id,
            executionProfileRef=session_input.execution_profile_ref,
        )


class CodexManagedSessionWorkflowInput(BaseModel):
    """Workflow input for one task-scoped Codex managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    task_run_id: NonBlankStr = Field(..., alias="taskRunId")
    runtime_id: NonBlankStr = Field(..., alias="runtimeId")
    execution_profile_ref: str | None = Field(None, alias="executionProfileRef")
    session_id: str | None = Field(None, alias="sessionId")
    session_epoch: int = Field(1, alias="sessionEpoch", ge=1)
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_control_action: ManagedSessionControlAction | None = Field(
        None, alias="lastControlAction"
    )
    last_control_reason: str | None = Field(None, alias="lastControlReason")
    latest_summary_ref: str | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: str | None = Field(None, alias="latestCheckpointRef")
    latest_control_event_ref: str | None = Field(None, alias="latestControlEventRef")
    latest_reset_boundary_ref: str | None = Field(None, alias="latestResetBoundaryRef")
    continue_as_new_event_threshold: int | None = Field(
        None, alias="continueAsNewEventThreshold", ge=1
    )
    request_tracking_state: tuple["CodexManagedSessionRequestTrackingEntry", ...] = Field(
        default=(), alias="requestTrackingState"
    )

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionWorkflowInput":
        self.task_run_id = require_non_blank(self.task_run_id, field_name="taskRunId")
        runtime_id = canonical_codex_managed_runtime_id(self.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
        self.runtime_id = runtime_id
        if self.execution_profile_ref is not None:
            self.execution_profile_ref = require_non_blank(
                self.execution_profile_ref,
                field_name="executionProfileRef",
            )
        if self.session_id is not None:
            self.session_id = require_non_blank(self.session_id, field_name="sessionId")
        if self.container_id is not None:
            self.container_id = require_non_blank(
                self.container_id,
                field_name="containerId",
            )
        if self.thread_id is not None:
            self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id,
                field_name="activeTurnId",
            )
        if self.last_control_reason is not None:
            self.last_control_reason = require_non_blank(
                self.last_control_reason,
                field_name="lastControlReason",
            )
        if self.latest_summary_ref is not None:
            self.latest_summary_ref = require_non_blank(
                self.latest_summary_ref,
                field_name="latestSummaryRef",
            )
        if self.latest_checkpoint_ref is not None:
            self.latest_checkpoint_ref = require_non_blank(
                self.latest_checkpoint_ref,
                field_name="latestCheckpointRef",
            )
        if self.latest_control_event_ref is not None:
            self.latest_control_event_ref = require_non_blank(
                self.latest_control_event_ref,
                field_name="latestControlEventRef",
            )
        if self.latest_reset_boundary_ref is not None:
            self.latest_reset_boundary_ref = require_non_blank(
                self.latest_reset_boundary_ref,
                field_name="latestResetBoundaryRef",
            )
        return self


class CodexManagedSessionRequestTrackingEntry(BaseModel):
    """Compact metadata for an identified mutating control request."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    request_id: NonBlankStr = Field(..., alias="requestId")
    action: ManagedSessionControlAction = Field(..., alias="action")
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    status: ManagedSessionRequestTrackingStatus = Field(..., alias="status")
    result_ref: str | None = Field(None, alias="resultRef")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionRequestTrackingEntry":
        self.request_id = require_non_blank(self.request_id, field_name="requestId")
        if self.result_ref is not None:
            self.result_ref = require_non_blank(self.result_ref, field_name="resultRef")
        return self


class CodexManagedSessionSendFollowUpRequest(BaseModel):
    """Typed workflow update request for sending a follow-up turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    message: NonBlankStr = Field(..., alias="message")
    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionSendFollowUpRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionInterruptRequest(BaseModel):
    """Typed workflow update request for interrupting an active turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionInterruptRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionSteerRequest(BaseModel):
    """Typed workflow update request for steering an active turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    message: NonBlankStr = Field(..., alias="message")
    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionSteerRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionWorkflowControlRequest(BaseModel):
    """Typed workflow update request for clear/cancel/terminate operations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    reason: str | None = Field(None, alias="reason")
    request_id: str | None = Field(None, alias="requestId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionWorkflowControlRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.request_id is not None:
            self.request_id = require_non_blank(self.request_id, field_name="requestId")
        return self


class CodexManagedSessionAttachRuntimeHandlesSignal(BaseModel):
    """Typed workflow signal for attaching bounded runtime handles."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_epoch: int | None = Field(None, alias="sessionEpoch", ge=1)
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_control_action: ManagedSessionControlAction | None = Field(
        None, alias="lastControlAction"
    )
    last_control_reason: str | None = Field(None, alias="lastControlReason")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionAttachRuntimeHandlesSignal":
        if self.container_id is not None:
            self.container_id = require_non_blank(
                self.container_id,
                field_name="containerId",
            )
        if self.thread_id is not None:
            self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id,
                field_name="activeTurnId",
            )
        if self.last_control_reason is not None:
            self.last_control_reason = require_non_blank(
                self.last_control_reason,
                field_name="lastControlReason",
            )
        return self


class CodexManagedSessionClearUpdateRequest(CodexManagedSessionWorkflowControlRequest):
    """Typed workflow update request for clearing session context."""


class CodexManagedSessionCancelUpdateRequest(CodexManagedSessionWorkflowControlRequest):
    """Typed workflow update request for canceling the active session turn."""


class CodexManagedSessionTerminateUpdateRequest(CodexManagedSessionWorkflowControlRequest):
    """Typed workflow update request for terminating the managed session."""


class CodexManagedSessionSnapshot(BaseModel):
    """Workflow-owned snapshot of one task-scoped Codex session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    binding: CodexManagedSessionBinding = Field(..., alias="binding")
    status: CodexManagedSessionWorkflowStatus = Field(..., alias="status")
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")
    last_control_action: ManagedSessionControlAction | None = Field(
        None, alias="lastControlAction"
    )
    last_control_reason: str | None = Field(None, alias="lastControlReason")
    latest_summary_ref: str | None = Field(None, alias="latestSummaryRef")
    latest_checkpoint_ref: str | None = Field(None, alias="latestCheckpointRef")
    latest_control_event_ref: str | None = Field(None, alias="latestControlEventRef")
    latest_reset_boundary_ref: str | None = Field(None, alias="latestResetBoundaryRef")
    termination_requested: bool = Field(False, alias="terminationRequested")
    request_tracking_state: tuple[CodexManagedSessionRequestTrackingEntry, ...] = Field(
        default=(), alias="requestTrackingState"
    )


class ClaudeSurfaceBinding(BaseModel):
    """Durable representation of one surface attached to a Claude session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    surface_id: NonBlankStr = Field(..., alias="surfaceId")
    surface_kind: ClaudeSurfaceKind = Field(..., alias="surfaceKind")
    projection_mode: ClaudeSurfaceProjectionMode = Field(
        ..., alias="projectionMode"
    )
    connection_state: ClaudeSurfaceConnectionState = Field(
        "connected", alias="connectionState"
    )
    interactive: bool = Field(..., alias="interactive")


class ClaudeManagedSession(BaseModel):
    """Canonical Claude Code session record for the shared session plane."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    runtime_family: ClaudeRuntimeFamily = Field(
        "claude_code", alias="runtimeFamily"
    )
    execution_owner: ClaudeExecutionOwner = Field(..., alias="executionOwner")
    state: ClaudeSessionState = Field(..., alias="state")
    primary_surface: ClaudeSurfaceKind = Field(..., alias="primarySurface")
    projection_mode: ClaudeProjectionMode = Field(..., alias="projectionMode")
    surface_bindings: tuple[ClaudeSurfaceBinding, ...] = Field(
        default=(), alias="surfaceBindings"
    )
    active_turn_id: NonBlankStr | None = Field(None, alias="activeTurnId")
    parent_session_id: NonBlankStr | None = Field(None, alias="parentSessionId")
    fork_of_session_id: NonBlankStr | None = Field(None, alias="forkOfSessionId")
    handoff_from_session_id: NonBlankStr | None = Field(
        None, alias="handoffFromSessionId"
    )
    session_group_id: NonBlankStr | None = Field(None, alias="sessionGroupId")
    created_by: ClaudeSessionCreatedBy = Field(..., alias="createdBy")
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    ended_at: datetime | None = Field(None, alias="endedAt")
    extensions: dict[str, Any] = Field(default_factory=dict, alias="extensions")

    @field_validator("extensions", mode="after")
    @classmethod
    def _validate_extensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="extensions")

    @model_validator(mode="after")
    def _validate_datetimes(self) -> "ClaudeManagedSession":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=UTC)
        if self.ended_at is not None and self.ended_at.tzinfo is None:
            self.ended_at = self.ended_at.replace(tzinfo=UTC)
        return self

    def with_remote_projection(
        self,
        *,
        surface_id: str,
        surface_kind: ClaudeSurfaceKind,
        interactive: bool,
        updated_at: datetime,
    ) -> "ClaudeManagedSession":
        """Return a copy with an added Remote Control projection surface."""

        binding = ClaudeSurfaceBinding(
            surfaceId=surface_id,
            surfaceKind=surface_kind,
            projectionMode="remote_projection",
            connectionState="connected",
            interactive=interactive,
        )
        return self.model_copy(
            deep=True,
            update={
                "surface_bindings": (*self.surface_bindings, binding),
                "updated_at": updated_at,
            }
        )

    def cloud_handoff(
        self,
        *,
        session_id: str,
        primary_surface: ClaudeSurfaceKind,
        created_by: ClaudeSessionCreatedBy,
        created_at: datetime,
    ) -> "ClaudeManagedSession":
        """Create a distinct cloud-owned session with lineage to this session."""

        destination_session_id = require_non_blank(
            session_id, field_name="sessionId"
        )
        if destination_session_id == self.session_id:
            raise ValueError("cloud_handoff must create a distinct sessionId")
        return ClaudeManagedSession(
            sessionId=destination_session_id,
            executionOwner="anthropic_cloud_vm",
            state="creating",
            primarySurface=primary_surface,
            projectionMode="handoff",
            surfaceBindings=(),
            handoffFromSessionId=self.session_id,
            createdBy=created_by,
            createdAt=created_at,
            updatedAt=created_at,
        )


class ClaudeManagedTurn(BaseModel):
    """Bounded input turn processed within a Claude managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    turn_id: NonBlankStr = Field(..., alias="turnId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    input_origin: ClaudeTurnInputOrigin = Field(..., alias="inputOrigin")
    state: ClaudeTurnState = Field(..., alias="state")
    summary: str | None = Field(None, alias="summary")
    started_at: datetime = Field(..., alias="startedAt")
    completed_at: datetime | None = Field(None, alias="completedAt")

    @model_validator(mode="after")
    def _validate_datetimes(self) -> "ClaudeManagedTurn":
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.completed_at is not None and self.completed_at.tzinfo is None:
            self.completed_at = self.completed_at.replace(tzinfo=UTC)
        return self


class ClaudeManagedWorkItem(BaseModel):
    """Event-bearing work unit emitted during a Claude managed turn."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    item_id: NonBlankStr = Field(..., alias="itemId")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    kind: ClaudeWorkItemKind = Field(..., alias="kind")
    status: ClaudeWorkItemStatus = Field(..., alias="status")
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    started_at: datetime = Field(..., alias="startedAt")
    ended_at: datetime | None = Field(None, alias="endedAt")

    @field_validator("payload", mode="after")
    @classmethod
    def _validate_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="payload")

    @model_validator(mode="after")
    def _validate_datetimes(self) -> "ClaudeManagedWorkItem":
        if self.started_at.tzinfo is None:
            self.started_at = self.started_at.replace(tzinfo=UTC)
        if self.ended_at is not None and self.ended_at.tzinfo is None:
            self.ended_at = self.ended_at.replace(tzinfo=UTC)
        return self


class ClaudePolicyPermissions(BaseModel):
    """Compiled Claude permission controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    mode: ClaudePermissionMode = "default"
    allow: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = Field(
        default=(), alias="protectedPaths"
    )
    auto_mode_enabled: bool = Field(False, alias="autoModeEnabled")
    bypass_disabled: bool = Field(False, alias="bypassDisabled")
    auto_disabled: bool = Field(False, alias="autoDisabled")


class ClaudePolicySandbox(BaseModel):
    """Compiled Claude sandbox controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    filesystem_scope: dict[str, Any] = Field(
        default_factory=dict, alias="filesystemScope"
    )
    network_scope: dict[str, Any] = Field(
        default_factory=dict, alias="networkScope"
    )

    @field_validator("filesystem_scope", "network_scope", mode="after")
    @classmethod
    def _validate_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="scope")


class ClaudePolicyHooks(BaseModel):
    """Compiled Claude hook controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    allow_managed_only: bool = Field(False, alias="allowManagedOnly")
    registry_hash: str | None = Field(None, alias="registryHash")


class ClaudePolicyMcp(BaseModel):
    """Compiled Claude MCP controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    allowed_servers: tuple[str, ...] = Field(
        default=(), alias="allowedServers"
    )
    denied_servers: tuple[str, ...] = Field(default=(), alias="deniedServers")
    allow_managed_only: bool = Field(False, alias="allowManagedOnly")


class ClaudePolicyMemory(BaseModel):
    """Compiled Claude memory controls."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    include_auto_memory: bool = Field(True, alias="includeAutoMemory")
    managed_claude_md_enabled: bool = Field(
        False, alias="managedClaudeMdEnabled"
    )
    excludes: tuple[str, ...] = ()


class ClaudePolicyBootstrapTemplate(BaseModel):
    """Claude BootstrapPreferences represented as bootstrap templates only."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["bootstrap_template"] = "bootstrap_template"
    name: NonBlankStr
    value: Any


class ClaudePolicySource(BaseModel):
    """Candidate policy source for Claude managed policy resolution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    source_kind: ClaudePolicySourceKind = Field(..., alias="sourceKind")
    settings: dict[str, Any] = Field(default_factory=dict)
    fetch_state: ClaudePolicyFetchState = Field("fetched", alias="fetchState")
    supported: bool = True
    risky_controls: tuple[str, ...] = Field(
        default=(), alias="riskyControls"
    )
    version: str | None = None

    @field_validator("settings", mode="after")
    @classmethod
    def _validate_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="settings")


class ClaudePolicyEnvelope(BaseModel):
    """Versioned effective policy attached to a Claude managed session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    policy_envelope_id: NonBlankStr = Field(..., alias="policyEnvelopeId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    provider_mode: ClaudeProviderMode = Field(..., alias="providerMode")
    managed_source_kind: ClaudeManagedSourceKind = Field(
        ..., alias="managedSourceKind"
    )
    policy_fetch_state: ClaudePolicyFetchState = Field(
        ..., alias="policyFetchState"
    )
    managed_source_version: str | None = Field(
        None, alias="managedSourceVersion"
    )
    policy_trust_level: ClaudePolicyTrustLevel = Field(
        ..., alias="policyTrustLevel"
    )
    permissions: ClaudePolicyPermissions = Field(
        default_factory=ClaudePolicyPermissions
    )
    sandbox: ClaudePolicySandbox = Field(default_factory=ClaudePolicySandbox)
    hooks: ClaudePolicyHooks = Field(default_factory=ClaudePolicyHooks)
    mcp: ClaudePolicyMcp = Field(default_factory=ClaudePolicyMcp)
    memory: ClaudePolicyMemory = Field(default_factory=ClaudePolicyMemory)
    bootstrap_templates: tuple[ClaudePolicyBootstrapTemplate, ...] = Field(
        default=(), alias="bootstrapTemplates"
    )
    security_dialog_required: bool = Field(
        False, alias="securityDialogRequired"
    )
    version: int = Field(..., ge=1)
    effective_settings: dict[str, Any] = Field(
        default_factory=dict, alias="effectiveSettings"
    )
    observability_sources: tuple[ClaudePolicySourceKind, ...] = Field(
        default=(), alias="observabilitySources"
    )
    admin_visibility: dict[str, Any] = Field(
        default_factory=dict, alias="adminVisibility"
    )
    user_visibility: dict[str, Any] = Field(
        default_factory=dict, alias="userVisibility"
    )

    @field_validator(
        "effective_settings",
        "admin_visibility",
        "user_visibility",
        mode="after",
    )
    @classmethod
    def _validate_mapping(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="policy")


class ClaudePolicyHandshake(BaseModel):
    """Startup readiness after Claude policy resolution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: NonBlankStr = Field(..., alias="sessionId")
    policy_envelope_id: NonBlankStr | None = Field(
        None, alias="policyEnvelopeId"
    )
    state: ClaudePolicyHandshakeState
    reason: str | None = None
    interactive: bool


class ClaudePolicyEvent(BaseModel):
    """Append-only Claude policy lifecycle event."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: NonBlankStr = Field(..., alias="eventId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    policy_envelope_id: NonBlankStr | None = Field(
        None, alias="policyEnvelopeId"
    )
    event_type: ClaudePolicyEventType = Field(..., alias="eventType")
    occurred_at: datetime = Field(..., alias="occurredAt")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudePolicyEvent":
        if self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=UTC)
        return self


_MANAGED_SOURCE_ORDER: tuple[ClaudePolicySourceKind, ...] = (
    "server_managed",
    "endpoint_managed",
)
_LOWER_SCOPE_SOURCE_KINDS: frozenset[ClaudePolicySourceKind] = frozenset(
    {"local_project", "shared_project", "user", "cli"}
)


def _selected_managed_source(
    sources: tuple[ClaudePolicySource, ...],
) -> ClaudePolicySource | None:
    for source_kind in _MANAGED_SOURCE_ORDER:
        for source in sources:
            if (
                source.source_kind == source_kind
                and source.supported
                and bool(source.settings)
            ):
                return source
    return None


def _fail_closed_managed_source(
    sources: tuple[ClaudePolicySource, ...],
) -> ClaudePolicySource | None:
    server_source = next(
        (
            source
            for source in sources
            if source.source_kind == "server_managed" and source.supported
        ),
        None,
    )
    if server_source is not None:
        if server_source.fetch_state == "fail_closed":
            return server_source
        if server_source.settings:
            return None

    endpoint_source = next(
        (
            source
            for source in sources
            if source.source_kind == "endpoint_managed" and source.supported
        ),
        None,
    )
    if (
        endpoint_source is not None
        and endpoint_source.fetch_state == "fail_closed"
    ):
        return endpoint_source
    return None


def _bootstrap_templates(
    settings: dict[str, Any],
) -> tuple[ClaudePolicyBootstrapTemplate, ...]:
    raw_preferences = settings.get("bootstrapPreferences") or ()
    templates: list[ClaudePolicyBootstrapTemplate] = []
    if isinstance(raw_preferences, list | tuple):
        for index, item in enumerate(raw_preferences):
            if isinstance(item, dict):
                name = item.get("name") or f"bootstrap-{index + 1}"
                value = item.get("value")
            else:
                name = f"bootstrap-{index + 1}"
                value = item
            templates.append(
                ClaudePolicyBootstrapTemplate(name=str(name), value=value)
            )
    return tuple(templates)


def _effective_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in settings.items()
        if key not in {"bootstrapPreferences", "managedDefaults"}
    }


def _policy_permissions(settings: dict[str, Any]) -> ClaudePolicyPermissions:
    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        return ClaudePolicyPermissions.model_validate(permissions)
    return ClaudePolicyPermissions()


def _policy_sandbox(settings: dict[str, Any]) -> ClaudePolicySandbox:
    sandbox = settings.get("sandbox")
    if isinstance(sandbox, dict):
        return ClaudePolicySandbox.model_validate(sandbox)
    return ClaudePolicySandbox()


def _policy_hooks(settings: dict[str, Any]) -> ClaudePolicyHooks:
    hooks = settings.get("hooks")
    if isinstance(hooks, dict):
        return ClaudePolicyHooks.model_validate(hooks)
    return ClaudePolicyHooks()


def _policy_mcp(settings: dict[str, Any]) -> ClaudePolicyMcp:
    mcp = settings.get("mcp")
    if isinstance(mcp, dict):
        return ClaudePolicyMcp.model_validate(mcp)
    return ClaudePolicyMcp()


def _policy_memory(settings: dict[str, Any]) -> ClaudePolicyMemory:
    memory = settings.get("memory")
    if isinstance(memory, dict):
        return ClaudePolicyMemory.model_validate(memory)
    return ClaudePolicyMemory()


def _policy_trust_level(
    managed_source_kind: ClaudeManagedSourceKind,
) -> ClaudePolicyTrustLevel:
    if managed_source_kind == "endpoint_managed":
        return "endpoint_enforced"
    if managed_source_kind == "server_managed":
        return "server_managed_best_effort"
    return "unmanaged"


def _policy_event(
    *,
    policy_envelope_id: str | None,
    session_id: str,
    event_type: ClaudePolicyEventType,
    occurred_at: datetime,
    sequence: int,
    metadata: dict[str, Any] | None = None,
) -> ClaudePolicyEvent:
    stable_id_part = policy_envelope_id or session_id
    return ClaudePolicyEvent(
        eventId=f"{stable_id_part}:{sequence}:{event_type}",
        sessionId=session_id,
        policyEnvelopeId=policy_envelope_id,
        eventType=event_type,
        occurredAt=occurred_at,
        metadata=metadata or {},
    )


def resolve_claude_policy_envelope(
    *,
    session_id: str,
    policy_envelope_id: str,
    provider_mode: ClaudeProviderMode,
    sources: tuple[ClaudePolicySource, ...],
    version: int = 1,
    interactive: bool = False,
    fail_closed_on_refresh_failure: bool = False,
    occurred_at: datetime | None = None,
) -> tuple[
    ClaudePolicyEnvelope | None,
    ClaudePolicyHandshake,
    tuple[ClaudePolicyEvent, ...],
]:
    """Resolve compact Claude policy fixture sources into an effective envelope."""

    event_time = occurred_at or datetime.now(UTC)
    events: list[ClaudePolicyEvent] = [
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type="policy.fetch.started",
            occurred_at=event_time,
            sequence=1,
        )
    ]

    fail_closed_source = (
        _fail_closed_managed_source(sources)
        if fail_closed_on_refresh_failure
        else None
    )
    if fail_closed_source is not None:
        events.append(
            _policy_event(
                policy_envelope_id=None,
                session_id=session_id,
                event_type="policy.fetch.failed",
                occurred_at=event_time,
                sequence=2,
                metadata={
                    "fetchState": "fail_closed",
                    "sourceKind": fail_closed_source.source_kind,
                },
            )
        )
        return (
            None,
            ClaudePolicyHandshake(
                sessionId=session_id,
                policyEnvelopeId=None,
                state="fail_closed",
                reason="Policy refresh failed in fail-closed mode.",
                interactive=interactive,
            ),
            tuple(events),
        )

    selected_source = _selected_managed_source(sources)
    managed_source_kind: ClaudeManagedSourceKind = (
        selected_source.source_kind if selected_source is not None else "none"
    )
    settings = selected_source.settings if selected_source is not None else {}
    fetch_state: ClaudePolicyFetchState = (
        selected_source.fetch_state
        if selected_source is not None
        else "not_applicable"
    )
    source_version = selected_source.version if selected_source is not None else None
    trust_level = _policy_trust_level(managed_source_kind)
    risky_controls = selected_source.risky_controls if selected_source else ()
    security_dialog_required = bool(risky_controls)
    observability_sources = tuple(
        source.source_kind
        for source in sources
        if source.source_kind in _LOWER_SCOPE_SOURCE_KINDS and source.settings
    )

    fetch_event_type: ClaudePolicyEventType = (
        "policy.fetch.failed"
        if fetch_state in {"fetch_failed", "fail_closed"}
        else "policy.fetch.succeeded"
    )
    events.append(
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type=fetch_event_type,
            occurred_at=event_time,
            sequence=2,
            metadata={"fetchState": fetch_state},
        )
    )

    admin_visibility = {
        "fetchState": fetch_state,
        "managedSourceKind": managed_source_kind,
        "policyTrustLevel": trust_level,
        "observabilitySources": list(observability_sources),
    }
    user_visibility = {
        "status": "managed" if managed_source_kind != "none" else "unmanaged"
    }
    envelope = ClaudePolicyEnvelope(
        policyEnvelopeId=policy_envelope_id,
        sessionId=session_id,
        providerMode=provider_mode,
        managedSourceKind=managed_source_kind,
        policyFetchState=fetch_state,
        managedSourceVersion=source_version,
        policyTrustLevel=trust_level,
        permissions=_policy_permissions(settings),
        sandbox=_policy_sandbox(settings),
        hooks=_policy_hooks(settings),
        mcp=_policy_mcp(settings),
        memory=_policy_memory(settings),
        bootstrapTemplates=_bootstrap_templates(settings),
        securityDialogRequired=security_dialog_required,
        version=version,
        effectiveSettings=_effective_settings(settings),
        observabilitySources=observability_sources,
        adminVisibility=admin_visibility,
        userVisibility=user_visibility,
    )

    events.append(
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type="policy.compiled",
            occurred_at=event_time,
            sequence=3,
        )
    )
    events.append(
        _policy_event(
            policy_envelope_id=policy_envelope_id,
            session_id=session_id,
            event_type="policy.version.changed",
            occurred_at=event_time,
            sequence=4,
            metadata={"version": version},
        )
    )

    if security_dialog_required:
        handshake_state: ClaudePolicyHandshakeState = (
            "security_dialog_required" if interactive else "blocked"
        )
        reason = (
            "Risky managed controls require a security dialog."
            if interactive
            else "Risky managed controls require a security dialog in a non-interactive session."
        )
        events.append(
            _policy_event(
                policy_envelope_id=policy_envelope_id,
                session_id=session_id,
                event_type="policy.dialog.required",
                occurred_at=event_time,
                sequence=5,
                metadata={"riskyControls": list(risky_controls)},
            )
        )
    else:
        handshake_state = "ready"
        reason = None

    return (
        envelope,
        ClaudePolicyHandshake(
            sessionId=session_id,
            policyEnvelopeId=policy_envelope_id,
            state=handshake_state,
            reason=reason,
            interactive=interactive,
        ),
        tuple(events),
    )


__all__ = [
    "CODEX_MANAGED_SESSION_CONTROL_ACTIONS",
    "ClaudeExecutionOwner",
    "ClaudeManagedSession",
    "ClaudeManagedSourceKind",
    "ClaudeManagedTurn",
    "ClaudeManagedWorkItem",
    "ClaudePermissionMode",
    "ClaudePolicyBootstrapTemplate",
    "ClaudePolicyEnvelope",
    "ClaudePolicyEvent",
    "ClaudePolicyEventType",
    "ClaudePolicyFetchState",
    "ClaudePolicyHandshake",
    "ClaudePolicyHandshakeState",
    "ClaudePolicyHooks",
    "ClaudePolicyMcp",
    "ClaudePolicyMemory",
    "ClaudePolicyPermissions",
    "ClaudePolicySandbox",
    "ClaudePolicySource",
    "ClaudePolicySourceKind",
    "ClaudePolicyTrustLevel",
    "ClaudeProjectionMode",
    "ClaudeProviderMode",
    "ClaudeRuntimeFamily",
    "ClaudeSessionCreatedBy",
    "ClaudeSessionState",
    "ClaudeSurfaceBinding",
    "ClaudeSurfaceConnectionState",
    "ClaudeSurfaceKind",
    "ClaudeTurnInputOrigin",
    "ClaudeTurnState",
    "ClaudeWorkItemKind",
    "ClaudeWorkItemStatus",
    "CodexManagedSessionArtifactsPublication",
    "CodexManagedSessionAttachRuntimeHandlesSignal",
    "CodexManagedSessionBinding",
    "CodexManagedSessionCancelUpdateRequest",
    "CodexManagedSessionClearRequest",
    "CodexManagedSessionClearUpdateRequest",
    "CodexManagedSessionHandle",
    "CodexManagedSessionInterruptRequest",
    "CodexManagedSessionLocator",
    "CodexManagedSessionPlaneContract",
    "CodexManagedSessionRecord",
    "CodexManagedSessionRequestTrackingEntry",
    "CodexManagedSessionSendFollowUpRequest",
    "CodexManagedSessionSnapshot",
    "CodexManagedSessionState",
    "CodexManagedSessionSteerRequest",
    "CodexManagedSessionSummary",
    "CodexManagedSessionTerminateUpdateRequest",
    "CodexManagedSessionTurnResponse",
    "CodexManagedSessionWorkflowControlRequest",
    "CodexManagedSessionWorkflowInput",
    "CodexManagedSessionWorkflowStatus",
    "FetchCodexManagedSessionSummaryRequest",
    "InterruptCodexManagedSessionTurnRequest",
    "LaunchCodexManagedSessionRequest",
    "ManagedSessionContainerBackend",
    "ManagedSessionControlAction",
    "ManagedSessionControlMode",
    "ManagedSessionHandleStatus",
    "ManagedSessionProtocol",
    "ManagedSessionRecordStatus",
    "ManagedSessionRequestTrackingStatus",
    "ManagedSessionTurnStatus",
    "PublishCodexManagedSessionArtifactsRequest",
    "resolve_claude_policy_envelope",
    "SendCodexManagedSessionTurnRequest",
    "SteerCodexManagedSessionTurnRequest",
    "TerminateCodexManagedSessionRequest",
    "canonical_codex_managed_runtime_id",
]
