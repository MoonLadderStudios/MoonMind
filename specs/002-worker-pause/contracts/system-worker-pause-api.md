# API Contract: System Worker Pause Controls

## 1. `GET /api/system/worker-pause`

**Auth**: Operator (same dependency used by dashboard routes).  
**Response 200**:
```json
{
  "system": {
    "workersPaused": true,
    "mode": "drain",
    "reason": "Rolling API migration",
    "version": 12,
    "requestedByUserId": "0f1e9021-7a3a-4e21-af4d-4b9c917a2ad5",
    "requestedAt": "2026-02-20T05:02:13Z",
    "updatedAt": "2026-02-20T05:02:13Z"
  },
  "metrics": {
    "queued": 148,
    "running": 3,
    "staleRunning": 1,
    "isDrained": false
  },
  "audit": {
    "latest": [
      {
        "id": "9771f9a6-ecac-4d1f-8df3-b67c2d67aa5d",
        "action": "pause",
        "mode": "drain",
        "reason": "Rolling API migration",
        "actorUserId": "0f1e9021-7a3a-4e21-af4d-4b9c917a2ad5",
        "createdAt": "2026-02-20T05:02:13Z"
      },
      {
        "id": "e3a9e019-e573-4ba2-875b-4e2a20d901dc",
        "action": "resume",
        "mode": null,
        "reason": "Deployment complete",
        "actorUserId": "0f1e9021-7a3a-4e21-af4d-4b9c917a2ad5",
        "createdAt": "2026-02-20T03:44:09Z"
      }
    ]
  }
}
```
- `metrics.staleRunning` exposes expired leases that still need attention before resuming.
- `audit.latest` is limited (default 5) for dashboard context; full history remains in `system_control_events`.

## 2. `POST /api/system/worker-pause`

**Body**:
```json
{
  "action": "pause",          // "pause" | "resume"
  "mode": "drain",            // required when action=pause, ignored on resume
  "reason": "Upgrading images",
  "forceResume": false        // optional flag to bypass isDrained check (default false)
}
```

**Responses**:
- `200 OK`: Returns the same payload as GET with updated `audit.latest[0]`.
- `400 Bad Request`: Missing reason/mode, conflicting pause request (e.g., same mode, empty reason), invalid mode string.
- `409 Conflict`: Resume attempted while `metrics.isDrained=false` and `forceResume` omitted; response detail includes current metrics so the dashboard can show a warning dialog.

All POST paths require a non-empty `reason`. Resume accepts either a new reason (“Maintenance complete”) or repeats the previous pause reason for audit continuity.

## 3. Claim response extension

`POST /api/queue/jobs/claim` envelope:
```json
{
  "job": null,
  "system": {
    "workersPaused": true,
    "mode": "quiesce",
    "reason": "Short network maintenance",
    "version": 14,
    "requestedAt": "2026-02-20T06:05:00Z",
    "updatedAt": "2026-02-20T06:05:00Z"
  }
}
```
- When `workersPaused=false`, the `system` block still appears (with `mode=null`) so workers can detect resumed versions.
- Claim guards run before `_requeue_expired_jobs`, ensuring the queue state is untouched during pause windows.

## 4. Heartbeat response extension

`POST /api/queue/jobs/{jobId}/heartbeat` response:
```json
{
  "id": "0e8f9c7a-0d8d-4df3-b89a-9cce90c450f5",
  "status": "running",
  "payload": { "...": "..." },
  "system": {
    "workersPaused": true,
    "mode": "quiesce",
    "reason": "Short network maintenance",
    "version": 14
  }
}
```
- Workers treat `system.mode="quiesce"` as a signal to set their local pause event at the next checkpoint while continuing heartbeats.
- Heartbeat responses keep existing fields untouched for backward compatibility.

## 5. Dashboard + MCP usage

- Dashboard JS polls GET every ~5 s, renders a banner with `system` + `metrics`, and sends POST requests with `{action, mode, reason}`. When `metrics.isDrained=false`, the UI prompts for confirmation before sending `forceResume:true`.
- MCP `queue.claim` / `queue.heartbeat` tools return the same JSON envelopes serialized via the shared Pydantic models so IDE client code can surface pause status without additional parsing.
