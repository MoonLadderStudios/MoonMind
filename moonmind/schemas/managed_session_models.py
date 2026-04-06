"""Canonical contracts for the Codex managed session plane."""

from __future__ import annotations

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
    """Minimal identifier set for addressing one managed session remotely."""

    session_id: NonBlankStr = Field(..., alias="sessionId")
    session_epoch: int | None = Field(None, alias="sessionEpoch", ge=1)
    container_id: NonBlankStr | None = Field(None, alias="containerId")
    thread_id: NonBlankStr | None = Field(None, alias="threadId")


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
        if self.thread_id is not None and self.new_thread_id == self.thread_id:
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
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


__all__ = [
    "CODEX_MANAGED_SESSION_CONTROL_ACTIONS",
    "CodexManagedSessionArtifactsPublication",
    "CodexManagedSessionClearRequest",
    "CodexManagedSessionHandle",
    "CodexManagedSessionLocator",
    "CodexManagedSessionPlaneContract",
    "CodexManagedSessionState",
    "CodexManagedSessionSummary",
    "CodexManagedSessionTurnResponse",
    "FetchCodexManagedSessionSummaryRequest",
    "InterruptCodexManagedSessionTurnRequest",
    "LaunchCodexManagedSessionRequest",
    "ManagedSessionContainerBackend",
    "ManagedSessionControlAction",
    "ManagedSessionControlMode",
    "ManagedSessionHandleStatus",
    "ManagedSessionProtocol",
    "ManagedSessionTurnStatus",
    "PublishCodexManagedSessionArtifactsRequest",
    "SendCodexManagedSessionTurnRequest",
    "SteerCodexManagedSessionTurnRequest",
    "TerminateCodexManagedSessionRequest",
]
