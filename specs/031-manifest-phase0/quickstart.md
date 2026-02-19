# Quickstart: Manifest Task System Phase 0

**Feature**: Manifest Task System Phase 0  
**Branch**: `031-manifest-phase0`

## Prerequisites

- Python deps installed (`poetry install`) and API service env configured.  
- Postgres + RabbitMQ running (e.g., `docker compose up rabbitmq api celery-worker`).  
- API token exported as `MOONMIND_API_TOKEN`.  
- Sample manifest YAML saved locally (see Step 1).  
- Feature branch `031-manifest-phase0` checked out.

## 1. Draft a v0 Manifest

Save as `examples/confluence-demo.yaml`:

```yaml
version: "v0"
metadata:
  name: "confluence-demo"
embeddings:
  provider: "openai"
  model: "text-embedding-3-small"
vectorStore:
  type: "qdrant"
  indexName: "knowledge-base"
dataSources:
  - id: "confluence-main"
    type: "ConfluenceReader"
run:
  dryRun: false
```

## 2. Upsert the Manifest into the Registry

```bash
curl -X PUT "http://localhost:5000/api/manifests/confluence-demo" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -Rs '{content:.}' examples/confluence-demo.yaml)"
```

Expected: response shows `contentHash`, `version: "v0"`, and empty `state`.

## 3. Submit an Inline Manifest Job (smoke test)

```bash
curl -X POST "http://localhost:5000/api/queue/jobs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "type": "manifest",
  "priority": 5,
  "payload": {
    "manifest": {
      "name": "confluence-demo-inline",
      "action": "run",
      "source": {
        "kind": "inline",
        "content": "version: \"v0\"\nmetadata:\n  name: confluence-demo-inline\nembeddings:\n  provider: openai\nvectorStore:\n  type: qdrant\ndataSources:\n- id: docs\n  type: ConfluenceReader\n"
      },
      "options": { "dryRun": true }
    }
  }
}
JSON
```

Expected: HTTP 201 with `payload.requiredCapabilities` like `["manifest","embeddings","openai","qdrant","confluence"]` and `manifestHash` set.

## 4. Trigger a Registry-Backed Run

```bash
curl -X POST "http://localhost:5000/api/manifests/confluence-demo/runs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"run","options":{"forceFull":false,"maxDocs":250}}'
```

Expected: HTTP 201 response with `jobId` plus queue metadata (`requiredCapabilities`, `manifestHash`). Registry record should update `last_run_*` timestamps.

## 5. Verify Queue Isolation & Sanitization

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs?type=manifest&limit=5" | jq '.items[0].payload'
```

Expected payload fields:
- `manifest.name`, `manifest.action`, `manifest.source.kind`, optional `source.name`
- `manifestHash`, `manifestVersion`, `requiredCapabilities`, `effectiveRunConfig`
- **No** `manifest.source.content`

## 6. Confirm Secret Rejection (FR-007)

```bash
curl -X POST "http://localhost:5000/api/queue/jobs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<'JSON'
{
  "type": "manifest",
  "payload": {
    "manifest": {
      "name": "bad-secret",
      "action": "plan",
      "source": {
        "kind": "inline",
        "content": "version: \"v0\"\nmetadata:\n  name: bad-secret\nembeddings:\n  provider: openai\nvectorStore:\n  type: qdrant\nconnection:\n  apiKey: sk-live-1234567890foobar\n"
      }
    }
  }
}
JSON
```

Expected: HTTP 422 with `detail.code="invalid_manifest"` citing raw secret detection.

## 7. Run Automated Tests

```bash
./tools/test_unit.sh
```

Tests should cover manifest contract (including secret detection), registry service/router, queue serialization, and capability gating to satisfy FR-013.
