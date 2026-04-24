"""MCP tool registry and dispatcher.

The queue-specific tool implementations have been removed as part of the
single substrate migration.  The base registry types are kept for consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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

@dataclass(frozen=True, slots=True)
class QueueToolExecutionContext:
    """Dependencies available to tool handlers.

    Stub retained for compatibility. The queue service has been removed.
    """

    service: Any
    user_id: UUID | None

ToolHandler = Callable[[BaseModel, Any], Awaitable[Any]]

@dataclass(frozen=True, slots=True)
class _ToolDefinition:
    """Internal registry metadata for one tool."""

    name: str
    description: str
    argument_model: type[BaseModel]
    handler: ToolHandler

class QueueToolRegistry:
    """Registry for MCP tools.

    The legacy queue tool implementations have been removed.
    This class is kept as a stub for compatibility with test fixtures
    and consumers that reference it.
    """

    def __init__(self) -> None:
        self._tools: dict[str, _ToolDefinition] = {}

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
        context: Any,
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

__all__ = [
    "QueueToolRegistry",
    "ToolArgumentsValidationError",
    "ToolCallRequest",
    "ToolCallResponse",
    "ToolListResponse",
    "ToolMetadata",
    "ToolNotFoundError",
]
