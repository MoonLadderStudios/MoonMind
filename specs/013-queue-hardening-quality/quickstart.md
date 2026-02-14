# Quickstart: Agent Queue Hardening and Quality (Milestone 5)

## Prerequisites

- Queue API service running.
- Database migrated with Milestone 5 queue schema updates.
- Worker token created (or OIDC/JWT auth available for worker principal).

## 1) Create a worker token (admin/operator)

```bash
curl -sS -X POST http://localhost:5000/api/queue/workers/tokens \
  -H 'Content-Type: application/json' \
  -d '{
    "workerId":"executor-01",
    "description":"local test worker",
    "allowedRepositories":["MoonLadderStudios/MoonMind"],
    "allowedJobTypes":["codex_exec"],
    "capabilities":["git","codex"]
  }'
```

Capture `token` from response.

## 2) Enqueue a capability-scoped job

```bash
curl -sS -X POST http://localhost:5000/api/queue/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "type":"codex_exec",
    "priority":10,
    "payload":{
      "repository":"MoonLadderStudios/MoonMind",
      "instruction":"echo test",
      "requiredCapabilities":["codex"]
    }
  }'
```

## 3) Claim with worker token and capabilities

```bash
curl -sS -X POST http://localhost:5000/api/queue/jobs/claim \
  -H 'Content-Type: application/json' \
  -H "X-MoonMind-Worker-Token: $WORKER_TOKEN" \
  -d '{
    "workerId":"executor-01",
    "leaseSeconds":120,
    "workerCapabilities":["codex","git"]
  }'
```

## 4) Append and poll events

```bash
curl -sS -X POST http://localhost:5000/api/queue/jobs/<job_id>/events \
  -H 'Content-Type: application/json' \
  -H "X-MoonMind-Worker-Token: $WORKER_TOKEN" \
  -d '{"workerId":"executor-01","level":"info","message":"started"}'
```

```bash
curl -sS "http://localhost:5000/api/queue/jobs/<job_id>/events?limit=50"
```

## 5) Validate retry + dead-letter behavior

- Submit repeated `fail` calls with `retryable=true`.
- Confirm first failures return `status=queued` with populated `nextAttemptAt`.
- Confirm exhausted retry returns `status=dead_letter` and is not claimable.

## 6) Unit tests

```bash
./tools/test_unit.sh
```

If Python tooling is missing locally, install pytest in the active environment first.
