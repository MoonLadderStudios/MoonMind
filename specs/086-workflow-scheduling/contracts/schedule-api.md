# Schedule API Contract

**Feature**: 086-workflow-scheduling
**Date**: 2026-03-18

## Endpoint: POST /api/executions (extended)

### Request: Immediate (existing, unchanged)

```json
{
  "workflowType": "MoonMind.Run",
  "title": "...",
  "initialParameters": { ... }
}
```

### Request: Deferred One-Time

```json
{
  "workflowType": "MoonMind.Run",
  "title": "...",
  "initialParameters": { ... },
  "schedule": {
    "mode": "once",
    "scheduledFor": "2026-03-19T02:00:00Z"
  }
}
```

### Request: Recurring

```json
{
  "type": "task",
  "payload": {
    "task": { "instructions": "...", "runtime": { "mode": "gemini_cli" } },
    "repository": "..."
  },
  "schedule": {
    "mode": "recurring",
    "name": "Daily review",
    "cron": "0 9 * * 1-5",
    "timezone": "America/Los_Angeles"
  }
}
```

### Response: Deferred One-Time (201)

```json
{
  "workflowId": "mm:...",
  "runId": "...",
  "workflowType": "MoonMind.Run",
  "state": "scheduled",
  "scheduledFor": "2026-03-19T02:00:00Z",
  "title": "...",
  "startedAt": null,
  "redirectPath": "/tasks/mm:...?source=temporal"
}
```

### Response: Recurring (201)

```json
{
  "scheduled": true,
  "definitionId": "uuid",
  "name": "Daily review",
  "cron": "0 9 * * 1-5",
  "timezone": "America/Los_Angeles",
  "nextRunAt": "2026-03-19T16:00:00Z",
  "redirectPath": "/tasks/schedules/uuid"
}
```

### Error: Invalid scheduledFor (422)

```json
{
  "code": "invalid_execution_request",
  "message": "scheduledFor must be a future UTC timestamp"
}
```

### Error: Invalid cron (422)

```json
{
  "code": "invalid_execution_request",
  "message": "Invalid cron expression: ..."
}
```

## Schedule Parameters Schema

| Field | Type | Required | Mode | Description |
| --- | --- | --- | --- | --- |
| mode | `"once"` \| `"recurring"` | Yes | all | Scheduling mode |
| scheduledFor | `datetime` (ISO 8601) | Yes for once | once | Future UTC timestamp |
| name | `string` | No | recurring | Auto-derived from title if omitted |
| description | `string` | No | recurring | Optional |
| cron | `string` | Yes for recurring | recurring | 5-field POSIX cron |
| timezone | `string` | No (default UTC) | recurring | IANA timezone |
| enabled | `bool` | No (default true) | recurring | Whether schedule starts enabled |
| scopeType | `"personal"` \| `"global"` | No (default personal) | recurring | Authorization scope |
| policy | `object` | No | recurring | Overlap/catchup/jitter policies |
