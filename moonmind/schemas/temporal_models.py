"""Schemas for Temporal execution lifecycle APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CreateExecutionRequest(BaseModel):
    """Request payload for starting a workflow execution."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: Literal["MoonMind.Run", "MoonMind.ManifestIngest"] = Field(
        ..., alias="workflowType"
    )
    title: Optional[str] = Field(None, alias="title")
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    failure_policy: Optional[
        Literal["fail_fast", "continue_and_report", "best_effort"]
    ] = Field(None, alias="failurePolicy")
    initial_parameters: dict[str, Any] = Field(
        default_factory=dict, alias="initialParameters"
    )
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "CreateExecutionRequest":
        if (
            self.workflow_type == "MoonMind.ManifestIngest"
            and not self.manifest_artifact_ref
        ):
            raise ValueError(
                "manifestArtifactRef is required when workflowType is MoonMind.ManifestIngest"
            )
        return self


class UpdateExecutionRequest(BaseModel):
    """Request payload for workflow updates."""

    model_config = ConfigDict(populate_by_name=True)

    update_name: Literal["UpdateInputs", "SetTitle", "RequestRerun"] = Field(
        "UpdateInputs", alias="updateName"
    )
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    parameters_patch: Optional[dict[str, Any]] = Field(None, alias="parametersPatch")
    title: Optional[str] = Field(None, alias="title")
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")


class UpdateExecutionResponse(BaseModel):
    """Outcome from an update command."""

    model_config = ConfigDict(populate_by_name=True)

    accepted: bool = Field(..., alias="accepted")
    applied: Literal["immediate", "next_safe_point", "continue_as_new"] = Field(
        ..., alias="applied"
    )
    message: str = Field(..., alias="message")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")


class SignalExecutionRequest(BaseModel):
    """Request payload for asynchronous workflow signals."""

    model_config = ConfigDict(populate_by_name=True)

    signal_name: Literal["ExternalEvent", "Approve", "Pause", "Resume"] = Field(
        ..., alias="signalName"
    )
    payload: dict[str, Any] = Field(default_factory=dict, alias="payload")
    payload_artifact_ref: Optional[str] = Field(None, alias="payloadArtifactRef")


class CancelExecutionRequest(BaseModel):
    """Request payload for cancellation."""

    model_config = ConfigDict(populate_by_name=True)

    reason: Optional[str] = Field(None, alias="reason")
    graceful: bool = Field(True, alias="graceful")


class ExecutionModel(BaseModel):
    """Materialized execution view returned by lifecycle APIs."""

    model_config = ConfigDict(populate_by_name=True)

    source: Literal["temporal"] = Field("temporal", alias="source")
    task_id: str = Field(..., alias="taskId")
    namespace: str = Field(..., alias="namespace")
    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    temporal_run_id: str = Field(..., alias="temporalRunId")
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
        "succeeded",
        "failed",
        "cancelled",
    ] = Field(..., alias="status")
    dashboard_status: Literal[
        "queued",
        "running",
        "awaiting_action",
        "succeeded",
        "failed",
        "cancelled",
    ] = Field(..., alias="dashboardStatus")
    raw_state: str = Field(..., alias="rawState")
    state: str = Field(..., alias="state")
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
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
    artifacts_count: int = Field(0, alias="artifactsCount")
    created_at: datetime = Field(..., alias="createdAt")
    latest_run_view: bool = Field(True, alias="latestRunView")
    continue_as_new_cause: Optional[str] = Field(None, alias="continueAsNewCause")
    started_at: datetime = Field(..., alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    closed_at: datetime | None = Field(None, alias="closedAt")
    detail_href: str = Field(..., alias="detailHref")


class ExecutionListResponse(BaseModel):
    """Paginated list response for executions."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ExecutionModel] = Field(default_factory=list, alias="items")
    next_page_token: Optional[str] = Field(None, alias="nextPageToken")
    count: Optional[int] = Field(None, alias="count")
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
