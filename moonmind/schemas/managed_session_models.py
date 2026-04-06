"""Canonical contracts for managed session-plane state."""

from __future__ import annotations

from typing import Literal

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
    protocol: Literal["codex_app_server"] = "codex_app_server"
    container_backend: Literal["docker"] = "docker"
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
        runtime_id = canonical_codex_managed_runtime_id(session_input.runtime_id)
        if runtime_id is None:
            raise ValueError("runtimeId must identify a managed Codex runtime")
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
