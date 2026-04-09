# Contract: Observability History Contract

## Goal

Define the Phase 3 task-run observability history contract without changing the existing route family.

## Route

`GET /api/task-runs/{id}/observability/events`

## Query Parameters

| Parameter | Type | Required | Meaning |
| --- | --- | --- | --- |
| `since` | integer | no | Return only rows with `sequence > since` |
| `limit` | integer | no | Maximum number of rows returned |
| `stream` | repeated string | no | Restrict rows to the requested canonical streams |
| `kind` | repeated string | no | Restrict rows to the requested canonical event kinds |

## Response

```json
{
  "events": [
    {
      "runId": "run-1",
      "sequence": 12,
      "timestamp": "2026-04-08T00:00:12Z",
      "stream": "session",
      "text": "Epoch boundary reached.",
      "kind": "session_reset_boundary",
      "sessionId": "sess:wf-task-1:codex_cli",
      "sessionEpoch": 2,
      "threadId": "thread-2"
    }
  ],
  "truncated": false,
  "sessionSnapshot": {
    "sessionId": "sess:wf-task-1:codex_cli",
    "sessionEpoch": 2,
    "containerId": "ctr-1",
    "threadId": "thread-2",
    "activeTurnId": null
  }
}
```

## Fallback Rules

Historical loading priority:

1. `observability.events.jsonl`
2. spool-backed event history
3. artifact-backed synthesis

`/logs/merged` remains outside this route and continues to serve as the human-readable fallback surface for consumers that cannot use structured history.
