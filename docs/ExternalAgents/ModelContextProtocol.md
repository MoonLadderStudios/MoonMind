# Model Context Protocol in MoonMind

This document describes how MoonMind exposes **agent-facing HTTP surfaces** related to the Model Context Protocol (MCP) ecosystem: the 2025 **MCP Streamable HTTP** endpoint, a legacy **context-style chat completion** endpoint, and a small JSON helper API for resource discovery, tool discovery, and invocation. Together they replace the older standalone `docs/CodexMcpToolsAdapter.md` guide; operational detail for Jules-specific behavior lives in [`docs/ExternalAgents/JulesAdapter.md`](JulesAdapter.md) (§ MCP tooling posture).

The canonical MCP transport is now the single `/mcp` endpoint. It accepts JSON-RPC 2.0 messages over HTTP POST using the 2025 Streamable HTTP transport shape. The older `/context` route remains a REST JSON chat-style completion contract and is not the MCP transport.

## Surfaces at a glance

| Surface | Method and path | Purpose |
| -------- | ---------------- | ------- |
| MCP Streamable HTTP | `POST /mcp` | JSON-RPC 2.0 MCP endpoint for `initialize`, `ping`, `tools/list`, and `tools/call`. |
| MCP server stream probe | `GET /mcp` | Returns `405 Method Not Allowed` because MoonMind does not currently emit server-initiated SSE messages. |
| Context completion | `POST /context` | Legacy chat-style generation with optional RAG; backed by **Google Gemini** in the current implementation. |
| Resource discovery | `GET /mcp/resources` | Lists MoonMind MCP-facing resources with stable URIs, descriptions, and MIME types. |
| JSON tool discovery helper | `GET /mcp/tools` | Lists tool names, descriptions, and JSON Schemas (`inputSchema`) for non-MCP helper clients. |
| JSON tool invocation helper | `POST /mcp/tools/call` | Dispatches one tool by name with a JSON `arguments` object for non-MCP helper clients. |

Implementation: [`api_service/api/routers/context_protocol.py`](../api_service/api/routers/context_protocol.py), [`api_service/api/routers/mcp_tools.py`](../api_service/api/routers/mcp_tools.py).

## Authentication

`/mcp`, `/context`, and `/mcp/*` use the same **`get_current_user()`** dependency as the rest of the API.

- When **`AUTH_PROVIDER`** is not `disabled`, clients must authenticate the same way as other protected routes (typically a **Bearer token** for JWT/OIDC flows). Your HTTP MCP client configuration must send that credential on every request.
- When **`AUTH_PROVIDER`** is `disabled`, the API still resolves a **default user** from the database (see [`api_service/auth_providers.py`](../api_service/auth_providers.py)) so downstream code has a stable user id unless tests or env overrides use a stub.

The example script under `examples/context_protocol_client.py` does not attach auth headers; use it only against a dev stack where that matches your auth mode, or extend it to send `Authorization: Bearer …`.

## `POST /mcp` — MCP Streamable HTTP

`POST /mcp` is the standards-oriented MCP endpoint. Clients send one JSON-RPC 2.0 message, or a 2025-03-26 JSON-RPC batch, per HTTP POST. The request `Accept` header must allow `application/json`; standard POST clients may send only `application/json`, a wildcard, or no explicit `Accept` header. MoonMind responds with JSON for request messages and returns `202 Accepted` with an empty body when the input is only notifications or client-side JSON-RPC responses.

MoonMind supports the `2025-03-26` MCP protocol version and accepts `2025-06-18` clients for the overlapping lifecycle/tool methods implemented here. If an `MCP-Protocol-Version` header is present, it must be one of those supported versions.

Supported methods:

- `initialize` — negotiates protocol version and declares the `tools` capability.
- `notifications/initialized` — accepted as a notification after initialization.
- `ping` — returns an empty result object.
- `tools/list` — returns immediately callable trusted tools; workflow-submission-only Temporal executable tools are excluded from this transport list.
- `tools/call` — invokes the same trusted immediate-call dispatch path as the helper invocation route, returning MCP tool content plus `structuredContent`. Tool execution failures are returned as successful JSON-RPC responses with `isError: true` in the result payload.

MoonMind does not currently send server-initiated JSON-RPC messages, so `GET /mcp` returns `405 Method Not Allowed` for SSE stream attempts. This is allowed by the Streamable HTTP transport when a server does not offer an SSE stream.

Example initialization:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {
      "name": "example-client",
      "version": "1.0.0"
    }
  }
}
```

Example tool call:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "jira.get_issue",
    "arguments": {
      "issueKey": "MM-777"
    }
  }
}
```

## `POST /context` — legacy context-style completion

This endpoint accepts a JSON body shaped for multi-turn chat and returns a single assistant string. It is **not** vendor-agnostic today: the router calls **`get_google_model(request.model)`** and uses the **Gemini** generate API. The `model` field must be a model id your MoonMind Google factory supports (for example values used elsewhere in the project such as `gemini-pro`).

Optional **RAG**: when RAG is enabled and a vector index is available, the **last user message** in `messages` is used as the retrieval query; retrieved chunks are injected into that turn. Response `metadata` includes estimated `usage` token counts, plus `rag_enabled` and `rag_context_used` flags.

### Request body

```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful AI assistant."
    },
    {
      "role": "user",
      "content": "Summarize the retrieved context and answer the question."
    }
  ],
  "model": "gemini-pro",
  "temperature": 0.7,
  "max_tokens": 1000,
  "stream": false,
  "metadata": {
    "source": "example_client"
  }
}
```

### Request fields

- **`messages`**: Array of `{ "role", "content" [, "name"] }`. Roles must be `system`, `user`, or `assistant` (invalid roles return 400).
- **`model`**: Gemini model identifier passed through to the Google stack.
- **`max_tokens`**: Mapped to Gemini `max_output_tokens` when set.
- **`temperature`**: Generation temperature.
- **`stream`**: Accepted on the model; **streaming is not implemented** in this handler.
- **`metadata`**: Opaque passthrough metadata on the request model (not automatically echoed back except via your own conventions).

### Response body

```json
{
  "id": "ctx-…",
  "content": "…",
  "model": "gemini-pro",
  "created_at": 1621234567,
  "metadata": {
    "usage": {
      "prompt_tokens": 42,
      "completion_tokens": 128,
      "total_tokens": 170
    },
    "rag_enabled": true,
    "rag_context_used": true
  }
}
```

Token counts in `usage` are **estimates** (word-split based) for observability, not billing-grade provider totals.

## `GET /mcp/resources` — HTTP MCP resources

This route provides a minimal resource catalog so clients can discover MoonMind's MCP-facing resources before deciding which surface to use.

Returns:

```json
{
  "resources": [
    {
      "uri": "moonmind://context",
      "name": "context-completion",
      "description": "Chat-style context completion endpoint with optional RAG, available through POST /context.",
      "mimeType": "application/json"
    },
    {
      "uri": "moonmind://mcp/tools",
      "name": "tool-catalog",
      "description": "Registered MoonMind tool catalog, available through GET /mcp/tools.",
      "mimeType": "application/json"
    }
  ]
}
```

Field names match the Pydantic models in [`moonmind/mcp/tool_registry.py`](../moonmind/mcp/tool_registry.py) (`uri`, `name`, `description`, `mimeType`).

## `GET /mcp/tools` and `POST /mcp/tools/call` — HTTP MCP tools

These routes provide a minimal **list-tools / call-tool** pair over HTTPS. They are suitable for agents and CLIs that can target a **base URL** plus paths, with JSON request and response bodies.

### Discovery: `GET /mcp/tools`

Returns:

```json
{
  "tools": [
    {
      "name": "jules.create_task",
      "description": "Create a new Jules task.",
      "inputSchema": { }
    }
  ]
}
```

Field names match the Pydantic models in [`moonmind/mcp/tool_registry.py`](../moonmind/mcp/tool_registry.py) (`name`, `description`, `inputSchema`).

**What is registered today**

- **Temporal executable tools** — The discovery response includes governed
  workflow-submission tools such as **`security.pentest.run`** so the dashboard can
  offer them in the Create page Tool picker. These tools execute through the
  Temporal task/plan path, not as immediate `/mcp/tools/call` RPCs; direct calls
  return `execution_tool_requires_task_submission`.
- **Jules** — When `JULES_ENABLED` is true and both `JULES_API_URL` and `JULES_API_KEY` are set, the server registers **`jules.create_task`**, **`jules.resolve_task`**, and **`jules.get_task`** (see [`moonmind/mcp/jules_tool_registry.py`](../moonmind/mcp/jules_tool_registry.py)).
- **Legacy queue tools** — The queue-backed MCP tools were removed as part of the **single-substrate (Temporal)** migration. [`QueueToolRegistry`](../moonmind/mcp/tool_registry.py) remains as a compatibility stub and does not register queue tools in production. Do not rely on `queue.*` tool names from older docs or specs.

### Invocation: `POST /mcp/tools/call`

Body:

```json
{
  "tool": "jules.get_task",
  "arguments": {
    "taskId": "…"
  }
}
```

Successful response:

```json
{
  "result": { }
}
```

The shape of `result` is tool-specific (for Jules tools, workflow execution payloads are JSON-serialized per the Jules client). Errors use HTTP status codes with a JSON `detail` object; common `code` values include `tool_not_found`, `invalid_tool_arguments`, `execution_tool_requires_task_submission`, and Jules-specific `jules_rate_limited` / `jules_request_failed` (see [`mcp_tools.py`](../api_service/api/routers/mcp_tools.py)).

### Configuring Codex (and similar clients)

Point your tool server at the **MoonMind API base** (for example `http://localhost:7000` from the host, or `http://api:8000` from another compose service). Use:

- **List tools**: `GET {base}/mcp/tools`
- **List resources**: `GET {base}/mcp/resources`
- **Call tool**: `POST {base}/mcp/tools/call` with `Content-Type: application/json`

Configure whatever fields your client uses for **authentication headers** so requests match your `AUTH_PROVIDER` setup. Exact config keys depend on the Codex (or other) release; this repository previously documented Codex-specific filenames in `CodexMcpToolsAdapter.md` — that file was removed in favor of this section plus upstream client docs.

## Docker labels and environment

The API container advertises the MCP Streamable HTTP endpoint for discovery. In `docker-compose.yaml` the `api` service sets:

```yaml
environment:
  - MODEL_CONTEXT_PROTOCOL_ENABLED=true
  - MODEL_CONTEXT_PROTOCOL_PORT=8000
  - MODEL_CONTEXT_PROTOCOL_HOST=0.0.0.0
labels:
  - "ai.model.context.protocol.version=2025-03-26"
  - "ai.model.context.protocol.endpoint=/mcp"
```

Those labels describe the MCP transport surface. The legacy `/context` route remains available on the same HTTP server for REST-style context completion.

## Example client (`examples/context_protocol_client.py`)

The repository includes a small Python script that `POST`s to `/context` with a default base URL of `http://localhost:7000` (override with `MOONMIND_API_URL`). As above, add auth headers if your environment requires them.

```bash
python examples/context_protocol_client.py
python examples/context_protocol_client.py gemini-pro
```

## From Other Agents

From another container on the compose network, the context URL is typically:

```
http://api:8000/context
```

From the host:

```
http://localhost:7000/context
```

For tool use, the same host and port apply to `/mcp/tools` and `/mcp/tools/call`.

## Related documentation

- [`docs/ExternalAgents/JulesAdapter.md`](JulesAdapter.md) — Jules adapter, including MCP as an optional consumer surface.
- [`docs/MoonMindArchitecture.md`](../MoonMindArchitecture.md) — High-level mention of `/context` and agent integration.
