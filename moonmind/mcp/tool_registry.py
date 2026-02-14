"""Queue MCP tool registry and dispatcher."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from moonmind.schemas.agent_queue_models import (
    ArtifactModel,
    ClaimJobRequest,
    ClaimJobResponse,
    CreateJobRequest,
    JobListResponse,
    JobModel,
)
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import AgentJobNotFoundError
from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
)


class ToolRegistryError(RuntimeError):
    """Base class for MCP tool-registry failures."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool id is not registered."""

    def __init__(self, tool: str) -> None:
        super().__init__(f"Tool '{tool}' is not registered")
        self.tool = tool


class ToolArgumentsValidationError(ToolRegistryError):
    """Raised when tool arguments fail schema validation."""

    def __init__(self, tool: str, *, detail: str) -> None:
        super().__init__(f"Invalid arguments for '{tool}': {detail}")
        self.tool = tool
        self.detail = detail


class ToolCallRequest(BaseModel):
    """HTTP request envelope for MCP tool invocation."""

    model_config = ConfigDict(populate_by_name=True)

    tool: str = Field(..., alias="tool")
    arguments: dict[str, Any] = Field(default_factory=dict, alias="arguments")


class ToolCallResponse(BaseModel):
    """HTTP response envelope for MCP tool invocation."""

    model_config = ConfigDict(populate_by_name=True)

    result: Any = Field(..., alias="result")


class ToolMetadata(BaseModel):
    """Tool definition payload returned by discovery endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., alias="name")
    description: str = Field(..., alias="description")
    input_schema: dict[str, Any] = Field(..., alias="inputSchema")


class ToolListResponse(BaseModel):
    """Tool discovery response envelope."""

    model_config = ConfigDict(populate_by_name=True)

    tools: list[ToolMetadata] = Field(default_factory=list, alias="tools")


class QueueGetRequest(BaseModel):
    """Tool arguments for queue.get."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(..., alias="jobId")


class QueueHeartbeatRequest(BaseModel):
    """Tool arguments for queue.heartbeat."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(..., alias="jobId")
    worker_id: str = Field(..., alias="workerId")
    lease_seconds: int = Field(..., alias="leaseSeconds", ge=1)


class QueueCompleteRequest(BaseModel):
    """Tool arguments for queue.complete."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(..., alias="jobId")
    worker_id: str = Field(..., alias="workerId")
    result_summary: str | None = Field(None, alias="resultSummary")


class QueueFailRequest(BaseModel):
    """Tool arguments for queue.fail."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(..., alias="jobId")
    worker_id: str = Field(..., alias="workerId")
    error_message: str = Field(..., alias="errorMessage")
    retryable: bool = Field(False, alias="retryable")


class QueueListRequest(BaseModel):
    """Tool arguments for queue.list."""

    model_config = ConfigDict(populate_by_name=True)

    status_filter: models.AgentJobStatus | None = Field(None, alias="status")
    type_filter: str | None = Field(None, alias="type")
    limit: int = Field(50, alias="limit", ge=1, le=200)


class QueueUploadArtifactRequest(BaseModel):
    """Tool arguments for optional queue.upload_artifact."""

    model_config = ConfigDict(populate_by_name=True)

    job_id: UUID = Field(..., alias="jobId")
    name: str = Field(..., alias="name")
    content_base64: str = Field(..., alias="contentBase64")
    content_type: str | None = Field(None, alias="contentType")
    digest: str | None = Field(None, alias="digest")


@dataclass(frozen=True, slots=True)
class QueueToolExecutionContext:
    """Dependencies available to tool handlers."""

    service: AgentQueueService
    user_id: UUID | None


ToolHandler = Callable[[BaseModel, QueueToolExecutionContext], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class _ToolDefinition:
    """Internal registry metadata for one tool."""

    name: str
    description: str
    argument_model: type[BaseModel]
    handler: ToolHandler


class QueueToolRegistry:
    """Registry for queue-related MCP tools."""

    def __init__(self) -> None:
        self._tools: dict[str, _ToolDefinition] = {}
        self._register_default_tools()

    def list_tools(self) -> list[ToolMetadata]:
        """Return registered tools with schemas for discovery endpoint."""

        output: list[ToolMetadata] = []
        for definition in sorted(self._tools.values(), key=lambda item: item.name):
            output.append(
                ToolMetadata(
                    name=definition.name,
                    description=definition.description,
                    input_schema=definition.argument_model.model_json_schema(
                        by_alias=True
                    ),
                )
            )
        return output

    async def call_tool(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any] | None,
        context: QueueToolExecutionContext,
    ) -> Any:
        """Validate arguments and dispatch a tool call."""

        definition = self._tools.get(tool)
        if definition is None:
            raise ToolNotFoundError(tool)

        payload = dict(arguments or {})
        try:
            parsed_args = definition.argument_model.model_validate(payload)
        except ValidationError as exc:
            raise ToolArgumentsValidationError(tool, detail=str(exc)) from exc

        return await definition.handler(parsed_args, context)

    def _register_default_tools(self) -> None:
        self._register(
            "queue.enqueue",
            "Create a new queue job.",
            CreateJobRequest,
            self._handle_enqueue,
        )
        self._register(
            "queue.claim",
            "Claim the next eligible queue job.",
            ClaimJobRequest,
            self._handle_claim,
        )
        self._register(
            "queue.heartbeat",
            "Renew lease for a running queue job.",
            QueueHeartbeatRequest,
            self._handle_heartbeat,
        )
        self._register(
            "queue.complete",
            "Mark a running queue job as succeeded.",
            QueueCompleteRequest,
            self._handle_complete,
        )
        self._register(
            "queue.fail",
            "Mark a running queue job as failed.",
            QueueFailRequest,
            self._handle_fail,
        )
        self._register(
            "queue.get",
            "Fetch queue job details by id.",
            QueueGetRequest,
            self._handle_get,
        )
        self._register(
            "queue.list",
            "List queue jobs with optional filters.",
            QueueListRequest,
            self._handle_list,
        )
        self._register(
            "queue.upload_artifact",
            "Upload a queue artifact from base64 content.",
            QueueUploadArtifactRequest,
            self._handle_upload_artifact,
        )

    def _register(
        self,
        name: str,
        description: str,
        argument_model: type[BaseModel],
        handler: ToolHandler,
    ) -> None:
        self._tools[name] = _ToolDefinition(
            name=name,
            description=description,
            argument_model=argument_model,
            handler=handler,
        )

    async def _handle_enqueue(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, CreateJobRequest):
            raise ToolArgumentsValidationError(
                "queue.enqueue",
                detail="Invalid payload type",
            )
        payload = args
        job = await context.service.create_job(
            job_type=payload.type,
            payload=payload.payload,
            priority=payload.priority,
            created_by_user_id=context.user_id,
            requested_by_user_id=context.user_id,
            affinity_key=payload.affinity_key,
            max_attempts=payload.max_attempts,
        )
        return JobModel.model_validate(job).model_dump(by_alias=True, mode="json")

    async def _handle_claim(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, ClaimJobRequest):
            raise ToolArgumentsValidationError(
                "queue.claim",
                detail="Invalid payload type",
            )
        payload = args
        job = await context.service.claim_job(
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
            allowed_types=payload.allowed_types,
            worker_capabilities=payload.worker_capabilities,
        )
        response = ClaimJobResponse(
            job=JobModel.model_validate(job) if job is not None else None
        )
        return response.model_dump(by_alias=True, mode="json")

    async def _handle_heartbeat(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, QueueHeartbeatRequest):
            raise ToolArgumentsValidationError(
                "queue.heartbeat",
                detail="Invalid payload type",
            )
        payload = args
        job = await context.service.heartbeat(
            job_id=payload.job_id,
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
        )
        return JobModel.model_validate(job).model_dump(by_alias=True, mode="json")

    async def _handle_complete(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, QueueCompleteRequest):
            raise ToolArgumentsValidationError(
                "queue.complete",
                detail="Invalid payload type",
            )
        payload = args
        job = await context.service.complete_job(
            job_id=payload.job_id,
            worker_id=payload.worker_id,
            result_summary=payload.result_summary,
        )
        return JobModel.model_validate(job).model_dump(by_alias=True, mode="json")

    async def _handle_fail(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, QueueFailRequest):
            raise ToolArgumentsValidationError(
                "queue.fail",
                detail="Invalid payload type",
            )
        payload = args
        job = await context.service.fail_job(
            job_id=payload.job_id,
            worker_id=payload.worker_id,
            error_message=payload.error_message,
            retryable=payload.retryable,
        )
        return JobModel.model_validate(job).model_dump(by_alias=True, mode="json")

    async def _handle_get(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, QueueGetRequest):
            raise ToolArgumentsValidationError(
                "queue.get",
                detail="Invalid payload type",
            )
        payload = args
        job = await context.service.get_job(payload.job_id)
        if job is None:
            raise AgentJobNotFoundError(payload.job_id)
        return JobModel.model_validate(job).model_dump(by_alias=True, mode="json")

    async def _handle_list(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, QueueListRequest):
            raise ToolArgumentsValidationError(
                "queue.list",
                detail="Invalid payload type",
            )
        payload = args
        jobs = await context.service.list_jobs(
            status=payload.status_filter,
            job_type=payload.type_filter,
            limit=payload.limit,
        )
        response = JobListResponse(
            items=[JobModel.model_validate(item) for item in jobs]
        )
        return response.model_dump(by_alias=True, mode="json")

    async def _handle_upload_artifact(
        self,
        args: BaseModel,
        context: QueueToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, QueueUploadArtifactRequest):
            raise ToolArgumentsValidationError(
                "queue.upload_artifact",
                detail="Invalid payload type",
            )
        payload = args
        try:
            file_bytes = base64.b64decode(payload.content_base64, validate=True)
        except ValueError as exc:
            raise AgentQueueValidationError(
                "contentBase64 must be valid base64"
            ) from exc

        artifact = await context.service.upload_artifact(
            job_id=payload.job_id,
            name=payload.name,
            data=file_bytes,
            content_type=payload.content_type,
            digest=payload.digest,
        )
        return ArtifactModel.model_validate(artifact).model_dump(
            by_alias=True,
            mode="json",
        )


__all__ = [
    "QueueToolExecutionContext",
    "QueueToolRegistry",
    "ToolArgumentsValidationError",
    "ToolCallRequest",
    "ToolCallResponse",
    "ToolListResponse",
    "ToolMetadata",
    "ToolNotFoundError",
]
