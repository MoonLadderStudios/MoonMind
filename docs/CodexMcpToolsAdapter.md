# Codex -> MoonMind MCP Tools Adapter

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-16

This guide shows the HTTP tools surface for queue operations using the canonical Task job contract.

## Endpoints

- `GET /mcp/tools`
- `POST /mcp/tools/call`

## 1. List tool definitions

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/mcp/tools"
```

## 2. Call `queue.enqueue`

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
    "tool": "queue.enqueue",
    "arguments": {
      "type": "task",
      "priority": 10,
      "maxAttempts": 3,
      "payload": {
        "repository": "MoonLadderStudios/MoonMind",
        "requiredCapabilities": ["git", "codex"],
        "targetRuntime": "codex",
        "task": {
          "instructions": "Run tests",
          "skill": { "id": "auto", "args": {} },
          "runtime": { "mode": "codex", "model": "gpt-5-codex", "effort": "high" },
          "git": { "startingBranch": null, "newBranch": null },
          "publish": { "mode": "branch", "prBaseBranch": null, "commitMessage": null, "prTitle": null, "prBody": null }
        }
      }
    }
  }'
```

## 3. Call `queue.claim`

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
    "tool": "queue.claim",
    "arguments": {
      "workerId": "executor-01",
      "leaseSeconds": 120,
      "allowedTypes": ["task", "codex_exec", "codex_skill"],
      "workerCapabilities": ["codex", "git", "gh"]
    }
  }'
```

## Supported tool names

- `queue.enqueue`
- `queue.claim`
- `queue.heartbeat`
- `queue.complete`
- `queue.fail`
- `queue.get`
- `queue.list`
- `queue.upload_artifact` (optional)

## Notes

- Tool responses are wrapped as `{ "result": ... }` and mirror REST payloads.
- Use `GET /mcp/tools` as source of truth for argument schemas.
- Runtime overrides are passed as `payload.task.runtime.model` and `payload.task.runtime.effort`.
- `codex_exec` and `codex_skill` are legacy compatibility paths during migration.
