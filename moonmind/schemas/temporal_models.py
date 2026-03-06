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
    disabled_reasons: dict[str, str] = Field(
        default_factory=dict, alias="disabledReasons"
    )


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


class ExecutionModel(BaseModel):
    """Materialized execution view returned by lifecycle APIs."""

    model_config = ConfigDict(populate_by_name=True)

    namespace: str = Field(..., alias="namespace")
    source: str = Field("temporal", alias="source")
    task_id: str = Field(..., alias="taskId")
    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    temporal_run_id: str = Field(..., alias="temporalRunId")
    legacy_run_id: Optional[str] = Field(None, alias="legacyRunId")
    workflow_type: str = Field(..., alias="workflowType")
    dashboard_status: str = Field(..., alias="dashboardStatus")
    state: str = Field(..., alias="state")
    raw_state: str = Field(..., alias="rawState")
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
    actions: ExecutionActionCapabilityModel = Field(
        default_factory=ExecutionActionCapabilityModel, alias="actions"
    )
    debug_fields: Optional[ExecutionDebugFieldsModel] = Field(None, alias="debugFields")
    redirect_path: Optional[str] = Field(None, alias="redirectPath")
    started_at: datetime = Field(..., alias="startedAt")
    updated_at: datetime = Field(..., alias="updatedAt")
    closed_at: datetime | None = Field(None, alias="closedAt")


class ExecutionListResponse(BaseModel):
    """Paginated list response for executions."""

    model_config = ConfigDict(populate_by_name=True)

    items: list[ExecutionModel] = Field(default_factory=list, alias="items")
    next_page_token: Optional[str] = Field(None, alias="nextPageToken")
    count: Optional[int] = Field(None, alias="count")
    count_mode: Literal["exact", "estimated_or_unknown"] = Field(
        "exact", alias="countMode"
    )
