"""MCP Streamable HTTP and helper tool endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

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
    ToolArgumentsValidationError,
    ToolCallRequest,
    ToolCallResponse,
    ToolListResponse,
    ToolNotFoundError,
)
from moonmind.workflows.adapters.jules_client import JulesClient, JulesClientError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp-tools"])
MCP_PROTOCOL_VERSION = "2025-03-26"
SUPPORTED_MCP_PROTOCOL_VERSIONS = {MCP_PROTOCOL_VERSION, "2025-06-18"}
MCP_SERVER_INFO = {"name": "moonmind", "version": "0.1.0"}

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


def _list_registered_tools() -> list[Any]:
    tools = _queue_registry.list_tools() + _execution_tool_registry.list_tools()
    if _jira_registry is not None:
        tools = tools + _jira_registry.list_tools()
    if _jules_registry is not None:
        tools = tools + _jules_registry.list_tools()
    return tools


def _list_streamable_callable_tools() -> list[Any]:
    tools = _queue_registry.list_tools()
    if _jira_registry is not None:
        tools = tools + _jira_registry.list_tools()
    if _jules_registry is not None:
        tools = tools + _jules_registry.list_tools()
    return tools


async def _dispatch_tool_call(payload: ToolCallRequest, user: User) -> Any:
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


def _json_rpc_result(request_id: str | int, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _json_rpc_error(
    request_id: str | int | None,
    *,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _validate_streamable_accept_header(request: Request) -> None:
    accept = request.headers.get("accept", "")
    accepted_types = [
        value.split(";", maxsplit=1)[0].strip()
        for value in accept.split(",")
        if value.strip()
    ]
    if accepted_types and not any(
        value in {"application/json", "*/*"} or value.endswith("/*")
        for value in accepted_types
    ):
        raise HTTPException(
            status_code=status.HTTP_406_NOT_ACCEPTABLE,
            detail={
                "code": "mcp_accept_header_required",
                "message": (
                    "MCP Streamable HTTP clients must send an Accept header "
                    "that allows application/json."
                ),
            },
        )


def _validate_protocol_version_header(request: Request) -> None:
    version = request.headers.get("MCP-Protocol-Version")
    if version and version not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "unsupported_mcp_protocol_version",
                "message": f"Unsupported MCP protocol version: {version}",
            },
        )


def _is_json_rpc_notification(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("jsonrpc") == "2.0"
        and "id" not in message
        and isinstance(message.get("method"), str)
    )


def _is_json_rpc_response(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and message.get("jsonrpc") == "2.0"
        and "method" not in message
        and "id" in message
        and ("result" in message or "error" in message)
    )


def _tool_result_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        text = result
        structured_content: Any = {"result": result}
    else:
        text = json.dumps(result, sort_keys=True, default=str)
        structured_content = result if isinstance(result, dict) else {"result": result}
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": False,
    }


def _tool_error_payload(detail: Any) -> dict[str, Any]:
    if isinstance(detail, dict):
        text = str(detail.get("message") or detail.get("code") or detail)
        structured_content = detail
    else:
        text = str(detail)
        structured_content = {"message": text}
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
        "isError": True,
    }


async def _handle_json_rpc_request(message: Any, user: User) -> dict[str, Any] | None:
    if _is_json_rpc_notification(message) or _is_json_rpc_response(message):
        return None
    if not isinstance(message, dict):
        return _json_rpc_error(
            None, code=-32600, message="Invalid JSON-RPC request payload."
        )
    request_id = message.get("id")
    method = message.get("method")
    if message.get("jsonrpc") != "2.0" or request_id is None or not isinstance(
        method, str
    ):
        return _json_rpc_error(
            request_id if isinstance(request_id, (str, int)) else None,
            code=-32600,
            message="Invalid JSON-RPC request.",
        )
    if not isinstance(request_id, (str, int)):
        return _json_rpc_error(
            None,
            code=-32600,
            message="JSON-RPC request id must be a string or integer.",
        )
    params = message.get("params") or {}
    if params is not None and not isinstance(params, dict):
        return _json_rpc_error(
            request_id, code=-32602, message="JSON-RPC params must be an object."
        )

    try:
        if method == "initialize":
            requested_version = str(params.get("protocolVersion") or "")
            protocol_version = (
                requested_version
                if requested_version in SUPPORTED_MCP_PROTOCOL_VERSIONS
                else MCP_PROTOCOL_VERSION
            )
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": MCP_SERVER_INFO,
                    "instructions": (
                        "MoonMind MCP exposes trusted tools through the "
                        "Streamable HTTP transport. Use tools/list and tools/call."
                    ),
                },
            )
        if method == "ping":
            return _json_rpc_result(request_id, {})
        if method == "tools/list":
            tools = [
                tool.model_dump(by_alias=True)
                for tool in _list_streamable_callable_tools()
            ]
            return _json_rpc_result(request_id, {"tools": tools})
        if method == "tools/call":
            tool_name = params.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                return _json_rpc_error(
                    request_id,
                    code=-32602,
                    message="tools/call requires params.name.",
                )
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _json_rpc_error(
                    request_id,
                    code=-32602,
                    message="tools/call params.arguments must be an object.",
                )
            result = await _dispatch_tool_call(
                ToolCallRequest(tool=tool_name, arguments=arguments),
                user,
            )
            return _json_rpc_result(request_id, _tool_result_payload(result))
    except HTTPException as exc:
        if method == "tools/call":
            return _json_rpc_result(request_id, _tool_error_payload(exc.detail))
        return _json_rpc_error(
            request_id,
            code=-32000,
            message="MCP tool request failed.",
            data=exc.detail,
        )
    except (ToolNotFoundError, ToolArgumentsValidationError) as exc:
        if method == "tools/call":
            return _json_rpc_result(request_id, _tool_error_payload(str(exc)))
        return _json_rpc_error(request_id, code=-32602, message=str(exc))
    except (JiraToolError, JulesClientError) as exc:
        mapped = _to_http_exception(exc)
        if method == "tools/call":
            return _json_rpc_result(request_id, _tool_error_payload(mapped.detail))
        return _json_rpc_error(
            request_id,
            code=-32000,
            message="MCP tool request failed.",
            data=mapped.detail,
        )
    except Exception:
        logger.exception("mcp_streamable_http_request_failed method=%s", method)
        return _json_rpc_error(
            request_id,
            code=-32603,
            message="An unexpected MCP request error occurred.",
        )

    return _json_rpc_error(
        request_id, code=-32601, message=f"Unsupported MCP method: {method}"
    )


@router.post("")
async def handle_streamable_http_post(
    request: Request,
    user: User = Depends(get_current_user()),
) -> Response:
    """Handle MCP Streamable HTTP JSON-RPC messages at the single MCP endpoint."""

    _validate_streamable_accept_header(request)
    _validate_protocol_version_header(request)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            _json_rpc_error(None, code=-32700, message="Parse error."),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(payload, list):
        if not payload:
            return JSONResponse(
                _json_rpc_error(None, code=-32600, message="Empty JSON-RPC batch."),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        has_batched_initialize = any(
            isinstance(message, dict) and message.get("method") == "initialize"
            for message in payload
        )
        if has_batched_initialize:
            return JSONResponse(
                _json_rpc_error(
                    None,
                    code=-32600,
                    message="MCP initialize requests must not be batched.",
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        responses = [
            response
            for response in [
                await _handle_json_rpc_request(message, user) for message in payload
            ]
            if response is not None
        ]
        if not responses:
            return Response(status_code=status.HTTP_202_ACCEPTED)
        return JSONResponse(responses)

    response = await _handle_json_rpc_request(payload, user)
    if response is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return JSONResponse(response)


@router.get("")
async def handle_streamable_http_get(request: Request) -> Response:
    """Return 405 because MoonMind does not emit server-initiated SSE messages."""

    accept = request.headers.get("accept", "")
    if "text/event-stream" not in accept:
        raise HTTPException(
            status_code=status.HTTP_406_NOT_ACCEPTABLE,
            detail={
                "code": "mcp_sse_accept_header_required",
                "message": "MCP Streamable HTTP GET requires text/event-stream.",
            },
        )
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)


@router.get("/tools", response_model=ToolListResponse)
async def list_tools(
    _user: User = Depends(get_current_user()),
) -> ToolListResponse:
    """Return all registered MCP tool definitions."""
    return ToolListResponse(tools=_list_registered_tools())

@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(
    payload: ToolCallRequest,
    user: User = Depends(get_current_user()),
) -> ToolCallResponse:
    """Dispatch one MCP tool invocation."""

    try:
        result = await _dispatch_tool_call(payload, user)
    except HTTPException:
        raise
    except JiraToolError as exc:
        raise _to_http_exception(exc) from None
    except Exception as exc:  # pragma: no cover - mapping layer
        raise _to_http_exception(exc) from exc
    return ToolCallResponse(result=result)
