"""Container-job MCP tool registry, schemas, and shared error normalization.

The five container-job operations are exposed identically over authenticated
HTTP and MCP (MoonLadderStudios/MoonMind#3259). Both transports call the same
``ContainerJobService`` and reuse the error classification defined here so that
invalid request, permission denied, job-not-found, unavailable evidence, and
backend-unavailable cases produce stable, machine-readable classifications.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from moonmind.schemas.container_job_models import (
    MAX_ARTIFACT_PAGE_ENTRIES,
    MAX_LOG_PAGE_ENTRIES,
    ContainerJobCancelRequest,
    ContainerJobLogQuery,
    ContainerJobSubmitRequest,
)
from moonmind.mcp.tool_registry import (
    ToolArgumentsValidationError,
    ToolMetadata,
    ToolNotFoundError,
)

CONTAINER_JOB_TOOL_NAMES = (
    "container.submit",
    "container.status",
    "container.logs",
    "container.artifacts",
    "container.cancel",
)


class ContainerJobToolError(RuntimeError):
    """Normalized error carrying a stable code and HTTP status for a transport."""

    def __init__(self, *, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def classify_container_job_error(exc: Exception) -> ContainerJobToolError:
    """Map a service/validation error to a stable transport-neutral classification."""

    # Imported lazily to avoid a hard import cycle with the API service layer.
    from api_service.services.container_jobs import (
        ContainerJobAuthorizationError,
        ContainerJobEvidenceUnavailableError,
        ContainerJobIdempotencyConflictError,
        ContainerJobNotFoundError,
    )

    if isinstance(exc, ContainerJobToolError):
        return exc
    if isinstance(exc, (ValidationError, ToolArgumentsValidationError, ValueError)):
        return ContainerJobToolError(
            code="invalid_request",
            message="Container-job request validation failed.",
            http_status=422,
        )
    if isinstance(exc, ContainerJobAuthorizationError):
        return ContainerJobToolError(
            code="permission_denied",
            message="Container-job authorization denied.",
            http_status=403,
        )
    if isinstance(exc, ContainerJobNotFoundError):
        return ContainerJobToolError(
            code="job_not_found",
            message="container job not found",
            http_status=404,
        )
    if isinstance(exc, ContainerJobIdempotencyConflictError):
        return ContainerJobToolError(
            code="idempotency_conflict", message=str(exc), http_status=409
        )
    if isinstance(exc, ContainerJobEvidenceUnavailableError):
        return ContainerJobToolError(
            code="evidence_unavailable", message=str(exc), http_status=503
        )
    return ContainerJobToolError(
        code="container_job_error",
        message="An unexpected container-job error occurred.",
        http_status=500,
    )


class _StatusArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_id: str = Field(alias="jobId", min_length=1, max_length=64)


class _LogArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_id: str = Field(alias="jobId", min_length=1, max_length=64)
    cursor: str | None = Field(None, max_length=512)
    limit: int = Field(100, ge=1, le=MAX_LOG_PAGE_ENTRIES)


class _ArtifactArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_id: str = Field(alias="jobId", min_length=1, max_length=64)
    cursor: str | None = Field(None, max_length=512)
    limit: int = Field(MAX_ARTIFACT_PAGE_ENTRIES, ge=1, le=MAX_ARTIFACT_PAGE_ENTRIES)


class _CancelArguments(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    job_id: str = Field(alias="jobId", min_length=1, max_length=64)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=255)
    reason: str | None = Field(None, max_length=512)


@dataclass(frozen=True, slots=True)
class ContainerJobToolContext:
    """Per-request dependencies shared by the HTTP and MCP transports."""

    service: Any
    owner: Any
    transport: str
    caller_request_id: str | None = None


_POLLING_GUIDANCE = (
    "Asynchronous: container.submit returns immediately with a stable jobId; "
    "poll container.status and container.logs until a terminal state "
    "(succeeded, failed, canceled, timed_out, rejected). Results are bounded; "
    "use the returned nextCursor to page logs and artifacts."
)


def _submit_input_schema() -> dict[str, Any]:
    schema = ContainerJobSubmitRequest.model_json_schema(by_alias=True)
    schema["x-moonmind-invocation"] = "async_container_job"
    return schema


class ContainerJobToolRegistry:
    """Discovery + dispatch for the five asynchronous container-job tools."""

    def list_tools(self) -> list[ToolMetadata]:
        return [
            ToolMetadata(
                name="container.submit",
                description=(
                    "Submit an asynchronous container job. Validates and authorizes "
                    "the request, creates or replays a durable job, starts the "
                    "Temporal workflow, and returns a stable jobId immediately "
                    "without holding the request open for execution. " + _POLLING_GUIDANCE
                ),
                input_schema=_submit_input_schema(),
            ),
            ToolMetadata(
                name="container.status",
                description=(
                    "Read a bounded canonical status snapshot for an owned "
                    "container job, including terminal and auxiliary diagnostics."
                ),
                input_schema={
                    "type": "object",
                    "required": ["jobId"],
                    "additionalProperties": False,
                    "properties": {
                        "jobId": {"type": "string", "title": "Job ID"},
                    },
                },
            ),
            ToolMetadata(
                name="container.logs",
                description=(
                    "Read one bounded, cursor-paginated log page for an owned "
                    "container job. Never returns an unbounded daemon stream."
                ),
                input_schema={
                    "type": "object",
                    "required": ["jobId"],
                    "additionalProperties": False,
                    "properties": {
                        "jobId": {"type": "string", "title": "Job ID"},
                        "cursor": {"type": "string", "title": "Cursor"},
                        "limit": {
                            "type": "integer",
                            "title": "Limit",
                            "minimum": 1,
                            "maximum": MAX_LOG_PAGE_ENTRIES,
                            "default": 100,
                        },
                    },
                },
            ),
            ToolMetadata(
                name="container.artifacts",
                description=(
                    "List authorized artifact references and publication "
                    "diagnostics for an owned container job. Bounded and paginated."
                ),
                input_schema={
                    "type": "object",
                    "required": ["jobId"],
                    "additionalProperties": False,
                    "properties": {
                        "jobId": {"type": "string", "title": "Job ID"},
                        "cursor": {"type": "string", "title": "Cursor"},
                        "limit": {
                            "type": "integer",
                            "title": "Limit",
                            "minimum": 1,
                            "maximum": MAX_ARTIFACT_PAGE_ENTRIES,
                            "default": MAX_ARTIFACT_PAGE_ENTRIES,
                        },
                    },
                },
            ),
            ToolMetadata(
                name="container.cancel",
                description=(
                    "Request idempotent cancellation of an owned container job. "
                    "Temporal stops the container and completes cleanup; repeated "
                    "or terminal cancellation is handled idempotently."
                ),
                input_schema={
                    "type": "object",
                    "required": ["jobId", "idempotencyKey"],
                    "additionalProperties": False,
                    "properties": {
                        "jobId": {"type": "string", "title": "Job ID"},
                        "idempotencyKey": {
                            "type": "string",
                            "title": "Idempotency key",
                        },
                        "reason": {"type": "string", "title": "Reason"},
                    },
                },
            ),
        ]

    def has_tool(self, name: str) -> bool:
        return name in CONTAINER_JOB_TOOL_NAMES

    async def call_tool(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any] | None,
        context: ContainerJobToolContext,
    ) -> dict[str, Any]:
        payload = dict(arguments or {})
        try:
            if tool == "container.submit":
                request = ContainerJobSubmitRequest.model_validate(payload)
                request = self._stamp_correlation(request, context)
                result = await context.service.submit(
                    owner=context.owner, request=request
                )
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if tool == "container.status":
                args = _StatusArguments.model_validate(payload)
                result = await context.service.status(
                    owner=context.owner, job_id=args.job_id
                )
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if tool == "container.logs":
                args = _LogArguments.model_validate(payload)
                result = await context.service.logs(
                    owner=context.owner,
                    job_id=args.job_id,
                    query=ContainerJobLogQuery(cursor=args.cursor, limit=args.limit),
                )
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if tool == "container.artifacts":
                args = _ArtifactArguments.model_validate(payload)
                result = await context.service.artifacts(
                    owner=context.owner,
                    job_id=args.job_id,
                    cursor=args.cursor,
                    limit=args.limit,
                )
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)
            if tool == "container.cancel":
                args = _CancelArguments.model_validate(payload)
                result = await context.service.cancel(
                    owner=context.owner,
                    job_id=args.job_id,
                    request=ContainerJobCancelRequest(
                        idempotencyKey=args.idempotency_key, reason=args.reason
                    ),
                )
                return result.model_dump(mode="json", by_alias=True, exclude_none=True)
        except ValidationError as exc:
            raise ToolArgumentsValidationError(
                tool, detail="Container-job request validation failed."
            ) from exc
        raise ToolNotFoundError(tool)

    @staticmethod
    def _stamp_correlation(
        request: ContainerJobSubmitRequest, context: ContainerJobToolContext
    ) -> ContainerJobSubmitRequest:
        """Fill the caller request id from transport correlation when omitted."""

        if request.source.caller_request_id or not context.caller_request_id:
            return request
        source = request.source.model_copy(
            update={"caller_request_id": context.caller_request_id}
        )
        return request.model_copy(update={"source": source})


__all__ = [
    "CONTAINER_JOB_TOOL_NAMES",
    "ContainerJobToolContext",
    "ContainerJobToolError",
    "ContainerJobToolRegistry",
    "classify_container_job_error",
]
