# Contract — POST /api/queue/jobs (Claude runtime gating)

## Request
```json
POST /api/queue/jobs
{
  "type": "task",
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "targetRuntime": "claude",
    "instructions": "string",
    "runtime": {
      "mode": "claude"
    }
  },
  "maxAttempts": 3,
  "priority": 0
}
```

## Success Response (200/201)
Conditions: Anthropic key present, payload valid.
```json
{
  "id": "<uuid>",
  "type": "task",
  "status": "queued",
  "payload": {
    "targetRuntime": "claude",
    ...
  },
  "system": null
}
```

## Error Response — Claude disabled
Conditions: `targetRuntime` resolves to `claude` but `RuntimeGateState.enabled` is false.
```json
Status: 400 Bad Request
{
  "detail": {
    "code": "claude_runtime_disabled",
    "message": "targetRuntime=claude requires ANTHROPIC_API_KEY to be configured"
  }
}
```

Notes:
- Queue service raises `AgentQueueValidationError` with the canonical message; the router maps that to the `claude_runtime_disabled` code.
- Validation runs before any DB writes, so no job artifacts are created when the gate blocks a request.
