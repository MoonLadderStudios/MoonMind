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

Every MCP route except the `GET /mcp` probe resolves its caller through
`get_mcp_caller` (`api_service/api/routers/mcp_tools.py`). A caller is either
the admitted operator or a managed runtime presenting its own scoped
capability; neither path resolves or synthesizes a person. The `GET /mcp`
probe carries no authentication and only answers `406` or `405`. See
[AuthenticationContracts.md](../Security/AuthenticationContracts.md) for the
mode, session, and error contracts behind `AUTH_PROVIDER`.

| Caller | Credential at the MCP boundary | Scope |
| --- | --- | --- |
| Operator | `get_instance_operator_access`: loopback/approved-ingress admission in local `disabled` mode, or a MoonMind session in `accounts`, `oidc`, `header` | All MCP tools. A presented machine credential or marker fails closed (`401`) rather than becoming operator access. |
| Managed runtime (Jira Skills) | `X-MoonMind-Mcp-Tools: v1` plus `Authorization: Bearer` with the step's `MOONMIND_MCP_TOOLS_BEARER_TOKEN` (delivered as `..._FILE` on Omnigent hosts), minted at launch for steps whose Skill requires `jira` | Only `jira.*` tools, while the bound workflow is active and before expiry. Wrong purpose is `403 mcp_tool_capability_scope_mismatch`; invalid, expired, or foreign-audience tokens are `401 invalid_mcp_tool_capability`; a finished or unknown workflow is `401 stale_mcp_tool_capability`. No fallback to operator admission. |
| Container jobs | Container-job session capability on `POST /mcp/container/tools/call` only | `container.*` tools within the signed session, workspace, and owner scope. |

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

POST bodies require `Content-Type: application/json` (an optional charset is
accepted). Missing or browser-simple media types return `415` before tool
dispatch. The shared operator browser boundary rejects a foreign or opaque
`Origin`/`Referer` with `403`, including in local disabled-auth mode. CLI clients
without browser origin headers remain supported. For the default local disabled
deployment (no public base URL or approved trusted ingress), an origin-bearing
browser request must also use a loopback Host. Matching attacker-controlled Host
and Origin values cannot authorize a DNS-rebound browser request. Configured
public URLs and the documented audited-ingress path retain their existing
contracts; origin-less Compose clients can still address `http://api:8000`
but need their scoped capability, because reaching the API is not operator
admission.
CORS alone is not a mutation authorization boundary.

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
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"jira.get_issue","arguments":{"issueKey":"PROJ-123"}}}
```

## JSON helper endpoints

`GET /mcp/resources` advertises the `moonmind://mcp/tools` tool catalog resource. `GET /mcp/tools` returns registered tool metadata. Depending on deployment settings, the catalog can include container-job, Jira, skills-on-demand, remediation, and governed Temporal executable tools. The latter require workflow submission and return `execution_tool_requires_task_submission` if directly invoked.

`POST /mcp/tools/call` accepts a tool name and arguments:

```json
{"tool":"jira.get_issue","arguments":{"issueKey":"PROJ-123"}}
```

The `result` shape is tool-specific. Errors use HTTP status codes with a structured `detail` containing codes such as `tool_not_found`, `invalid_tool_arguments`, or provider-specific failures.

## Client configuration

Point clients at the MoonMind API base URL, for example `http://localhost:7000` from the host or `http://api:8000` from another Compose service. Operator clients use the admitted operator path for the deployment's `AUTH_PROVIDER`; managed runtimes send their scoped capability headers.

The API container advertises the MCP endpoint using `MODEL_CONTEXT_PROTOCOL_ENABLED`, `MODEL_CONTEXT_PROTOCOL_PORT`, and `MODEL_CONTEXT_PROTOCOL_HOST` in the canonical Compose file.
