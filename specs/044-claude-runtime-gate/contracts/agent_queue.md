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


