# Quickstart: Agent Queue MVP (Milestone 1)

**Feature**: Agent Queue MVP  
**Branch**: `009-agent-queue-mvp`

## Prerequisites

- API dependencies installed.
- Postgres available for local development.
- Feature branch checked out.

## 1. Apply Migration

Run the new migration so `agent_jobs` exists:

```bash
alembic upgrade head
```

## 2. Start API Service

Run the API service using the local project workflow you already use for MoonMind.

## 3. Validate Queue API Basics

Create a job:

```bash
curl -X POST http://localhost:5000/api/queue/jobs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{"type":"codex_exec","priority":10,"payload":{"instruction":"test"}}'
```

Claim a job:

```bash
curl -X POST http://localhost:5000/api/queue/jobs/claim \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{"workerId":"executor-01","leaseSeconds":120}'
```

List jobs:

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs?status=queued&limit=20"
```

## 4. Run Unit Tests

Use the project-standard unit test script:

```bash
./tools/test_unit.sh
```

## Expected Results

- Migration succeeds and `agent_jobs` table is present.
- Queue endpoints accept valid requests and return expected lifecycle states.
- Unit tests pass, including transition and concurrency coverage.
