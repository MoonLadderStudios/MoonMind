# Model Context Protocol in MoonMind

This document describes how MoonMind exposes **agent-facing HTTP surfaces** related to the Model Context Protocol (MCP) ecosystem: the 2025 **MCP Streamable HTTP** endpoint, a legacy **context-style chat completion** endpoint, and a small JSON helper API for discovery and invocation. Together they replace the older standalone `docs/CodexMcpToolsAdapter.md` guide; operational detail for Jules-specific behavior lives in [`docs/ExternalAgents/JulesAdapter.md`](JulesAdapter.md) (§ MCP tooling posture).

The canonical MCP transport is now the single `/mcp` endpoint. It accepts JSON-RPC 2.0 messages over HTTP POST using the 2025 Streamable HTTP transport shape. The older `/context` route remains a REST JSON chat-style completion contract and is not the MCP transport.

## Surfaces at a glance

| Surface | Method and path | Purpose |
| -------- | ---------------- | ------- |
| MCP Streamable HTTP | `POST /mcp` | JSON-RPC 2.0 MCP endpoint for `initialize`, `ping`, `tools/list`, and `tools/call`. |
| MCP server stream probe | `GET /mcp` | Returns `405 Method Not Allowed` because MoonMind does not currently emit server-initiated SSE messages. |
| Context completion | `POST /context` | Legacy chat-style generation with optional RAG; backed by **Google Gemini** in the current implementation. |
| JSON tool discovery helper | `GET /mcp/tools` | Lists tool names, descriptions, and JSON Schemas (`inputSchema`) for non-MCP helper clients. |
| JSON tool invocation helper | `POST /mcp/tools/call` | Dispatches one tool by name with a JSON `arguments` object for non-MCP helper clients. |

Implementation: [`api_service/api/routers/context_protocol.py`](../api_service/api/routers/context_protocol.py), [`api_service/api/routers/mcp_tools.py`](../api_service/api/routers/mcp_tools.py).

## Authentication

`/mcp`, `/context`, and `/mcp/*` use the same **`get_current_user()`** dependency as the rest of the API.

- When **`AUTH_PROVIDER`** is not `disabled`, clients must authenticate the same way as other protected routes (typically a **Bearer token** for JWT/OIDC flows). Your HTTP MCP client configuration must send that credential on every request.
- When **`AUTH_PROVIDER`** is `disabled`, the API still resolves a **default user** from the database (see [`api_service/auth_providers.py`](../api_service/auth_providers.py)) so downstream code has a stable user id unless tests or env overrides use a stub.

The example script under `examples/context_protocol_client.py` does not attach auth headers; use it only against a dev stack where that matches your auth mode, or extend it to send `Authorization: Bearer …`.

## `POST /mcp` — MCP Streamable HTTP

`POST /mcp` is the standards-oriented MCP endpoint. Clients send one JSON-RPC 2.0 message, or a 2025-03-26 JSON-RPC batch, per HTTP POST. The request `Accept` header must include both `application/json` and `text/event-stream`, matching the 2025 Streamable HTTP transport. MoonMind responds with JSON for request messages and returns `202 Accepted` with an empty body when the input is only notifications or client-side JSON-RPC responses.

MoonMind supports the `2025-03-26` MCP protocol version and accepts `2025-06-18` clients for the overlapping lifecycle/tool methods implemented here. If an `MCP-Protocol-Version` header is present, it must be one of those supported versions.

Supported methods:

- `initialize` — negotiates protocol version and declares the `tools` capability.
- `notifications/initialized` — accepted as a notification after initialization.
- `ping` — returns an empty result object.
- `tools/list` — returns the same trusted tool catalog exposed by the helper discovery route.
- `tools/call` — invokes the same trusted tool dispatch path as the helper invocation route, returning MCP tool content plus `structuredContent`.

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
  task-submission tools such as **`security.pentest.run`** so Mission Control can
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

The shape of `result` is tool-specific (for Jules tools, task payloads are JSON-serialized per the Jules client). Errors use HTTP status codes with a JSON `detail` object; common `code` values include `tool_not_found`, `invalid_tool_arguments`, `execution_tool_requires_task_submission`, and Jules-specific `jules_rate_limited` / `jules_request_failed` (see [`mcp_tools.py`](../api_service/api/routers/mcp_tools.py)).

### Configuring Codex (and similar clients)

Point your tool server at the **MoonMind API base** (for example `http://localhost:8000` from the host, or `http://api:8000` from another compose service). Use:

- **List tools**: `GET {base}/mcp/tools`
- **Call tool**: `POST {base}/mcp/tools/call` with `Content-Type: application/json`

Configure whatever fields your client uses for **authentication headers** so requests match your `AUTH_PROVIDER` setup. Exact config keys depend on the Codex (or other) release; this repository previously documented Codex-specific filenames in `CodexMcpToolsAdapter.md` — that file was removed in favor of this section plus upstream client docs.

## Docker labels and environment

The API container advertises the context endpoint for discovery. In `docker-compose.yaml` the `api` service sets:

```yaml
environment:
  - MODEL_CONTEXT_PROTOCOL_ENABLED=true
  - MODEL_CONTEXT_PROTOCOL_PORT=8000
  - MODEL_CONTEXT_PROTOCOL_HOST=0.0.0.0
labels:
  - "ai.model.context.protocol.version=0.1"
  - "ai.model.context.protocol.endpoint=/context"
```

Those variables describe the **context** surface; the MCP tool paths are always under **`/mcp`** on the same HTTP server.

## Example client (`examples/context_protocol_client.py`)

The repository includes a small Python script that `POST`s to `/context` with a default base URL of `http://localhost:8000` (override with `MOONMIND_API_URL`). As above, add auth headers if your environment requires them.

```bash
python examples/context_protocol_client.py
python examples/context_protocol_client.py gemini-pro
```

## OpenHands and other agents

From another container on the compose network, the context URL is typically:

```
http://api:8000/context
```

From the host:

```
http://localhost:8000/context
```

For tool use, the same host and port apply to `/mcp/tools` and `/mcp/tools/call`.

## Related documentation

- [`docs/ExternalAgents/JulesAdapter.md`](JulesAdapter.md) — Jules adapter, including MCP as an optional consumer surface.
- [`docs/MoonMindArchitecture.md`](../MoonMindArchitecture.md) — High-level mention of `/context` and agent integration.
