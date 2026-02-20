# API Contract: System Worker Pause Controls

## 1. `GET /api/system/worker-pause`

**Auth**: Operator (same scopes as queue dashboard).  
**Response 200**:
```json
{
  "paused": false,
  "mode": null,
  "reason": null,
  "version": 3,
  "requestedByUserId": "0f1e...",
  "requestedAt": "2026-02-20T02:13:00Z",
  "updatedAt": "2026-02-20T02:13:00Z",
  "metrics": {
    "queued": 48,
    "running": 0,
    "staleRunning": 0,
    "isDrained": true
  },
  "audit": {
    "latest": [
      {
        "id": "9771...",
        "action": "resume",
        "mode": null,
        "reason": "Deployment complete",
        "actorUserId": "0f1e...",
        "createdAt": "2026-02-20T02:25:00Z"
      },
      {
        "id": "beef...",
        "action": "pause",
        "mode": "drain",
        "reason": "Upgrading images",
        "actorUserId": "0f1e...",
        "createdAt": "2026-02-20T02:00:00Z"
      }
    ]
  }
}
```

- `metrics` section fuels the drain progress UI.
- `audit.latest` returns the most recent N events (default 5) for dashboard context; full audit remains in DB.

## 2. `POST /api/system/worker-pause`

**Body**:
```json
{
  "action": "pause",           // "pause" | "resume"
  "mode": "drain",             // required when action=pause, ignored on resume
  "reason": "Upgrading images" // required for both actions (audit compliance)
}
```

**Responses**:
- `200 OK`: Returns the same payload as GET (fresh snapshot) + `audit.latest[0]` referencing the new event.
- `400 Bad Request`: Invalid action/mode transitions (e.g., resume while already running) or missing reason.

## 3. Claim response extension

`POST /api/queue/jobs/claim` now returns:
```json
{
  "job": null,
  "system": {
    "workersPaused": true,
    "mode": "drain",
    "reason": "Upgrading images",
    "version": 12,
    "requestedAt": "2026-02-20T01:55:00Z",
    "updatedAt": "2026-02-20T02:00:00Z"
  }
}
```
- When `workersPaused=false`, the `system` block still returns the latest status so workers may log version changes.

## 4. Heartbeat response extension

`POST /api/queue/jobs/{jobId}/heartbeat` returns the standard job document plus `system` metadata:
```json
{
  "id": "...",
  "status": "running",
  "payload": { ... },
  "system": {
    "workersPaused": true,
    "mode": "quiesce",
    "reason": "Cluster maintenance",
    "version": 13
  }
}
```
- Workers use `mode` to decide whether to stop between stage or task-step checkpoints while still heartbeating.

## 5. MCP propagation

`POST /mcp/tools/call` → `queue.claim` and `queue.heartbeat` mirror the JSON above because responses are serialized using the shared schema.
