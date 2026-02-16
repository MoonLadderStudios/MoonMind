"""HTTP MCP tool wrapper endpoints for queue operations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.mcp.tool_registry import (
    QueueToolExecutionContext,
    QueueToolRegistry,
    ToolArgumentsValidationError,
    ToolCallRequest,
    ToolCallResponse,
    ToolListResponse,
    ToolNotFoundError,
)
from moonmind.workflows import get_agent_queue_repository
from moonmind.workflows.agent_queue.repositories import (
    AgentArtifactJobMismatchError,
    AgentArtifactNotFoundError,
    AgentJobNotFoundError,
    AgentJobOwnershipError,
    AgentJobStateError,
    AgentQueueRepository,
)
from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
)

router = APIRouter(prefix="/mcp", tags=["mcp-tools"])

_tool_registry = QueueToolRegistry()


async def _get_repository(
    session: AsyncSession = Depends(get_async_session),
) -> AgentQueueRepository:
    return get_agent_queue_repository(session)


async def _get_service(
    repository: AgentQueueRepository = Depends(_get_repository),
) -> AgentQueueService:
    return AgentQueueService(repository)


def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ToolNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tool_not_found", "message": str(exc)},
        )
    if isinstance(exc, ToolArgumentsValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_tool_arguments", "message": str(exc)},
        )
    if isinstance(exc, AgentJobNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "job_not_found", "message": str(exc)},
        )
    if isinstance(exc, AgentJobOwnershipError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "job_ownership_mismatch", "message": str(exc)},
        )
    if isinstance(exc, AgentJobStateError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "job_state_conflict", "message": str(exc)},
        )
    if isinstance(exc, AgentArtifactNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "artifact_not_found", "message": str(exc)},
        )
    if isinstance(exc, AgentArtifactJobMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "artifact_job_mismatch", "message": str(exc)},
        )
    if isinstance(exc, AgentQueueValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        code = "invalid_queue_payload"
        lowered = str(exc).lower()
        if "exceeds max bytes" in lowered:
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            code = "artifact_too_large"
        elif "does not exist on disk" in lowered:
            status_code = status.HTTP_404_NOT_FOUND
            code = "artifact_file_missing"
        return HTTPException(
            status_code=status_code,
            detail={"code": code, "message": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "mcp_tool_internal_error",
            "message": "An unexpected MCP tool error occurred.",
        },
    )


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(
    _user: User = Depends(get_current_user()),
) -> ToolListResponse:
    """Return queue MCP tool definitions."""

    return ToolListResponse(tools=_tool_registry.list_tools())


@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(
    payload: ToolCallRequest,
    service: AgentQueueService = Depends(_get_service),
    user: User = Depends(get_current_user()),
) -> ToolCallResponse:
    """Dispatch one queue MCP tool invocation."""

    try:
        context = QueueToolExecutionContext(
            service=service,
            user_id=getattr(user, "id", None),
        )
        result: Any = await _tool_registry.call_tool(
            tool=payload.tool,
            arguments=payload.arguments,
            context=context,
        )
    except Exception as exc:  # pragma: no cover - mapping layer
        raise _to_http_exception(exc) from exc
    return ToolCallResponse(result=result)
