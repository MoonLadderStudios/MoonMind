# Codex -> MoonMind MCP Tools Adapter

This guide shows the Milestone 4 HTTP tool surface for queue operations.

## Endpoints

- `GET /mcp/tools`
- `POST /mcp/tools/call`

## 1) List Tool Definitions

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/mcp/tools"
```

## 2) Call a Tool (`queue.enqueue`)

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
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
      },
      "maxAttempts": 3
    }
  }'
```

## 3) Call `queue.claim`

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
    "tool": "queue.claim",
    "arguments": {
      "workerId": "executor-01",
      "leaseSeconds": 120,
      "allowedTypes": ["codex_exec"]
    }
  }'
```

## Supported Tool Names

- `queue.enqueue`
- `queue.claim`
- `queue.heartbeat`
- `queue.complete`
- `queue.fail`
- `queue.get`
- `queue.list`
- `queue.upload_artifact` (optional; accepts `contentBase64`)

## Notes

- Tool responses are wrapped as `{ "result": ... }` and preserve REST-equivalent queue payload fields.
- Use `GET /mcp/tools` as the source of truth for required argument schemas.
- Per-task Codex overrides are passed in `payload.codex.model` and `payload.codex.effort`; these override worker defaults for that task only.
