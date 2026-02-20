# Implementation Plan: Agent Queue MCP Tools Wrapper (Milestone 4)

**Branch**: `012-mcp-queue-tools-wrapper` | **Date**: 2026-02-13 | **Spec**: `specs/012-mcp-queue-tools-wrapper/spec.md`
**Input**: Feature specification from `/specs/012-mcp-queue-tools-wrapper/spec.md`

## Summary

Implement Milestone 4 from `docs/TaskQueueSystem.md` by adding an HTTP MCP tools wrapper (`GET /mcp/tools`, `POST /mcp/tools/call`) backed by a queue tool registry that calls the same `AgentQueueService` methods as REST queue routes, plus tests and a concise Codex integration guide.

## Technical Context

**Language/Version**: Python 3.11  
**Primary Dependencies**: FastAPI, Pydantic, existing agent queue service/repository stack, existing auth dependency utilities  
**Storage**: No new persistent store; reuse queue DB/artifact persistence via existing services  
**Testing**: pytest via `./tools/test_unit.sh`  
**Target Platform**: MoonMind API service runtime (Linux container/local dev shell)  
**Project Type**: Backend API router + internal registry + documentation  
**Performance Goals**: Tool discovery and call wrapper endpoints remain thin pass-through adapters with predictable serialization overhead  
**Constraints**: Must reuse queue service behavior, maintain auth parity, and avoid JSON-RPC MCP protocol expansion in this milestone  
**Scale/Scope**: Add one MCP router, one registry module, focused unit tests, and a small operator doc

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- `.specify/memory/constitution.md` contains unresolved placeholder content and no enforceable MUST/SHOULD directives.
- No additional constitution constraints can be objectively evaluated beyond AGENTS and repository instructions.

**Gate Status**: PASS WITH NOTE.

## Project Structure

### Documentation (this feature)

```text
specs/012-mcp-queue-tools-wrapper/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── requirements-traceability.md
│   └── mcp-tools-http-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
api_service/
├── api/routers/mcp_tools.py               # MCP-as-tools-over-HTTP endpoints
└── main.py                                # router registration

moonmind/
└── mcp/
    ├── __init__.py
    └── tool_registry.py                   # queue tool definitions + dispatch to AgentQueueService

tests/
└── unit/
    ├── api/routers/test_mcp_tools.py
    └── mcp/test_tool_registry.py

docs/
└── CodexMcpToolsAdapter.md                # concise Codex configuration guide
```

**Structure Decision**: Keep MCP wrapper concerns isolated in `moonmind/mcp` and a dedicated API router so queue business logic remains centralized in existing `AgentQueueService` methods.

## Phase 0: Research Plan

1. Define tool registry contract for tool metadata + argument validation + async dispatch.
2. Determine error mapping strategy from queue exceptions to MCP wrapper HTTP responses.
3. Determine response-shape strategy to preserve REST-equivalent queue model payloads.
4. Define minimal Codex integration guide format for HTTP tool adapter usage.

## Phase 1: Design Outputs

- `research.md`: records decisions and rejected alternatives.
- `data-model.md`: documents MCP wrapper entities and tool envelopes.
- `contracts/mcp-tools-http-contract.md`: endpoint shape for tools list/call.
- `contracts/requirements-traceability.md`: one row per `DOC-REQ-*` with implementation and validation strategy.
- `quickstart.md`: local verification flow for listing/calling tools.

## Post-Design Constitution Re-check

- Design includes runtime implementation and validation tasks.
- No enforceable constitution violations identified due placeholder-only constitution content.

**Gate Status**: PASS.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
