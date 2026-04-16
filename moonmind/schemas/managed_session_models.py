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
ClaudeDecisionStage = Literal[
    "session_state_guard",
    "pretool_hooks",
    "permission_rules",
    "protected_path_guard",
    "permission_mode_baseline",
    "sandbox_substitution",
    "auto_mode_classifier",
    "interactive_prompt_or_headless_resolution",
    "runtime_execution",
    "posttool_hooks",
    "checkpoint_capture",
]
ClaudeDecisionProposalKind = Literal[
    "tool",
    "file",
    "network",
    "mcp",
    "hook",
    "classifier",
    "prompt",
    "runtime",
    "checkpoint",
]
ClaudeDecisionOutcome = Literal[
    "proposed",
    "mutated",
    "allowed",
    "asked",
    "denied",
    "deferred",
    "canceled",
    "resolved",
    "failed",
    "executed",
]
ClaudeDecisionProvenanceSource = Literal[
    "session_state",
    "policy",
    "hook",
    "protected_path",
    "permission_mode",
    "sandbox",
    "classifier",
    "user",
    "headless_policy",
    "runtime",
    "checkpoint",
]
ClaudeDecisionEventName = Literal[
    "decision.proposed",
    "decision.mutated",
    "decision.allowed",
    "decision.asked",
    "decision.denied",
    "decision.deferred",
    "decision.canceled",
    "decision.resolved",
]
ClaudeHookSourceScope = Literal["managed", "user", "project", "plugin", "sdk"]
ClaudeHookOutcome = Literal[
    "allow",
    "deny",
    "ask",
    "mutate",
    "error",
    "noop",
    "defer",
]
ClaudeHookWorkEventName = Literal[
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
]

CLAUDE_DECISION_STAGE_ORDER: tuple[ClaudeDecisionStage, ...] = (
    "session_state_guard",
    "pretool_hooks",
    "permission_rules",
    "protected_path_guard",
    "permission_mode_baseline",
    "sandbox_substitution",
    "auto_mode_classifier",
    "interactive_prompt_or_headless_resolution",
    "runtime_execution",
    "posttool_hooks",
    "checkpoint_capture",
)
CLAUDE_DECISION_EVENT_NAMES: tuple[ClaudeDecisionEventName, ...] = (
    "decision.proposed",
    "decision.mutated",
    "decision.allowed",
    "decision.asked",
    "decision.denied",
    "decision.deferred",
    "decision.canceled",
    "decision.resolved",
)
CLAUDE_HOOK_WORK_EVENT_NAMES: tuple[ClaudeHookWorkEventName, ...] = (
    "work.hook.started",
    "work.hook.completed",
    "work.hook.blocked",
)

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
    event_name: ClaudeHookWorkEventName | None = Field(None, alias="eventName")
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


def _decision_event_for_outcome(
    outcome: ClaudeDecisionOutcome,
) -> ClaudeDecisionEventName:
    if outcome == "allowed":
        return "decision.allowed"
    if outcome == "asked":
        return "decision.asked"
    if outcome == "denied":
        return "decision.denied"
    if outcome == "deferred":
        return "decision.deferred"
    if outcome == "canceled":
        return "decision.canceled"
    if outcome == "mutated":
        return "decision.mutated"
    if outcome == "proposed":
        return "decision.proposed"
    return "decision.resolved"


class ClaudeDecisionPoint(BaseModel):
    """Normalized decision-stage record for Claude managed sessions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    decision_id: NonBlankStr = Field(..., alias="decisionId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    work_item_id: NonBlankStr | None = Field(None, alias="workItemId")
    proposal_kind: ClaudeDecisionProposalKind = Field(..., alias="proposalKind")
    origin_stage: ClaudeDecisionStage = Field(..., alias="originStage")
    outcome: ClaudeDecisionOutcome = Field(..., alias="outcome")
    provenance_source: ClaudeDecisionProvenanceSource = Field(
        ..., alias="provenanceSource"
    )
    event_name: ClaudeDecisionEventName = Field(..., alias="eventName")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("metadata", mode="after")
    @classmethod
    def _validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="metadata")

    @model_validator(mode="after")
    def _validate_invariants(self) -> "ClaudeDecisionPoint":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        if (
            self.origin_stage == "protected_path_guard"
            and self.outcome == "allowed"
        ):
            raise ValueError("Protected path decisions cannot be allowed")
        if self.provenance_source == "protected_path" and (
            self.origin_stage != "protected_path_guard"
            or self.outcome not in {"asked", "denied", "deferred"}
        ):
            raise ValueError(
                "Protected path decisions must ask, deny, or defer at protected_path_guard"
            )
        if self.provenance_source == "classifier" and (
            self.origin_stage != "auto_mode_classifier"
        ):
            raise ValueError(
                "Classifier decisions must originate from auto_mode_classifier"
            )
        if self.provenance_source == "headless_policy" and (
            self.origin_stage != "interactive_prompt_or_headless_resolution"
            or self.outcome not in {"denied", "deferred"}
        ):
            raise ValueError("Headless decisions must deny or defer")
        return self

    @classmethod
    def protected_path(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        outcome: Literal["asked", "denied", "deferred"],
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        if outcome == "allowed":
            raise ValueError("Protected path decisions cannot be allowed")
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage="protected_path_guard",
            outcome=outcome,
            provenanceSource="protected_path",
            eventName=_decision_event_for_outcome(outcome),
            metadata=metadata or {},
            createdAt=created_at,
        )

    @classmethod
    def classifier(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        outcome: ClaudeDecisionOutcome,
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage="auto_mode_classifier",
            outcome=outcome,
            provenanceSource="classifier",
            eventName=_decision_event_for_outcome(outcome),
            metadata=metadata or {},
            createdAt=created_at,
        )

    @classmethod
    def headless_resolution(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        outcome: Literal["denied", "deferred"],
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        if outcome not in {"denied", "deferred"}:
            raise ValueError("Headless decisions must deny or defer")
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage="interactive_prompt_or_headless_resolution",
            outcome=outcome,
            provenanceSource="headless_policy",
            eventName=_decision_event_for_outcome(outcome),
            metadata=metadata or {},
            createdAt=created_at,
        )

    @classmethod
    def hook_tightened(
        cls,
        *,
        decision_id: str,
        session_id: str,
        turn_id: str,
        proposal_kind: ClaudeDecisionProposalKind,
        origin_stage: Literal["pretool_hooks", "posttool_hooks"],
        outcome: Literal["asked", "denied", "deferred", "mutated"],
        created_at: datetime,
        work_item_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ClaudeDecisionPoint":
        if origin_stage not in {"pretool_hooks", "posttool_hooks"}:
            raise ValueError(
                "Hook decisions must originate from pretool_hooks or posttool_hooks"
            )
        next_metadata = dict(metadata or {})
        next_metadata["hookTightened"] = True
        return cls(
            decisionId=decision_id,
            sessionId=session_id,
            turnId=turn_id,
            workItemId=work_item_id,
            proposalKind=proposal_kind,
            originStage=origin_stage,
            outcome=outcome,
            provenanceSource="hook",
            eventName=_decision_event_for_outcome(outcome),
            metadata=next_metadata,
            createdAt=created_at,
        )


class ClaudeHookAudit(BaseModel):
    """Normalized Claude hook execution audit record."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    audit_id: NonBlankStr = Field(..., alias="auditId")
    session_id: NonBlankStr = Field(..., alias="sessionId")
    turn_id: NonBlankStr = Field(..., alias="turnId")
    decision_id: NonBlankStr | None = Field(None, alias="decisionId")
    hook_name: NonBlankStr = Field(..., alias="hookName")
    source_scope: ClaudeHookSourceScope = Field(..., alias="sourceScope")
    event_type: NonBlankStr = Field(..., alias="eventType")
    matcher: NonBlankStr = Field(..., alias="matcher")
    outcome: ClaudeHookOutcome = Field(..., alias="outcome")
    audit_data: dict[str, Any] = Field(default_factory=dict, alias="auditData")
    created_at: datetime = Field(..., alias="createdAt")

    @field_validator("audit_data", mode="after")
    @classmethod
    def _validate_audit_data(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_compact_temporal_mapping(value, field_name="auditData")

    @model_validator(mode="after")
    def _validate_datetime(self) -> "ClaudeHookAudit":
        if self.created_at.tzinfo is None:
            self.created_at = self.created_at.replace(tzinfo=UTC)
        return self


__all__ = [
    "CODEX_MANAGED_SESSION_CONTROL_ACTIONS",
    "CLAUDE_DECISION_EVENT_NAMES",
    "CLAUDE_DECISION_STAGE_ORDER",
    "CLAUDE_HOOK_WORK_EVENT_NAMES",
    "ClaudeDecisionEventName",
    "ClaudeDecisionOutcome",
    "ClaudeDecisionPoint",
    "ClaudeDecisionProposalKind",
    "ClaudeDecisionProvenanceSource",
    "ClaudeDecisionStage",
    "ClaudeExecutionOwner",
    "ClaudeHookAudit",
    "ClaudeHookOutcome",
    "ClaudeHookSourceScope",
    "ClaudeHookWorkEventName",
    "ClaudeManagedSession",
    "ClaudeManagedTurn",
    "ClaudeManagedWorkItem",
    "ClaudeProjectionMode",
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
    "SendCodexManagedSessionTurnRequest",
    "SteerCodexManagedSessionTurnRequest",
    "TerminateCodexManagedSessionRequest",
    "canonical_codex_managed_runtime_id",
]
