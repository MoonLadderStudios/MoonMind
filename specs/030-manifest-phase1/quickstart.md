# Quickstart: Manifest Task System Phase 1 (Worker Readiness)

**Feature**: Manifest Task System Phase 1 (Worker Readiness)  
**Updated**: March 1, 2026

## 1. Start API dependencies

Bring up the API stack you normally use for local unit/integration work.

## 2. Seed a manifest and queue run

1. Upsert a registry manifest:

```bash
curl -X PUT "$MOONMIND_URL/api/manifests/demo-manifest" \
  -H "Content-Type: application/json" \
  -d '{"content":"version: \"v0\"\nmetadata:\n  name: demo-manifest\nembeddings:\n  provider: openai\n  model: text-embedding-3-small\nvectorStore:\n  type: qdrant\n  indexName: demo\ndataSources:\n  - id: docs\n    type: SimpleDirectoryReader\n    path: ./docs\n"}'
```

2. Submit a run:

```bash
curl -X POST "$MOONMIND_URL/api/manifests/demo-manifest/runs" \
  -H "Content-Type: application/json" \
  -d '{"action":"run"}'
```

Capture `jobId` from the response.

## 3. Resolve worker secrets for running manifest jobs

After the worker has claimed the job and it is `running`, call:

```bash
curl -X POST "$MOONMIND_URL/api/queue/jobs/$JOB_ID/manifest/secrets" \
  -H "X-MoonMind-Worker-Token: $WORKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"includeProfile":true,"includeVault":true}'
```

Expected:
- `profile[]` contains resolved values for profile/env-backed refs.
- `vault[]` contains pass-through vault reference metadata.

## 4. Persist manifest checkpoint state

```bash
curl -X POST "$MOONMIND_URL/api/manifests/demo-manifest/state" \
  -H "Content-Type: application/json" \
  -d '{
    "stateJson": {
      "docs": {
        "cursor": "2026-03-01T12:00:00Z",
        "docHashes": {"doc-1": "sha256:abc"}
      }
    },
    "lastRunStatus": "succeeded"
  }'
```

Then verify:

```bash
curl "$MOONMIND_URL/api/manifests/demo-manifest"
```

Expected: `state.stateJson` and `state.stateUpdatedAt` are populated.

## 5. Run tests

```bash
./tools/test_unit.sh
```

This must pass before handoff.
