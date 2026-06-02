"""HTTP MCP discovery, tool-wrapper, and streamable transport endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.integrations.jira.errors import JiraToolError
from moonmind.integrations.jira.tool import JiraToolService
from moonmind.mcp.executable_tool_registry import ExecutableToolDiscoveryRegistry
from moonmind.mcp.jira_tool_registry import (
    JiraToolExecutionContext,
    JiraToolRegistry,
)
from moonmind.mcp.jules_tool_registry import (
    JulesToolExecutionContext,
    JulesToolRegistry,
)
from moonmind.mcp.tool_registry import (
    QueueToolExecutionContext,
    QueueToolRegistry,
    ResourceListResponse,
    ResourceMetadata,
    ToolArgumentsValidationError,
    ToolCallRequest,
    ToolCallResponse,
    ToolListResponse,
    ToolNotFoundError,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-tools"])
_MCP_PROTOCOL_VERSION = "2025-06-18"

_queue_registry = QueueToolRegistry()
_execution_tool_registry = ExecutableToolDiscoveryRegistry()
_jira_registry: JiraToolRegistry | None = None
_jira_service: JiraToolService | None = None
_jules_registry: JulesToolRegistry | None = None
_jules_client: JulesClient | None = None

if settings.atlassian.jira.jira_tool_enabled:
    _jira_service = JiraToolService(atlassian_settings=settings.atlassian)
    _jira_registry = JiraToolRegistry(
        enabled_actions=_jira_service.discoverable_actions()
    )

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

def _list_all_tools() -> list:
    tools = _queue_registry.list_tools() + _execution_tool_registry.list_tools()
    if _jira_registry is not None:
        tools = tools + _jira_registry.list_tools()
    if _jules_registry is not None:
        tools = tools + _jules_registry.list_tools()
    return tools

def _list_resources() -> list[ResourceMetadata]:
    return [
        ResourceMetadata(
            uri="moonmind://mcp/tools",
            name="MoonMind MCP tool catalog",
            description="Registered tool names, descriptions, and JSON Schemas.",
            mimeType="application/json",
        ),
        ResourceMetadata(
            uri="moonmind://integrations/callbacks",
            name="MoonMind integration callback API",
            description=(
                "Generic external-agent callback receiver contract and defaults."
            ),
            mimeType="application/json",
        ),
        ResourceMetadata(
            uri="moonmind://context",
            name="MoonMind context completion API",
            description="Context-style completion endpoint with optional RAG.",
            mimeType="application/json",
        ),
    ]

def _read_resource(uri: str) -> dict[str, Any]:
    if uri == "moonmind://mcp/tools":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(
                ToolListResponse(tools=_list_all_tools()).model_dump(by_alias=True),
                sort_keys=True,
            ),
        }
    if uri == "moonmind://integrations/callbacks":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(
                {
                    "endpointTemplate": (
                        "/api/integrations/{integrationName}/callbacks/"
                        "{callbackCorrelationKey}"
                    ),
                    "method": "POST",
                    "auth": [
                        "X-MoonMind-Integration-Token",
                        "Authorization: Bearer <token>",
                    ],
                    "payload": "IntegrationCallbackRequest",
                    "genericIntegrationNames": True,
                },
                sort_keys=True,
            ),
        }
    if uri == "moonmind://context":
        return {
            "uri": uri,
            "mimeType": "application/json",
            "text": json.dumps(
                {
                    "endpoint": "/context",
                    "method": "POST",
                    "contract": "ContextRequest",
                    "ragSupported": True,
                },
                sort_keys=True,
            ),
        }
    raise ToolNotFoundError(uri)

def _to_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, ToolNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "tool_not_found", "message": str(exc)},
        )
    if isinstance(exc, ToolArgumentsValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_tool_arguments", "message": str(exc)},
        )
    if isinstance(exc, JiraToolError):
        return HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        )
    if isinstance(exc, JulesClientError):
        if exc.status_code == 429:
            http_status = status.HTTP_429_TOO_MANY_REQUESTS
            code = "jules_rate_limited"
        elif exc.status_code is not None and 400 <= exc.status_code < 500:
            http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
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

async def _dispatch_tool(
    payload: ToolCallRequest,
    user: User,
) -> Any:
    if _execution_tool_registry.has_tool(payload.tool):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "execution_tool_requires_task_submission",
                "message": (
                    f"Tool '{payload.tool}' is a Temporal executable tool. "
                    "Submit it as a task or plan step instead of calling "
                    "/mcp/tools/call directly."
                ),
            },
        )
    if payload.tool.startswith("jira.") and _jira_registry is not None:
        if _jira_service is None:  # pragma: no cover
            raise ToolNotFoundError(payload.tool)
        jira_context = JiraToolExecutionContext(service=_jira_service)
        return await _jira_registry.call_tool(
            tool=payload.tool,
            arguments=payload.arguments,
            context=jira_context,
        )
    if payload.tool.startswith("jules.") and _jules_registry is not None:
        if _jules_client is None:  # pragma: no cover
            raise ToolNotFoundError(payload.tool)
        jules_context = JulesToolExecutionContext(client=_jules_client)
        return await _jules_registry.call_tool(
            tool=payload.tool,
            arguments=payload.arguments,
            context=jules_context,
        )
    queue_context = QueueToolExecutionContext(
        service=None,
        user_id=getattr(user, "id", None),
    )
    return await _queue_registry.call_tool(
        tool=payload.tool,
        arguments=payload.arguments,
        context=queue_context,
    )

@router.get("/tools", response_model=ToolListResponse)
async def list_tools(
    _user: User = Depends(get_current_user()),
) -> ToolListResponse:
    """Return all registered MCP tool definitions."""
    return ToolListResponse(tools=_list_all_tools())

@router.get("/resources", response_model=ResourceListResponse)
async def list_resources(
    _user: User = Depends(get_current_user()),
) -> ResourceListResponse:
    """Return MCP resource definitions exposed by MoonMind."""
    return ResourceListResponse(resources=_list_resources())

@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(
    payload: ToolCallRequest,
    user: User = Depends(get_current_user()),
) -> ToolCallResponse:
    """Dispatch one MCP tool invocation."""

    try:
        result = await _dispatch_tool(payload, user)
    except HTTPException:
        raise
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None
    except Exception as exc:  # pragma: no cover - mapping layer
        raise _to_http_exception(exc) from exc
    return ToolCallResponse(result=result)

def _jsonrpc_response(
    *,
    request_id: Any,
    result: Any | None = None,
    error: dict[str, Any] | None = None,
) -> JSONResponse:
    content: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        content["error"] = error
    else:
        content["result"] = result
    return JSONResponse(
        content=content,
        headers={"MCP-Protocol-Version": _MCP_PROTOCOL_VERSION},
    )

def _jsonrpc_error(
    *,
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return _jsonrpc_response(request_id=request_id, error=error)

@router.get("")
async def open_streamable_http_channel(
    _request: Request,
    _user: User = Depends(get_current_user()),
) -> StreamingResponse:
    """Open the GET side of the MCP Streamable HTTP transport."""
    return StreamingResponse(
        iter((b": moonmind mcp stream\n\n",)),
        media_type="text/event-stream",
        headers={"MCP-Protocol-Version": _MCP_PROTOCOL_VERSION},
    )

@router.post("")
async def streamable_http_rpc(
    request: Request,
    user: User = Depends(get_current_user()),
) -> Response:
    """Handle MCP Streamable HTTP JSON-RPC requests on the canonical endpoint."""
    payload = await request.json()
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _jsonrpc_error(
            request_id=payload.get("id") if isinstance(payload, dict) else None,
            code=-32600,
            message="Invalid JSON-RPC request.",
        )

    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    raw_params = payload.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}

    if request_id is None and method.startswith("notifications/"):
        return Response(
            status_code=status.HTTP_202_ACCEPTED,
            headers={"MCP-Protocol-Version": _MCP_PROTOCOL_VERSION},
        )

    try:
        if method == "initialize":
            result = {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                },
                "serverInfo": {"name": "moonmind", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = ToolListResponse(tools=_list_all_tools()).model_dump(
                by_alias=True
            )
        elif method == "tools/call":
            result_payload = await _dispatch_tool(
                ToolCallRequest(
                    tool=str(params.get("name") or ""),
                    arguments=dict(params.get("arguments") or {}),
                ),
                user,
            )
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result_payload, sort_keys=True),
                    }
                ],
                "structuredContent": result_payload,
                "isError": False,
            }
        elif method == "resources/list":
            result = ResourceListResponse(resources=_list_resources()).model_dump(
                by_alias=True
            )
        elif method == "resources/read":
            result = {"contents": [_read_resource(str(params.get("uri") or ""))]}
        else:
            return _jsonrpc_error(
                request_id=request_id,
                code=-32601,
                message=f"Method '{method}' is not supported.",
            )
    except HTTPException as exc:
        return _jsonrpc_error(
            request_id=request_id,
            code=-32000,
            message="MCP request failed.",
            data=exc.detail,
        )
    except JiraToolError as exc:
        return _jsonrpc_error(
            request_id=request_id,
            code=-32000,
            message=str(exc),
            data={"code": exc.code, "statusCode": exc.status_code},
        )
    except (ToolNotFoundError, ToolArgumentsValidationError) as exc:
        return _jsonrpc_error(
            request_id=request_id,
            code=-32602,
            message=str(exc),
        )
    except Exception as exc:  # pragma: no cover - defensive mapping
        logger.exception("Unexpected MCP JSON-RPC error")
        return _jsonrpc_error(
            request_id=request_id,
            code=-32603,
            message="Internal MCP error.",
            data={"error": str(exc)},
        )

    return _jsonrpc_response(request_id=request_id, result=result)
