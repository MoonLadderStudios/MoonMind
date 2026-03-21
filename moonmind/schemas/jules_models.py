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
    # -- Actual Jules API State enum values (case-insensitive) --
    "awaiting_plan_approval": "running",
    "awaiting_user_feedback": "running",
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
    "paused": "running",
    "pending": "queued",
    "planning": "running",
    "queued": "queued",
    "resolved": "succeeded",
    "running": "running",
    "started": "running",
    "state_unspecified": "unknown",
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


class GitHubRepoContext(BaseModel):
    """Context to use a GitHub repo in a Jules session.

    See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions#GitHubRepoContext
    """

    model_config = ConfigDict(populate_by_name=True)

    starting_branch: str = Field("main", alias="startingBranch")


class SourceContext(BaseModel):
    """Source context for a Jules session.

    See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions#SourceContext

    The ``source`` field must be a resource name in the format
    ``sources/github/{owner}/{repo}``.
    """

    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(..., alias="source")
    github_repo_context: GitHubRepoContext = Field(
        ..., alias="githubRepoContext"
    )

    @staticmethod
    def from_repo(
        owner_slash_repo: str,
        *,
        branch: str = "main",
    ) -> "SourceContext":
        """Build a ``SourceContext`` from an ``owner/repo`` string."""
        return SourceContext(
            source=f"sources/github/{owner_slash_repo}",
            github_repo_context=GitHubRepoContext(starting_branch=branch),
        )


class JulesCreateTaskRequest(BaseModel):
    """Request payload for creating a Jules session.

    Maps internal field names to Jules API wire format:
    ``prompt`` (required), ``title`` (optional), ``sourceContext`` (required),
    ``automationMode`` (optional).
    """

    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = Field(None, alias="title")
    description: str = Field(..., alias="prompt")
    metadata: Optional[dict[str, Any]] = Field(None, exclude=True)
    source_context: Optional[SourceContext] = Field(None, alias="sourceContext")
    automation_mode: Optional[str] = Field(None, alias="automationMode")


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


class JulesSendMessageRequest(BaseModel):
    """Request payload for sending a follow-up message to an existing Jules session.

    See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions/sendMessage
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(..., alias="sessionId")
    prompt: str = Field(..., alias="prompt", min_length=1)


class PullRequest(BaseModel):
    """A pull request created by a Jules session.

    See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions#PullRequest
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    url: Optional[str] = Field(None, alias="url")
    title: Optional[str] = Field(None, alias="title")
    description: Optional[str] = Field(None, alias="description")


class SessionOutput(BaseModel):
    """An output of a Jules session.

    See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions#SessionOutput
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    pull_request: Optional[PullRequest] = Field(None, alias="pullRequest")


class JulesTaskResponse(BaseModel):
    """Response payload for Jules session operations.

    Maps Jules API wire format (``id``, ``state``) to internal field
    names (``task_id``, ``status``) for backwards compatibility.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    task_id: str = Field(..., alias="id")
    status: Optional[str] = Field(None, alias="state")
    url: Optional[str] = Field(None, alias="url")
    name: Optional[str] = Field(None, alias="name")
    outputs: list[SessionOutput] = Field(default_factory=list, alias="outputs")

    @property
    def pull_request_url(self) -> Optional[str]:
        """Return the URL of the first pull request output, if any."""
        return next((
            output.pull_request.url
            for output in self.outputs
            if output.pull_request and output.pull_request.url
        ), None)


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


class JulesIntegrationMergePRResult(BaseModel):
    """Result returned by `integration.jules.merge_pr`."""

    model_config = ConfigDict(populate_by_name=True)

    activity_name: Literal["integration.jules.merge_pr"] = Field(
        "integration.jules.merge_pr", alias="activityName"
    )
    pr_url: str = Field(..., alias="prUrl")
    merged: bool = Field(..., alias="merged")
    merge_sha: Optional[str] = Field(None, alias="mergeSha")
    summary: str = Field(..., alias="summary")


__all__ = [
    "GitHubRepoContext",
    "JulesCreateTaskRequest",
    "JulesIntegrationCancelResult",
    "JulesIntegrationFetchResult",
    "JulesIntegrationMergePRResult",
    "JulesIntegrationStartRequest",
    "JulesIntegrationStartResult",
    "JulesIntegrationStatusResult",
    "JulesResolveTaskRequest",
    "JulesGetTaskRequest",
    "JulesSendMessageRequest",
    "JulesTaskResponse",
    "JulesNormalizedStatus",
    "SourceContext",
    "normalize_jules_status",
]
