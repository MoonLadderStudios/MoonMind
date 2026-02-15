# API Contract: Queue Hardening Surfaces (Milestone 5)

## 1) Worker-authenticated claim with policy context

### Request

`POST /api/queue/jobs/claim`

Headers (one of):

- `X-MoonMind-Worker-Token: <raw token>`
- OIDC/JWT `Authorization: Bearer <token>` (existing auth flow)

Body:

```json
{
  "workerId": "executor-01",
  "leaseSeconds": 120,
  "allowedTypes": ["codex_exec"],
  "workerCapabilities": ["git", "gh", "codex"]
}
```

### Response

```json
{
  "job": {
    "id": "...",
    "type": "codex_exec",
    "status": "running",
    "attempt": 1,
    "maxAttempts": 3,
    "nextAttemptAt": null,
    "payload": {
      "repository": "MoonLadderStudios/MoonMind",
      "codex": {
        "model": "gpt-5-codex",
        "effort": "high"
      },
      "requiredCapabilities": ["git", "codex"]
    }
  }
}
```

Per-task Codex overrides are expressed via `payload.codex.model` and
`payload.codex.effort`, and should take precedence over worker defaults for that
job only.

## 2) Retry/dead-letter behavior

### Retryable fail request

`POST /api/queue/jobs/{jobId}/fail`

```json
{
  "workerId": "executor-01",
  "errorMessage": "network timeout",
  "retryable": true
}
```

### Retry response shape

- If attempts remain: `status = queued`, `nextAttemptAt` populated.
- If exhausted: `status = dead_letter`, `nextAttemptAt = null`.

## 3) Job events append + incremental poll

### Append event

`POST /api/queue/jobs/{jobId}/events`

```json
{
  "workerId": "executor-01",
  "level": "info",
  "message": "Starting codex execution",
  "payload": {"phase": "execute"}
}
```

### List events

`GET /api/queue/jobs/{jobId}/events?after=2026-02-14T09:32:11Z&limit=200`

Response:

```json
{
  "items": [
    {
      "id": "...",
      "jobId": "...",
      "level": "info",
      "message": "Starting codex execution",
      "payload": {"phase": "execute"},
      "createdAt": "2026-02-14T09:32:11.231Z"
    }
  ]
}
```

## 4) Worker token administration (minimal)

### Create token

`POST /api/queue/workers/tokens`

```json
{
  "workerId": "executor-01",
  "description": "Primary executor",
  "allowedRepositories": ["MoonLadderStudios/MoonMind"],
  "allowedJobTypes": ["codex_exec"],
  "capabilities": ["git", "gh", "codex"]
}
```

Response includes one-time secret:

```json
{
  "id": "...",
  "workerId": "executor-01",
  "token": "mmwt_...",
  "allowedRepositories": ["MoonLadderStudios/MoonMind"],
  "allowedJobTypes": ["codex_exec"],
  "capabilities": ["git", "gh", "codex"],
  "isActive": true
}
```
