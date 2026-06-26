"""Jules MCP tool registry and dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from pydantic import BaseModel, ValidationError

from moonmind.mcp.tool_registry import (
    ToolArgumentsValidationError,
    ToolMetadata,
    ToolNotFoundError,
    _ToolDefinition,
)
from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesResolveTaskRequest,
    JulesTaskResponse,
)
from moonmind.workflows.adapters.jules_client import JulesClient

@dataclass(frozen=True, slots=True)
class JulesToolExecutionContext:
    """Dependencies available to Jules tool handlers."""

    client: JulesClient

JulesToolHandler = Callable[[BaseModel, JulesToolExecutionContext], Awaitable[Any]]

class JulesToolRegistry:
    """Registry for Jules-related MCP tools."""

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
        context: JulesToolExecutionContext,
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
            "jules.create_task",
            "Create a new Jules task.",
            JulesCreateTaskRequest,
            self._handle_create_task,
        )
        self._register(
            "jules.resolve_task",
            "Resolve a Jules task.",
            JulesResolveTaskRequest,
            self._handle_resolve_task,
        )
        self._register(
            "jules.get_task",
            "Retrieve a Jules task by id.",
            JulesGetTaskRequest,
            self._handle_get_task,
        )

    def _register(
        self,
        name: str,
        description: str,
        argument_model: type[BaseModel],
        handler: JulesToolHandler,
    ) -> None:
        self._tools[name] = _ToolDefinition(
            name=name,
            description=description,
            argument_model=argument_model,
            handler=handler,
        )

    async def _handle_create_task(
        self,
        args: BaseModel,
        context: JulesToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, JulesCreateTaskRequest):
            raise ToolArgumentsValidationError(
                "jules.create_task",
                detail="Invalid payload type",
            )
        result: JulesTaskResponse = await context.client.create_task(args)
        return result.model_dump(by_alias=True, mode="json")

    async def _handle_resolve_task(
        self,
        args: BaseModel,
        context: JulesToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, JulesResolveTaskRequest):
            raise ToolArgumentsValidationError(
                "jules.resolve_task",
                detail="Invalid payload type",
            )
        result: JulesTaskResponse = await context.client.resolve_task(args)
        return result.model_dump(by_alias=True, mode="json")

    async def _handle_get_task(
        self,
        args: BaseModel,
        context: JulesToolExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(args, JulesGetTaskRequest):
            raise ToolArgumentsValidationError(
                "jules.get_task",
                detail="Invalid payload type",
            )
        result: JulesTaskResponse = await context.client.get_task(args)
        return result.model_dump(by_alias=True, mode="json")

__all__ = [
    "JulesToolExecutionContext",
    "JulesToolRegistry",
]
