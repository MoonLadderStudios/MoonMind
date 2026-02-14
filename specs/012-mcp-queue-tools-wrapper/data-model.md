# Data Model: Agent Queue MCP Tools Wrapper (Milestone 4)

## Overview

Milestone 4 introduces MCP-wrapper transport models and tool registry metadata. Queue persistence models remain unchanged.

## Runtime Entities

### McpToolDefinition

Represents one registered tool exposed by `/mcp/tools`.

- `name` (str): Tool identifier (e.g., `queue.enqueue`).
- `description` (str): Human-readable purpose.
- `input_schema` (dict[str, Any]): JSON schema for arguments.

### McpToolCallRequest

Request envelope for executing a tool.

- `tool` (str): Registered tool name.
- `arguments` (dict[str, Any]): Tool-specific input payload.

### McpToolCallResponse

Response envelope for tool execution.

- `result` (Any): Tool-specific response payload (REST-equivalent shape).

### QueueToolExecutionContext

Runtime dependencies used by registry handlers.

- `service` (`AgentQueueService`): shared queue service instance.
- `user_id` (UUID | None): caller identity passed to create operations.

## Validation Rules

- Unknown tool names must be rejected with typed `tool_not_found` errors.
- Tool argument payloads must validate against per-tool Pydantic schemas.
- Queue service exceptions (not-found/conflict/validation) must map to explicit HTTP error codes.
- Artifact upload tool must reject invalid base64 content.

## Tool Result Shapes

- `queue.enqueue` -> `JobModel`
- `queue.claim` -> `ClaimJobResponse`
- `queue.heartbeat` -> `JobModel`
- `queue.complete` -> `JobModel`
- `queue.fail` -> `JobModel`
- `queue.get` -> `JobModel`
- `queue.list` -> `JobListResponse`
- `queue.upload_artifact` -> `ArtifactModel` (optional tool)
