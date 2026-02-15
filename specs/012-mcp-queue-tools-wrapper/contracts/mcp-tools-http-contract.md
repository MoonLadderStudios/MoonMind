# MCP Tools HTTP Contract (Milestone 4)

## Endpoint: `GET /mcp/tools`

### Response Shape

```json
{
  "tools": [
    {
      "name": "queue.enqueue",
      "description": "Create a new queue job.",
      "inputSchema": {"type": "object", "properties": {}}
    }
  ]
}
```

## Endpoint: `POST /mcp/tools/call`

### Request Shape

```json
{
  "tool": "queue.enqueue",
  "arguments": {
    "type": "codex_exec",
    "priority": 10,
    "payload": {
      "repository": "MoonLadderStudios/MoonMind",
      "instruction": "Run tests",
      "codex": {
        "model": "gpt-5-codex",
        "effort": "high"
      }
    }
  }
}
```

### Response Shape

```json
{
  "result": {
    "id": "uuid",
    "type": "codex_exec",
    "status": "queued"
  }
}
```

### Per-task Codex override contract

For `codex_exec`/`codex_skill` payloads sent through MCP:

- `payload.codex.model` is an optional per-task model override.
- `payload.codex.effort` is an optional per-task effort override.
- Precedence is `payload.codex.*` -> worker defaults (`MOONMIND_CODEX_*`/`CODEX_*`) -> Codex CLI defaults.

## Error Envelope

Errors follow FastAPI `HTTPException` format:

```json
{
  "detail": {
    "code": "tool_not_found|invalid_tool_arguments|job_not_found|queue_internal_error",
    "message": "human-readable message"
  }
}
```
