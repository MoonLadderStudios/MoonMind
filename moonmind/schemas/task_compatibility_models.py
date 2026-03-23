"""Schemas for unified task compatibility APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskSource = Literal["queue", "temporal"]
TaskStatus = Literal[
    "queued",
    "running",
    "awaiting_action",
    "waiting",
    "succeeded",
    "failed",
    "cancelled",
]


class TaskActionAvailability(BaseModel):
    """Task-facing action availability metadata."""

    model_config = ConfigDict(populate_by_name=True)

    rename: bool = Field(False, alias="rename")
    edit_inputs: bool = Field(False, alias="editInputs")
    rerun: bool = Field(False, alias="rerun")
    approve: bool = Field(False, alias="approve")
    pause: bool = Field(False, alias="pause")
    resume: bool = Field(False, alias="resume")
    deliver_callback: bool = Field(False, alias="deliverCallback")
    cancel: bool = Field(False, alias="cancel")
    force_terminate: bool = Field(False, alias="forceTerminate")


class TaskDebugContext(BaseModel):
    """Optional raw lifecycle/debug context for one task."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str | None = Field(None, alias="namespace")
    workflow_type: str | None = Field(None, alias="workflowType")
    workflow_id: str | None = Field(None, alias="workflowId")
    temporal_run_id: str | None = Field(None, alias="temporalRunId")
    temporal_status: str | None = Field(None, alias="temporalStatus")
    close_status: str | None = Field(None, alias="closeStatus")
    waiting_reason: str | None = Field(None, alias="waitingReason")
    attention_required: bool = Field(False, alias="attentionRequired")


class TaskCompatibilityRow(BaseModel):
    """Normalized list row for one task-compatible execution."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(..., alias="taskId")
    source: TaskSource = Field(..., alias="source")
    entry: str | None = Field(None, alias="entry")
    title: str = Field(..., alias="title")
    summary: str | None = Field(None, alias="summary")
    status: TaskStatus = Field(..., alias="status")
    raw_state: str | None = Field(None, alias="rawState")
    temporal_status: str | None = Field(None, alias="temporalStatus")
    close_status: str | None = Field(None, alias="closeStatus")
    workflow_id: str | None = Field(None, alias="workflowId")
    workflow_type: str | None = Field(None, alias="workflowType")
    owner_type: str | None = Field(None, alias="ownerType")
    owner_id: str | None = Field(None, alias="ownerId")
    created_at: datetime = Field(..., alias="createdAt")
    started_at: datetime | None = Field(None, alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    closed_at: datetime | None = Field(None, alias="closedAt")
    artifacts_count: int = Field(0, alias="artifactsCount")
    detail_href: str = Field(..., alias="detailHref")
    queue_name: str | None = Field(None, alias="queueName")
    runtime_mode: str | None = Field(None, alias="runtimeMode")
    skill_id: str | None = Field(None, alias="skillId")
    publish_mode: str | None = Field(None, alias="publishMode")


class TaskCompatibilityDetail(TaskCompatibilityRow):
    """Expanded compatibility detail payload."""

    namespace: str | None = Field(None, alias="namespace")
    temporal_run_id: str | None = Field(None, alias="temporalRunId")
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    search_attributes: dict[str, Any] = Field(
        default_factory=dict, alias="searchAttributes"
    )
    memo: dict[str, Any] = Field(default_factory=dict, alias="memo")
    input_artifact_ref: str | None = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: str | None = Field(None, alias="planArtifactRef")
    manifest_artifact_ref: str | None = Field(None, alias="manifestArtifactRef")
    parameter_preview: dict[str, Any] = Field(
        default_factory=dict, alias="parameterPreview"
    )
    actions: TaskActionAvailability = Field(
        default_factory=TaskActionAvailability, alias="actions"
    )
    debug: TaskDebugContext | None = Field(None, alias="debug")


class TaskCompatibilityListResponse(BaseModel):
    """Paginated compatibility list response."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[TaskCompatibilityRow] = Field(default_factory=list, alias="items")
    next_cursor: str | None = Field(None, alias="nextCursor")
    count: int | None = Field(None, alias="count")
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
