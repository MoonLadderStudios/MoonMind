# Contract: System Worker Pause Operations API

## GET `/api/system/worker-pause`

Returns the current worker operation snapshot for Settings -> Operations.

Response `200`:

```json
{
  "system": {
    "workersPaused": false,
    "mode": "running",
    "reason": "Normal operation",
    "version": 1,
    "requestedByUserId": null,
    "requestedAt": null,
    "updatedAt": "2026-04-28T00:00:00Z"
  },
  "metrics": {
    "queued": 0,
    "running": 0,
    "staleRunning": 0,
    "isDrained": true,
    "metricsSource": "temporal"
  },
  "audit": {
    "latest": [
      {
        "id": "00000000-0000-0000-0000-000000000000",
        "action": "pause",
        "mode": "drain",
        "reason": "Maintenance",
        "actorUserId": "00000000-0000-0000-0000-000000000001",
        "createdAt": "2026-04-28T00:01:00Z"
      }
    ]
  },
  "signalStatus": "succeeded"
}
```

## POST `/api/system/worker-pause`

Submits an authorized worker operation command.

Pause request:

```json
{
  "action": "pause",
  "mode": "drain",
  "reason": "Maintenance window",
  "confirmation": "Pause workers confirmed"
}
```

Resume request:

```json
{
  "action": "resume",
  "reason": "Maintenance complete",
  "forceResume": false,
  "confirmation": "Resume workers confirmed"
}
```

Response `200`: same snapshot shape as `GET`, with `signalStatus` set to the command result summary.

Errors:
- `400 worker_operation_invalid`: malformed action or mode.
- `403 worker_operation_forbidden`: authenticated actor may not invoke operations.
- `409 worker_operation_conflict`: command conflicts with current operation state.
- `422 worker_operation_confirmation_required`: disruptive operation lacks required confirmation.
- `503 worker_operation_unavailable`: operation subsystem cannot process the command.

Security:
- Backend authorization is authoritative.
- The route must not accept raw command strings, shell paths, credentials, or arbitrary operation names.
- Audit payloads must remain sanitized and non-secret.
