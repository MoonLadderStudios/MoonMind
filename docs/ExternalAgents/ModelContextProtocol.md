# Model Context Protocol in MoonMind

MoonMind exposes one canonical Model Context Protocol transport at `/mcp`, plus small JSON helper endpoints for tool and resource discovery. The retired Gemini chat-style `/context` endpoint is not part of this contract.

## Surfaces

| Surface | Method and path | Purpose |
| --- | --- | --- |
| MCP Streamable HTTP | `POST /mcp` | JSON-RPC 2.0 endpoint for `initialize`, `ping`, `tools/list`, and `tools/call`. |
| MCP server stream probe | `GET /mcp` | Returns `405 Method Not Allowed`; MoonMind does not emit server-initiated SSE messages. |
| Resource discovery | `GET /mcp/resources` | Lists MCP-facing resources. |
| Tool discovery helper | `GET /mcp/tools` | Lists tool names, descriptions, and JSON Schemas. |
| Tool invocation helper | `POST /mcp/tools/call` | Invokes one immediate-call tool with a JSON `arguments` object. |

Implementation: [`api_service/api/routers/mcp_tools.py`](../../api_service/api/routers/mcp_tools.py).

## Authentication

All MCP routes use the same `get_current_user()` dependency as the rest of the API. When `AUTH_PROVIDER` is enabled, clients must send the configured bearer credential. When authentication is disabled, the API resolves the default database user so downstream calls retain a stable owner identity.

## Streamable HTTP

Clients send one JSON-RPC 2.0 message, or a 2025-03-26 batch, per HTTP POST. The request `Accept` header must allow `application/json`. MoonMind responds with JSON for requests and `202 Accepted` with an empty body for notification-only input.

MoonMind supports protocol versions `2025-03-26` and `2025-06-18` for its implemented lifecycle and tool methods:

- `initialize` negotiates the protocol and declares the `tools` capability.
- `notifications/initialized` is accepted after initialization.
- `ping` returns an empty result object.
- `tools/list` lists trusted immediately callable tools.
- `tools/call` invokes the same dispatch path as the JSON helper and returns MCP content plus `structuredContent`.

Workflow-submission-only executable tools can appear in the JSON discovery helper so the dashboard can author a workflow, but they are excluded from Streamable HTTP `tools/list` because they cannot execute as immediate calls.

Example initialization:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"example-client","version":"1.0.0"}}}
```

Example tool call:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"jira.get_issue","arguments":{"issueKey":"MM-777"}}}
```

## JSON helper endpoints

`GET /mcp/resources` advertises the `moonmind://mcp/tools` tool catalog resource. `GET /mcp/tools` returns registered tool metadata. Depending on deployment settings, the catalog can include container-job, Jira, Jules, skills-on-demand, remediation, and governed Temporal executable tools. The latter require workflow submission and return `execution_tool_requires_task_submission` if directly invoked.

`POST /mcp/tools/call` accepts a tool name and arguments:

```json
{"tool":"jira.get_issue","arguments":{"issueKey":"MM-777"}}
```

The `result` shape is tool-specific. Errors use HTTP status codes with a structured `detail` containing codes such as `tool_not_found`, `invalid_tool_arguments`, or provider-specific failures.

## Client configuration

Point clients at the MoonMind API base URL, for example `http://localhost:7000` from the host or `http://api:8000` from another Compose service. Configure authentication headers to match `AUTH_PROVIDER`.

The API container advertises the MCP endpoint using `MODEL_CONTEXT_PROTOCOL_ENABLED`, `MODEL_CONTEXT_PROTOCOL_PORT`, and `MODEL_CONTEXT_PROTOCOL_HOST` in the canonical Compose file.
