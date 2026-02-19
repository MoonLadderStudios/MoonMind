# Quickstart: Manifest Task System Phase 1

**Feature**: Manifest Task System Phase 1  
**Branch**: `030-manifest-phase1`

## Prerequisites

- Core stack running: `docker compose up rabbitmq api celery-worker` (API handles queue + registry endpoints).  
- Qdrant reachable via `QDRANT_HOST`/`QDRANT_PORT` (or `QDRANT_URL`).  
- Embedding provider credentials exported (e.g., `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `OLLAMA_BASE_URL`).  
- Manifest worker env vars configured:
  - `MOONMIND_URL`, `MOONMIND_WORKER_ID`, `MOONMIND_WORKER_TOKEN`  
  - `MOONMIND_WORKER_ALLOWED_TYPES=manifest`  
  - `MOONMIND_WORKER_CAPABILITIES=manifest,qdrant,embeddings,github,confluence,gdrive,local_fs` (adjust per adapters)  
  - `MOONMIND_WORKDIR` pointing to a writable directory (e.g., `var/manifest-worker`)
- Feature branch `030-manifest-phase1` checked out and dependencies installed (`poetry install`).

## 1. Start the Manifest Worker

```bash
MOONMIND_URL="http://localhost:5000" \
MOONMIND_WORKER_ID="manifest-dev" \
MOONMIND_WORKER_TOKEN="$(cat var/secrets/manifest-worker.token)" \
MOONMIND_WORKDIR="/workspace/manifest-worker" \
MOONMIND_WORKER_ALLOWED_TYPES="manifest" \
MOONMIND_WORKER_CAPABILITIES="manifest,qdrant,embeddings,github,confluence" \
QDRANT_HOST="localhost" QDRANT_PORT="6333" \
OPENAI_API_KEY="..." \
poetry run moonmind-manifest-worker
```

Expected: startup logs print preflight checks (Qdrant ping, embedding provider readiness) and the worker begins polling `type="manifest"` jobs.

## 2. Register a Demo Manifest

```bash
cat <<'YAML' > examples/manifest-phase1.yaml
version: "v0"
metadata:
  name: "phase1-confluence-demo"
embeddings:
  provider: "openai"
  model: "text-embedding-3-small"
vectorStore:
  type: "qdrant"
  indexName: "manifest-phase1-demo"
  allowCreateCollection: true
  connection:
    host: "${QDRANT_HOST}"
    apiKey: "${QDRANT_API_KEY:-vault://qdrant#api_key}"
dataSources:
  - id: "confluence-main"
    type: "ConfluenceReader"
    params:
      baseUrl: "https://confluence.example.com"
      spaceKey: "ENG"
      username: "${CONFLUENCE_USER}"
      apiToken: "${CONFLUENCE_TOKEN}"
transforms:
  htmlToText: true
  splitter:
    chunkSize: 800
    chunkOverlap: 100
run:
  dryRun: false
YAML

curl -X PUT "http://localhost:5000/api/manifests/phase1-confluence-demo" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -Rs '{content:.}' examples/manifest-phase1.yaml)"
```

Expected response includes `content_hash`, `version:"v0"`, and empty `state`.

## 3. Submit a Manifest Run

```bash
curl -X POST "http://localhost:5000/api/manifests/phase1-confluence-demo/runs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"run","options":{"forceFull":false,"maxDocs":1000}}'
```

Expected: API returns `{ "jobId": "...", "payload": { "manifestHash": "sha256:...", "requiredCapabilities": [...] } }`.

## 4. Monitor Stage Events & Artifacts

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs/<JOB_ID>/events?stream=true"
```

Watch for ordered events: `moonmind.manifest.validate`, `...plan`, `...fetch`, `...transform`, `...embed`, `...upsert`, `...finalize`. Each event contains counts (documents/chunks/points) and references newly uploaded artifacts (e.g., `reports/run_summary.json`).

To inspect artifacts:

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs/<JOB_ID>/artifacts" | jq
```

Verify `manifest/resolved.yaml` redacts secrets and `reports/checkpoint.json` mirrors `ManifestRecord.state_json`.

## 5. Test Incremental Sync

1. Modify or delete a document referenced by the manifest (e.g., remove a Confluence page).  
2. Resubmit the manifest run with the same payload.  
3. Confirm via events/artifacts that unchanged docs are skipped (`documentsChanged` remains low) and deletions are recorded in `reports/run_summary.json`.  
4. Inspect `state_json` via `GET /api/manifests/phase1-confluence-demo` to ensure checkpoint timestamps advance.

## 6. Test Cancellation

While a run is mid-`embed`, issue:

```bash
curl -X POST "http://localhost:5000/api/queue/jobs/<JOB_ID>/cancel" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN"
```

Expected: worker finishes the current batch, emits `moonmind.manifest.finalize` with `status:"cancelled"`, uploads `reports/run_summary.json` indicating partial counts, and acknowledges the cancellation.

## 7. Run Automated Tests

```bash
./tools/test_unit.sh
```

Expected suites:
- `tests/unit/manifest_v0/test_engine.py` covering chunking, embeddings, vector store deletes, checkpoint writes.  
- `tests/unit/manifest_v0/readers/test_*.py` covering adapter-specific edge cases.  
- `tests/unit/agents/test_manifest_worker.py` covering preflight, event ordering, cancellation, artifact uploads.  
- API/service tests verifying checkpoint endpoints and registry state updates.
