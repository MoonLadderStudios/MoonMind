# Research: Agent Queue MCP Tools Wrapper (Milestone 4)

## Decision 1: Implement Choice A HTTP tool wrapper (`/mcp/tools`, `/mcp/tools/call`)

- **Decision**: Implement MCP-as-tools-over-HTTP endpoints as specified in document section 5.3 Choice A.
- **Rationale**: This matches explicit milestone scope and delivers a usable tool surface without introducing JSON-RPC protocol complexity.
- **Alternatives considered**:
  - Full JSON-RPC MCP protocol (`initialize`, `tools/list`, `tools/call`): rejected as out-of-scope for milestone timeline.

## Decision 2: Use a dedicated queue tool registry abstraction

- **Decision**: Build `moonmind/mcp/tool_registry.py` containing tool metadata, argument validation, and service dispatch functions.
- **Rationale**: Keeps router thin and centralizes tool definitions/schemas/handler mapping in one place.
- **Alternatives considered**:
  - Encode dispatch map directly in API router: rejected due reduced testability and higher drift risk.

## Decision 3: Preserve REST-equivalent result payloads

- **Decision**: Serialize tool call results with existing queue schema models (`JobModel`, `ClaimJobResponse`, `JobListResponse`, etc.) and wrap in `{result: ...}`.
- **Rationale**: Satisfies requirement that MCP adapter responses align with REST queue outputs.
- **Alternatives considered**:
  - New MCP-only response schema objects: rejected because it duplicates existing queue model contracts.

## Decision 4: Reuse existing queue service methods for business behavior

- **Decision**: Tool handlers call `AgentQueueService` methods directly (same methods used by REST router).
- **Rationale**: Enforces identical validation/state-transition behavior across REST and MCP wrappers.
- **Alternatives considered**:
  - Separate MCP-only queue logic: rejected because it violates behavior-parity requirement.

## Decision 5: Include optional `queue.upload_artifact` tool using base64 payload

- **Decision**: Add optional artifact upload tool accepting `contentBase64` for JSON transport.
- **Rationale**: Document marks upload artifact as optional in tool surface and this enables machine-agent usage without multipart transport.
- **Alternatives considered**:
  - Exclude upload tool entirely: rejected to avoid partial parity with documented queue tool list.

## Decision 6: Add concise Codex adapter document in `docs/`

- **Decision**: Provide a focused guide with endpoint examples and `curl`-style payloads for tool listing/call.
- **Rationale**: Milestone requires practical setup guidance for Codex tool usage.
- **Alternatives considered**:
  - Embed setup instructions only in spec artifacts: rejected because milestone requires repository documentation deliverable.
