# Quickstart: Agent Queue MCP Tools Wrapper (Milestone 4)

**Feature**: Agent Queue MCP Tools Wrapper  
**Branch**: `012-mcp-queue-tools-wrapper`

## Prerequisites

- MoonMind API is running with queue routes enabled.
- Queue DB/artifact migrations from Milestones 1-2 are applied.
- Valid API auth token (when auth is enabled).

## 1. List Available Tools

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/mcp/tools"
```

Verify queue tools are present (enqueue/claim/heartbeat/complete/fail/get/list).

## 2. Call `queue.enqueue`

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
    "tool": "queue.enqueue",
    "arguments": {
      "type": "codex_exec",
      "priority": 10,
      "payload": {"instruction": "Run lint"},
      "maxAttempts": 3
    }
  }'
```

Capture `result.id` as `JOB_ID`.

## 3. Call `queue.get`

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
    "tool": "queue.get",
    "arguments": {"jobId": "'$JOB_ID'"}
  }'
```

## 4. Call `queue.list`

```bash
curl -X POST "http://localhost:5000/mcp/tools/call" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{
    "tool": "queue.list",
    "arguments": {"status": "queued", "limit": 10}
  }'
```

## 5. Run Unit Tests

```bash
./tools/test_unit.sh
```
