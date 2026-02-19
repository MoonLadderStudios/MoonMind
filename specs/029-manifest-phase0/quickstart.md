# Quickstart: Manifest Queue Phase 0

**Feature**: Manifest Queue Phase 0  
**Branch**: `029-manifest-phase0`

## Prerequisites

- MoonMind API dependencies installed (Python 3.11 env, Poetry/Node deps).  
- Postgres + RabbitMQ reachable (local stack or `docker compose up rabbitmq api celery-worker`).  
- Worker/service env vars configured (see `docs/ManifestTaskSystem.md` §9).  
- Auth token for the API: `MOONMIND_API_TOKEN`.  
- `jq` available for quick JSON encoding.  
- Feature branch `029-manifest-phase0` checked out.

## 1. Define a v0 Manifest

Save the YAML below as `examples/confluence-demo.yaml` (adjust sources as needed):

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
  connection:
    host: "${QDRANT_HOST}"
dataSources:
  - id: "confluence-main"
    type: "ConfluenceReader"
    params:
      spaceKey: "ENG"
run:
  dryRun: false
```

## 2. Register the Manifest in the Registry

```bash
curl -X PUT "http://localhost:5000/api/manifests/confluence-demo" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -Rs '{content:.}' examples/confluence-demo.yaml)"
```

Expected: response includes `contentHash`, `version: "v0"`, and empty `state`.

## 3. Submit an Inline Manifest Job (optional smoke)

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
        "content": "version: \"v0\"\nmetadata:\n  name: confluence-demo-inline\n..."
      },
      "options": { "dryRun": true }
    }
  }
}
JSON
```

Expected: response `payload.requiredCapabilities` includes `["manifest","embeddings","openai","qdrant","confluence"]`.

## 4. Create a Registry-Backed Run

```bash
curl -X POST "http://localhost:5000/api/manifests/confluence-demo/runs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"run","options":{"forceFull":false,"maxDocs":250}}'
```

Expected: HTTP 201 with `jobId` and queue metadata carrying `manifestHash` + derived capabilities.

## 5. Verify Manifest Job Isolation & Sanitization

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs?type=manifest&limit=20" | jq '.items[0].payload'
```

Expected payload fields:
- `manifest.name`, `manifest.action`, `manifest.source.kind`, optional `source.name`.
- `manifestHash`, `manifestVersion`, `requiredCapabilities`.
- **No** inline YAML (`manifest.source.content` should be absent).

## 6. Run Automated Tests

```bash
./tools/test_unit.sh
```

Expected: manifest contract + registry tests pass, proving compliance with DOC-REQ-001…005 and FR-010.
