# Requirements Traceability Matrix: Agent Queue MCP Tools Wrapper

**Feature**: `012-mcp-queue-tools-wrapper`  
**Source**: `docs/CodexTaskQueue.md`

| DOC-REQ ID | Mapped FR(s) | Planned Implementation Surface | Validation Strategy |
|------------|--------------|--------------------------------|--------------------|
| `DOC-REQ-001` | `FR-001`, `FR-002` | New MCP router + registry modules | Router and registry unit tests |
| `DOC-REQ-002` | `FR-002`, `FR-004` | Queue tool set registration (`enqueue`, `claim`, `heartbeat`, `complete`, `fail`, `get`, `list`) | `/mcp/tools` and dispatch tests validate tool names and results |
| `DOC-REQ-003` | `FR-003` | Registry handlers dispatch to `AgentQueueService` methods | Mocked service tests assert method-level dispatch parity |
| `DOC-REQ-004` | `FR-001` | `GET /mcp/tools` and `POST /mcp/tools/call` endpoints in router | API unit tests for endpoint shape and status codes |
| `DOC-REQ-005` | `FR-002` | Tool definition model includes `name`, `description`, `inputSchema` | Discovery endpoint tests validate schema content |
| `DOC-REQ-006` | `FR-004` | Tool call request/response envelopes (`tool`, `arguments`, `result`) | API tests validate wrapper contract |
| `DOC-REQ-007` | `FR-001` | Add files `api_service/api/routers/mcp_tools.py`, `moonmind/mcp/tool_registry.py` | Source-path and import coverage via unit tests |
| `DOC-REQ-008` | `FR-003` | Use existing queue schema models for serialization in registry handlers | Registry tests validate shared model serialization |
| `DOC-REQ-009` | `FR-004` | Return REST-equivalent queue payload shapes from tool results | Tool call tests compare expected model fields |
| `DOC-REQ-010` | `FR-005` | Add concise operator documentation for Codex HTTP adapter usage | Manual doc verification and quickstart flow |
