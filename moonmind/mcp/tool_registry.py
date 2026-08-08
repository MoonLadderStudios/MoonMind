"""MCP tool registry and dispatcher.

The queue-specific tool implementations have been removed as part of the
single substrate migration.  The base registry types are kept for consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

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


class ResourceMetadata(BaseModel):
    """Resource definition payload returned by discovery endpoint."""

    model_config = ConfigDict(populate_by_name=True)

    uri: str = Field(..., alias="uri")
    name: str = Field(..., alias="name")
    description: str | None = Field(None, alias="description")
    mime_type: str | None = Field(None, alias="mimeType")


class ResourceListResponse(BaseModel):
    """Resource discovery response envelope."""

    model_config = ConfigDict(populate_by_name=True)

    resources: list[ResourceMetadata] = Field(default_factory=list, alias="resources")


ToolHandler = Callable[[BaseModel, Any], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class _ToolDefinition:
    """Internal metadata shared by the concrete MCP tool registries."""

    name: str
    description: str
    argument_model: type[BaseModel]
    handler: ToolHandler


__all__ = [
    "ResourceListResponse",
    "ResourceMetadata",
    "ToolArgumentsValidationError",
    "ToolCallRequest",
    "ToolCallResponse",
    "ToolListResponse",
    "ToolMetadata",
    "ToolNotFoundError",
]
