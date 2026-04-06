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
