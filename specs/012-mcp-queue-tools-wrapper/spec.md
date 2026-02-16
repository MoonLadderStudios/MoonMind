# Feature Specification: Agent Queue MCP Tools Wrapper (Milestone 4)

**Feature Branch**: `012-mcp-queue-tools-wrapper`  
**Created**: 2026-02-13  
**Status**: Draft  
**Input**: User description: "Implement Milestone 4 of docs/CodexTaskQueue.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agent Discovers Queue Tools over HTTP (Priority: P1)

As an external agent client, I can list available queue tools with schemas so I can safely construct tool calls.

**Why this priority**: Tool discovery is required before any queue operation can be invoked through the MCP wrapper.

**Independent Test**: Call `GET /mcp/tools` and verify queue tool definitions include names, descriptions, and JSON argument schemas.

**Acceptance Scenarios**:

1. **Given** an authenticated caller, **When** `GET /mcp/tools` is requested, **Then** response lists queue tool names and argument schemas.
2. **Given** tool definitions are registered in the server, **When** discovery endpoint is called, **Then** definitions remain stable and deterministic across calls.

---

### User Story 2 - Agent Calls Queue Tools with Shared Service Behavior (Priority: P1)

As an external agent client, I can invoke queue operations via MCP tool calls so behavior matches existing REST queue semantics.

**Why this priority**: Milestone 4 requires wrapping existing queue operations as tools without changing underlying behavior.

**Independent Test**: Call `POST /mcp/tools/call` for enqueue/claim/get/list/lifecycle tools and verify responses match REST models.

**Acceptance Scenarios**:

1. **Given** a valid tool call payload (`tool`, `arguments`), **When** `POST /mcp/tools/call` executes, **Then** queue service methods are invoked and result is returned in `{ "result": ... }`.
2. **Given** a queue tool returns a job payload, **When** MCP call completes, **Then** returned shape is equivalent to REST `JobModel`/envelope outputs.
3. **Given** an unknown tool name, **When** MCP call is submitted, **Then** API returns a typed tool-not-found error.

---

### User Story 3 - Team Configures Codex to Use MoonMind Tools (Priority: P2)

As an operator, I can follow a concise setup guide so Codex CLI can invoke MoonMind queue tools through HTTP adapter endpoints.

**Why this priority**: Milestone 4 explicitly requires guidance for configuring Codex to call MoonMind tools.

**Independent Test**: Follow the new doc instructions to list tools and issue one sample queue tool call.

**Acceptance Scenarios**:

1. **Given** MoonMind API is running, **When** operator follows the guide, **Then** they can successfully call `GET /mcp/tools` and `POST /mcp/tools/call`.
2. **Given** a sample enqueue call, **When** guide steps are executed, **Then** response contains queue job data in MCP wrapper format.

### Edge Cases

- Tool call payload omits required fields for a specific queue operation.
- Tool call contains invalid enum/filter values (status, job type, lifecycle payloads).
- Queue get tool requests unknown job id.
- Tool registry and route become inconsistent after code changes.
- Optional artifact upload tool receives invalid base64 payload.

## Requirements *(mandatory)*

### Source Document Requirements

- **DOC-REQ-001** (Source: `docs/CodexTaskQueue.md:483`, `docs/CodexTaskQueue.md:485`): Milestone 4 MUST add an MCP tool router and tool registry.
- **DOC-REQ-002** (Source: `docs/CodexTaskQueue.md:238`, `docs/CodexTaskQueue.md:246`): MCP tool surface MUST expose queue operations (`enqueue`, `claim`, `heartbeat`, `complete`, `fail`, `get`, `list`).
- **DOC-REQ-003** (Source: `docs/CodexTaskQueue.md:251`, `docs/CodexTaskQueue.md:254`): MCP and REST adapters MUST call the same queue service methods to keep behavior identical.
- **DOC-REQ-004** (Source: `docs/CodexTaskQueue.md:262`, `docs/CodexTaskQueue.md:267`): MVP MCP server shape MUST provide `GET /mcp/tools` and `POST /mcp/tools/call` HTTP endpoints.
- **DOC-REQ-005** (Source: `docs/CodexTaskQueue.md:266`): Tool discovery endpoint MUST return tool definitions with name, description, and JSON schema.
- **DOC-REQ-006** (Source: `docs/CodexTaskQueue.md:267`): Tool call endpoint MUST accept `{tool, arguments}` and return `{result}`.
- **DOC-REQ-007** (Source: `docs/CodexTaskQueue.md:287`, `docs/CodexTaskQueue.md:288`): Implementation MUST add `api_service/api/routers/mcp_tools.py` and `moonmind/mcp/tool_registry.py`.
- **DOC-REQ-008** (Source: `docs/CodexTaskQueue.md:292`, `docs/CodexTaskQueue.md:294`): Queue schemas MUST remain shared between REST and MCP surfaces.
- **DOC-REQ-009** (Source: `docs/CodexTaskQueue.md:531`): MCP adapter responses for queue operations MUST align with REST `JobModel`-equivalent structures.
- **DOC-REQ-010** (Source: `docs/CodexTaskQueue.md:487`): Deliverables MUST include a concise document describing how Codex can call MoonMind tools.

### Functional Requirements

- **FR-001** (`DOC-REQ-001`, `DOC-REQ-004`, `DOC-REQ-007`): The system MUST expose authenticated HTTP MCP wrapper endpoints at `GET /mcp/tools` and `POST /mcp/tools/call`.
- **FR-002** (`DOC-REQ-002`, `DOC-REQ-005`): The tool registry MUST publish queue tool definitions with explicit argument schemas and deterministic naming.
- **FR-003** (`DOC-REQ-003`, `DOC-REQ-008`): MCP tool handlers MUST invoke existing `AgentQueueService` methods and shared queue schemas rather than duplicating queue logic.
- **FR-004** (`DOC-REQ-006`, `DOC-REQ-009`): Tool call responses MUST return queue operation results in wrapper format while preserving REST-equivalent model fields.
- **FR-005** (`DOC-REQ-010`): Repository docs MUST include a concise operator guide for configuring Codex to call MoonMind tool endpoints.
- **FR-006**: Runtime deliverables MUST include production code changes and validation tests (docs-only outputs are insufficient).

### Key Entities *(include if feature involves data)*

- **McpToolDefinition**: Registered metadata describing a tool name, description, and JSON argument schema.
- **McpToolCallRequest**: Tool execution request envelope containing tool id and argument payload.
- **McpToolCallResponse**: Standardized response envelope containing tool execution result payload.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `GET /mcp/tools` returns queue tool definitions with non-empty description and schema fields.
- **SC-002**: `POST /mcp/tools/call` successfully invokes at least enqueue/claim/get/list queue operations and returns wrapper result payloads.
- **SC-003**: MCP queue tool outputs are schema-compatible with existing REST queue response models for equivalent operations.
- **SC-004**: Automated tests validate discovery endpoint behavior, tool call dispatch, and error handling for unknown tools/invalid arguments.
- **SC-005**: Milestone 4 unit tests pass through `./tools/test_unit.sh`.

## Assumptions

- Milestone 4 adopts the HTTP "MCP-as-tools-over-HTTP" approach from document Choice A.
- Existing queue REST implementation from Milestones 1-2 remains authoritative for queue business rules.
- Standard JSON-RPC MCP protocol compatibility (Choice B) is explicitly out-of-scope for this milestone.
