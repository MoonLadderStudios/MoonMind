"""Canonical contracts for the Codex managed session plane."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from moonmind.schemas._validation import NonBlankStr, require_non_blank


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
    environment: dict[str, str] = Field(default_factory=dict, alias="environment")

    @model_validator(mode="after")
    def _normalize_environment(self) -> "LaunchCodexManagedSessionRequest":
        normalized: dict[str, str] = {}
        for raw_key, raw_value in self.environment.items():
            key = require_non_blank(str(raw_key), field_name="environment key")
            normalized[key] = str(raw_value)
        self.environment = normalized
        return self


class SendCodexManagedSessionTurnRequest(CodexManagedSessionLocator):
    """Send a new turn to the remote session container."""

    instructions: NonBlankStr = Field(..., alias="instructions")
    input_refs: tuple[NonBlankStr, ...] = Field(default=(), alias="inputRefs")
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


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
        return self


class CodexManagedSessionControlRequest(BaseModel):
    """Control payload applied to the task-scoped Codex session workflow."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: ManagedSessionControlAction = Field(..., alias="action")
    reason: str | None = Field(None, alias="reason")
    container_id: str | None = Field(None, alias="containerId")
    thread_id: str | None = Field(None, alias="threadId")
    active_turn_id: str | None = Field(None, alias="activeTurnId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionControlRequest":
        if self.reason is not None:
            self.reason = require_non_blank(self.reason, field_name="reason")
        if self.container_id is not None:
            self.container_id = require_non_blank(
                self.container_id, field_name="containerId"
            )
        if self.thread_id is not None:
            self.thread_id = require_non_blank(self.thread_id, field_name="threadId")
        if self.active_turn_id is not None:
            self.active_turn_id = require_non_blank(
                self.active_turn_id, field_name="activeTurnId"
            )
        return self


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
    termination_requested: bool = Field(False, alias="terminationRequested")


__all__ = [
    "CODEX_MANAGED_SESSION_CONTROL_ACTIONS",
    "CodexManagedSessionArtifactsPublication",
    "CodexManagedSessionBinding",
    "CodexManagedSessionClearRequest",
    "CodexManagedSessionControlRequest",
    "CodexManagedSessionHandle",
    "CodexManagedSessionLocator",
    "CodexManagedSessionPlaneContract",
    "CodexManagedSessionRecord",
    "CodexManagedSessionSnapshot",
    "CodexManagedSessionState",
    "CodexManagedSessionSummary",
    "CodexManagedSessionTurnResponse",
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
    "ManagedSessionTurnStatus",
    "PublishCodexManagedSessionArtifactsRequest",
    "SendCodexManagedSessionTurnRequest",
    "SteerCodexManagedSessionTurnRequest",
    "TerminateCodexManagedSessionRequest",
    "canonical_codex_managed_runtime_id",
]
