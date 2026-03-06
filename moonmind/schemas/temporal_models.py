"""Schemas for Temporal execution lifecycle APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SUPPORTED_WORKFLOW_TYPES = ["MoonMind.Run", "MoonMind.ManifestIngest"]
SUPPORTED_FAILURE_POLICIES = [
    "fail_fast",
    "continue_and_report",
    "best_effort",
]
SUPPORTED_UPDATE_NAMES = ["UpdateInputs", "SetTitle", "RequestRerun"]
SUPPORTED_SIGNAL_NAMES = ["ExternalEvent", "Approve", "Pause", "Resume"]


class CreateExecutionRequest(BaseModel):
    """Request payload for starting a workflow execution."""

    model_config = ConfigDict(populate_by_name=True)

    workflow_type: str = Field(
        ...,
        alias="workflowType",
        json_schema_extra={"enum": SUPPORTED_WORKFLOW_TYPES},
    )
    title: Optional[str] = Field(None, alias="title")
    input_artifact_ref: Optional[str] = Field(None, alias="inputArtifactRef")
    plan_artifact_ref: Optional[str] = Field(None, alias="planArtifactRef")
    manifest_artifact_ref: Optional[str] = Field(None, alias="manifestArtifactRef")
    failure_policy: Optional[str] = Field(
        None,
        alias="failurePolicy",
        json_schema_extra={"enum": SUPPORTED_FAILURE_POLICIES},
    )
    initial_parameters: dict[str, Any] = Field(
        default_factory=dict, alias="initialParameters"
    )
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")


class UpdateExecutionRequest(BaseModel):
    """Request payload for workflow updates."""

    model_config = ConfigDict(populate_by_name=True)

    update_name: str = Field(
        "UpdateInputs",
        alias="updateName",
        json_schema_extra={"enum": SUPPORTED_UPDATE_NAMES},
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

    signal_name: str = Field(
        ...,
        alias="signalName",
        json_schema_extra={"enum": SUPPORTED_SIGNAL_NAMES},
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

    namespace: str = Field(..., alias="namespace")
    workflow_id: str = Field(..., alias="workflowId")
    run_id: str = Field(..., alias="runId")
    workflow_type: str = Field(..., alias="workflowType")
    state: str = Field(..., alias="state")
    temporal_status: Literal["running", "completed", "failed", "canceled"] = Field(
        ..., alias="temporalStatus"
    )
    close_status: Optional[str] = Field(None, alias="closeStatus")
    search_attributes: dict[str, Any] = Field(
        default_factory=dict, alias="searchAttributes"
    )
    memo: dict[str, Any] = Field(default_factory=dict, alias="memo")
    artifact_refs: list[str] = Field(default_factory=list, alias="artifactRefs")
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
