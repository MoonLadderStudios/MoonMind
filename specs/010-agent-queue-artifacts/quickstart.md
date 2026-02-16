# Quickstart: Agent Queue Artifact Upload (Milestone 2)

**Feature**: Agent Queue Artifact Upload  
**Branch**: `010-agent-queue-artifacts`

## Prerequisites

- Milestone 1 queue API is available.
- Database migrations are up to date.
- API service is running.

## 1. Apply Migration

```bash
alembic upgrade head
```

## 2. Configure Artifact Root and Limits

Set environment values (or defaults):

```bash
AGENT_JOB_ARTIFACT_ROOT=var/artifacts/agent_jobs
AGENT_JOB_ARTIFACT_MAX_BYTES=10485760
```

## 3. Create or Reuse a Queue Job

```bash
curl -X POST http://localhost:5000/api/queue/jobs \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -d '{"type":"codex_exec","priority":10,"payload":{"instruction":"artifact test"}}'
```

Capture returned `id` as `JOB_ID`.

## 4. Upload an Artifact

```bash
curl -X POST "http://localhost:5000/api/queue/jobs/$JOB_ID/artifacts/upload" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -F "file=@/tmp/test.log" \
  -F "name=logs/test.log" \
  -F "contentType=text/plain"
```

## 5. List Artifacts

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs/$JOB_ID/artifacts"
```

Capture artifact id as `ARTIFACT_ID`.

## 6. Download Artifact

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs/$JOB_ID/artifacts/$ARTIFACT_ID/download" \
  -o /tmp/downloaded.log
```

## 7. Validate Security Guards

- Attempt upload with traversal name (expect rejection):

```bash
curl -X POST "http://localhost:5000/api/queue/jobs/$JOB_ID/artifacts/upload" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -F "file=@/tmp/test.log" \
  -F "name=../../escape.log"
```

- Attempt oversized upload (expect rejection).

## 8. Run Unit Tests

```bash
./tools/test_unit.sh
```
