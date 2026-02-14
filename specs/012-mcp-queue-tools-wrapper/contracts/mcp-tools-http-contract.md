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
    "payload": {"instruction": "Run tests"}
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
