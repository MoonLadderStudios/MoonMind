"""Pydantic schemas for Jules API requests, responses, and monitoring helpers."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

JulesNormalizedStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "canceled",
    "unknown",
]

_JULES_STATUS_MAP: dict[str, JulesNormalizedStatus] = {
    "accepted": "queued",
    "assigned": "queued",
    "blocked": "running",
    "canceled": "canceled",
    "cancelled": "canceled",
    "completed": "succeeded",
    "created": "queued",
    "done": "succeeded",
    "errored": "failed",
    "failed": "failed",
    "failure": "failed",
    "finished": "succeeded",
    "in_progress": "running",
    "open": "queued",
    "pending": "queued",
    "queued": "queued",
    "resolved": "succeeded",
    "running": "running",
    "started": "running",
    "submitted": "queued",
    "success": "succeeded",
    "succeeded": "succeeded",
    "timed_out": "failed",
    "timeout": "failed",
}


def normalize_jules_status(raw_status: str | None) -> JulesNormalizedStatus:
    """Map raw Jules task status values to the provider-neutral status set."""

    normalized = str(raw_status or "").strip().lower()
    if not normalized:
        return "unknown"
    return _JULES_STATUS_MAP.get(normalized, "unknown")


class JulesCreateTaskRequest(BaseModel):
    """Request payload for creating a Jules task."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = Field(..., alias="title")
    description: str = Field(..., alias="description")
    metadata: Optional[dict[str, Any]] = Field(None, alias="metadata")


class JulesResolveTaskRequest(BaseModel):
    """Request payload for finishing a Jules task."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(..., alias="taskId")
    resolution_notes: str = Field(..., alias="resolutionNotes")
    status: str = Field("completed", alias="status")


class JulesGetTaskRequest(BaseModel):
    """Request payload for retrieving a Jules task."""

    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(..., alias="taskId")


class JulesTaskResponse(BaseModel):
    """Response payload for Jules task operations."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    task_id: str = Field(..., alias="taskId")
    status: str = Field(..., alias="status")
    url: Optional[str] = Field(None, alias="url")


class JulesIntegrationStartRequest(BaseModel):
    """Provider-neutral start contract for Jules-backed monitoring."""

    model_config = ConfigDict(populate_by_name=True)

    correlation_id: str = Field(..., alias="correlationId", min_length=1)
    idempotency_key: str = Field(..., alias="idempotencyKey", min_length=1)
    title: str = Field(..., alias="title", min_length=1)
    description: str = Field(..., alias="description", min_length=1)
    input_refs: list[str] = Field(default_factory=list, alias="inputRefs")
    parameters: dict[str, Any] = Field(default_factory=dict, alias="parameters")
    callback_url: Optional[str] = Field(None, alias="callbackUrl")
    callback_correlation_key: Optional[str] = Field(
        None, alias="callbackCorrelationKey"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class JulesIntegrationStartResult(BaseModel):
    """Normalized start result returned by `integration.jules.start`."""

    model_config = ConfigDict(populate_by_name=True)

    activity_name: Literal["integration.jules.start"] = Field(
        "integration.jules.start", alias="activityName"
    )
    task_queue: str = Field("mm.activity.integrations", alias="taskQueue")
    external_operation_id: str = Field(..., alias="externalOperationId")
    normalized_status: JulesNormalizedStatus = Field(..., alias="normalizedStatus")
    provider_status: str = Field(..., alias="providerStatus")
    callback_supported: bool = Field(..., alias="callbackSupported")
    callback_correlation_key: Optional[str] = Field(
        None, alias="callbackCorrelationKey"
    )
    recommended_poll_seconds: Optional[int] = Field(
        None, alias="recommendedPollSeconds", ge=1
    )
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )
    idempotency_key: str = Field(..., alias="idempotencyKey")
    ambiguous_timeout: bool = Field(False, alias="ambiguousTimeout")


class JulesIntegrationStatusResult(BaseModel):
    """Normalized status result returned by `integration.jules.status`."""

    model_config = ConfigDict(populate_by_name=True)

    activity_name: Literal["integration.jules.status"] = Field(
        "integration.jules.status", alias="activityName"
    )
    task_queue: str = Field("mm.activity.integrations", alias="taskQueue")
    external_operation_id: str = Field(..., alias="externalOperationId")
    normalized_status: JulesNormalizedStatus = Field(..., alias="normalizedStatus")
    provider_status: str = Field(..., alias="providerStatus")
    terminal: bool = Field(..., alias="terminal")
    recommended_poll_seconds: Optional[int] = Field(
        None, alias="recommendedPollSeconds", ge=1
    )
    external_url: Optional[str] = Field(None, alias="externalUrl")
    provider_summary: dict[str, Any] = Field(
        default_factory=dict, alias="providerSummary"
    )


class JulesIntegrationFetchResult(BaseModel):
    """Normalized result returned by `integration.jules.fetch_result`."""

    model_config = ConfigDict(populate_by_name=True)

    activity_name: Literal["integration.jules.fetch_result"] = Field(
        "integration.jules.fetch_result", alias="activityName"
    )
    task_queue: str = Field("mm.activity.integrations", alias="taskQueue")
    external_operation_id: str = Field(..., alias="externalOperationId")
    output_refs: list[str] = Field(default_factory=list, alias="outputRefs")
    summary: Optional[str] = Field(None, alias="summary")
    diagnostics_ref: Optional[str] = Field(None, alias="diagnosticsRef")
    provider_status: str = Field(..., alias="providerStatus")
    normalized_status: JulesNormalizedStatus = Field(..., alias="normalizedStatus")


class JulesIntegrationCancelResult(BaseModel):
    """Normalized result returned by `integration.jules.cancel`."""

    model_config = ConfigDict(populate_by_name=True)

    activity_name: Literal["integration.jules.cancel"] = Field(
        "integration.jules.cancel", alias="activityName"
    )
    task_queue: str = Field("mm.activity.integrations", alias="taskQueue")
    external_operation_id: str = Field(..., alias="externalOperationId")
    accepted: bool = Field(..., alias="accepted")
    unsupported: bool = Field(False, alias="unsupported")
    ambiguous: bool = Field(False, alias="ambiguous")
    final_provider_status: Optional[str] = Field(None, alias="finalProviderStatus")
    normalized_status: JulesNormalizedStatus = Field(..., alias="normalizedStatus")
    summary: Optional[str] = Field(None, alias="summary")


__all__ = [
    "JulesCreateTaskRequest",
    "JulesIntegrationCancelResult",
    "JulesIntegrationFetchResult",
    "JulesIntegrationStartRequest",
    "JulesIntegrationStartResult",
    "JulesIntegrationStatusResult",
    "JulesResolveTaskRequest",
    "JulesGetTaskRequest",
    "JulesTaskResponse",
    "JulesNormalizedStatus",
    "normalize_jules_status",
]
