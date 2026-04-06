"""Canonical contracts for managed session-plane state."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


def _require_non_blank(value: str, *, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


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
    cross_task_reuse: bool = False
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


class CodexManagedSessionState(BaseModel):
    """Identity and continuity state for one task-scoped Codex session."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(..., alias="sessionId", min_length=1)
    session_epoch: int = Field(..., alias="sessionEpoch", ge=1)
    container_id: str = Field(..., alias="containerId", min_length=1)
    thread_id: str = Field(..., alias="threadId", min_length=1)
    active_turn_id: str | None = Field(None, alias="activeTurnId")

    @model_validator(mode="after")
    def _normalize(self) -> "CodexManagedSessionState":
        self.session_id = _require_non_blank(self.session_id, field_name="sessionId")
        self.container_id = _require_non_blank(
            self.container_id, field_name="containerId"
        )
        self.thread_id = _require_non_blank(self.thread_id, field_name="threadId")
        active_turn_id = self.active_turn_id
        if active_turn_id is not None:
            self.active_turn_id = _require_non_blank(
                active_turn_id, field_name="activeTurnId"
            )
        return self

    def clear_session(self, *, new_thread_id: str) -> "CodexManagedSessionState":
        """Advance to a new session epoch inside the existing container."""

        normalized_thread_id = _require_non_blank(
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
