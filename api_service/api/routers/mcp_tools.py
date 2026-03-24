"""HTTP MCP tool wrapper endpoints for queue and Jules operations."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.mcp.jules_tool_registry import (
    JulesToolExecutionContext,
    JulesToolRegistry,
)
from moonmind.mcp.tool_registry import (
    QueueToolExecutionContext,
    QueueToolRegistry,
    ToolArgumentsValidationError,
    ToolCallRequest,
    ToolCallResponse,
    ToolListResponse,
    ToolNotFoundError,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-tools"])

_queue_registry = QueueToolRegistry()
_jules_registry: JulesToolRegistry | None = None
_jules_client: JulesClient | None = None

if settings.jules.jules_enabled:
    _jules_api_url = settings.jules.jules_api_url
    _jules_api_key = settings.jules.jules_api_key
    if _jules_api_url and _jules_api_key:
        _jules_client = JulesClient(
            base_url=_jules_api_url,
            api_key=_jules_api_key,
            timeout=settings.jules.jules_timeout_seconds,
            retry_attempts=settings.jules.jules_retry_attempts,
            retry_delay_seconds=settings.jules.jules_retry_delay_seconds,
        )
        _jules_registry = JulesToolRegistry()
        logger.info("Jules MCP tools enabled")
    else:
        logger.warning(
            "JULES_ENABLED is true but JULES_API_URL or JULES_API_KEY is missing; "
            "Jules tools will not be registered"
        )


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
    if isinstance(exc, JulesClientError):
        if exc.status_code == 429:
            http_status = status.HTTP_429_TOO_MANY_REQUESTS
            code = "jules_rate_limited"
        elif exc.status_code is not None and 400 <= exc.status_code < 500:
            http_status = status.HTTP_422_UNPROCESSABLE_ENTITY
            code = "jules_request_failed"
        else:
            http_status = status.HTTP_502_BAD_GATEWAY
            code = "jules_request_failed"
        return HTTPException(
            status_code=http_status,
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
    """Return all registered MCP tool definitions."""
    tools = _queue_registry.list_tools()
    if _jules_registry is not None:
        tools = tools + _jules_registry.list_tools()
    return ToolListResponse(tools=tools)


@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(
    payload: ToolCallRequest,
    user: User = Depends(get_current_user()),
) -> ToolCallResponse:
    """Dispatch one MCP tool invocation."""

    try:
        if payload.tool.startswith("jules.") and _jules_registry is not None:
            if _jules_client is None:  # pragma: no cover
                raise ToolNotFoundError(payload.tool)
            jules_context = JulesToolExecutionContext(client=_jules_client)
            result: Any = await _jules_registry.call_tool(
                tool=payload.tool,
                arguments=payload.arguments,
                context=jules_context,
            )
        else:
            queue_context = QueueToolExecutionContext(
                service=None,
                user_id=getattr(user, "id", None),
            )
            result = await _queue_registry.call_tool(
                tool=payload.tool,
                arguments=payload.arguments,
                context=queue_context,
            )
    except Exception as exc:  # pragma: no cover - mapping layer
        raise _to_http_exception(exc) from exc
    return ToolCallResponse(result=result)
