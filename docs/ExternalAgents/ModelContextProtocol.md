# Model Context Protocol in MoonMind

MoonMind exposes one canonical Model Context Protocol transport at `/mcp`, plus small JSON helper endpoints for tool and resource discovery. The retired Gemini chat-style `/context` endpoint is not part of this contract.

## Surfaces

| Surface | Method and path | Purpose |
| --- | --- | --- |
| MCP Streamable HTTP | `POST /mcp` | JSON-RPC 2.0 endpoint for `initialize`, `ping`, `tools/list`, and `tools/call`. |
| MCP server stream probe | `GET /mcp` | Requires `Accept: text/event-stream`; returns `405 Method Not Allowed` when present or `406 Not Acceptable` when absent. MoonMind does not emit server-initiated SSE messages. |
| Resource discovery | `GET /mcp/resources` | Lists MCP-facing resources. |
| Tool discovery helper | `GET /mcp/tools` | Lists tool names, descriptions, and JSON Schemas. |
| Tool invocation helper | `POST /mcp/tools/call` | Invokes one immediate-call tool with a JSON `arguments` object. |

Implementation: [`api_service/api/routers/mcp_tools.py`](../../api_service/api/routers/mcp_tools.py).

## Authentication

All MCP routes use the same `get_current_user()` dependency as the rest of the API
(`api_service/api/routers/mcp_tools.py`). The one MoonMind selector `AUTH_PROVIDER`
(`accounts` | `oidc` | `header` | explicitly restricted local `disabled`) decides
how that dependency validates the caller. Retired selectors (`keycloak`, `default`,
`google`) fail at startup with migration guidance; they are never silently
translated. See [AuthenticationContracts.md](../Security/AuthenticationContracts.md)
for the authoritative mode, identity, session, and error contracts.

| Caller | Credential at the MCP boundary | Scope |
| --- | --- | --- |
| Browser / API user (`accounts`, `oidc`, `header`) | MoonMind session for the resolved `User.id` UUID (MoonMind cookie; bearer only where the route explicitly accepts it) | Only its authorized routes and resources; conflicting cookie/bearer identities are rejected (`401 auth_conflict`) |
| Local single-user (`disabled`) | Stable persisted user behind loopback-only bind or documented trusted ingress (`MOONMIND_TRUSTED_INGRESS=1`) | Same execution-linked authorization; never an error fallback |
| Machine (worker / container-job) | Container-job bearer (`MOONMIND_CONTAINER_JOBS_BEARER_TOKEN` family) or other scoped service credential on its authorized routes | Succeeds only on authorized routes and resources; runtime, session, and worker tokens never become unrestricted browser credentials. The removed legacy worker-token path is rejected (`410 worker_token_deprecated`). |

Missing credentials at strict boundaries are `401 auth_required`; invalid, expired,
wrong-key/issuer/purpose, or reserved-identity credentials are `401 auth_invalid`,
including on cache hits. Cookie-authenticated mutations require CSRF protection
and origin validation; WebSocket handshakes validate origin and are revoked on the
same bounded interval as other streams. SSE and artifact downloads work without
placing broad tokens in URLs.

Native Workflow Chat and upstream runtime access stay behind the qualified
same-origin binding facade: a valid MoonMind login does not grant unrestricted
access to every upstream session or host, service credentials stay server-side,
and browser cookies or bearer headers are not blindly forwarded upstream.

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
