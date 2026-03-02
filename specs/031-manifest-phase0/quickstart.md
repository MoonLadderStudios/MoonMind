# Quickstart: Manifest Phase 0 Rebaseline Validation

**Feature**: `031-manifest-phase0`  
**Branch**: `031-manifest-phase0`

## Prerequisites

- Dependencies installed (`poetry install`).
- Services running: `docker compose up rabbitmq api celery-worker`.
- API token exported: `MOONMIND_API_TOKEN`.
- Branch checked out: `031-manifest-phase0`.

## 1. Create a v0 Manifest Fixture

Save to `examples/manifests/phase0-demo.yaml`:

```yaml
version: "v0"
metadata:
  name: "phase0-demo"
embeddings:
  provider: "openai"
vectorStore:
  type: "qdrant"
dataSources:
  - id: "repo-docs"
    type: "GithubRepositoryReader"
run:
  dryRun: false
```

## 2. Upsert the Manifest into the Registry

```bash
curl -X PUT "http://localhost:5000/api/manifests/phase0-demo" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$(jq -Rs '{content:.}' examples/manifests/phase0-demo.yaml)"
```

Expected:
- HTTP 200
- Response includes `name`, `contentHash`, `version` (`v0`), and `state`

## 3. Submit an Inline Manifest Queue Job

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
      "name": "phase0-demo",
      "action": "run",
      "source": {
        "kind": "inline",
        "content": "version: \"v0\"\nmetadata:\n  name: phase0-demo\nembeddings:\n  provider: openai\nvectorStore:\n  type: qdrant\ndataSources:\n  - id: repo-docs\n    type: GithubRepositoryReader\n"
      },
      "options": {"dryRun": true}
    }
  }
}
JSON
```

Expected:
- HTTP 201
- `payload.manifestHash` and `payload.manifestVersion` are present
- `payload.requiredCapabilities` includes `manifest`, `embeddings`, `openai`, `qdrant`, `github`

## 4. Submit a Registry-Backed Run

```bash
curl -X POST "http://localhost:5000/api/manifests/phase0-demo/runs" \
  -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"action":"run","options":{"forceFull":false,"maxDocs":200}}'
```

Expected:
- HTTP 201
- Response contains `jobId` and `queue.requiredCapabilities`
- Registry record updates `lastRun*` metadata

## 5. Verify Queue Payload Sanitization

```bash
curl -H "Authorization: Bearer $MOONMIND_API_TOKEN" \
  "http://localhost:5000/api/queue/jobs?type=manifest&limit=5" | jq '.items[0].payload'
```

Expected:
- Contains `manifest.name`, `manifest.action`, `manifest.source.kind`
- Contains `manifestHash`, `manifestVersion`, `requiredCapabilities`, optional `manifestSecretRefs`
- Does **not** contain `manifest.source.content`

## 6. Verify Fail-Fast Secret Rejection

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
      "action": "run",
      "source": {
        "kind": "inline",
        "content": "version: \"v0\"\nmetadata:\n  name: bad-secret\nembeddings:\n  provider: openai\nvectorStore:\n  type: qdrant\ndataSources:\n  - id: docs\n    type: GithubRepositoryReader\nauth:\n  apiKey: <OPENAI_API_KEY>\n"
      }
    }
  }
}
JSON
```

Expected:
- HTTP 422
- `detail.code = "invalid_queue_payload"`
- `detail.message` references manifest raw secret rejection

## 7. Run Regression Suite

```bash
./tools/test_unit.sh
```

Expected:
- Manifest contract, queue routing/sanitization, registry CRUD/run submission, and capability-claim tests pass.
- Test evidence supports FR-010 and contributes to FR-011 traceability.

## 8. Run Runtime Tasks Scope Gate

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
```

Expected:
- Scope validation passes with runtime implementation tasks and validation tasks present.

## 9. Run Runtime Diff Scope Gate

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

Expected:
- Scope validation passes with runtime code and test files present in the implementation diff.

## Verification Evidence (2026-03-02)

- `./tools/test_unit.sh`: **PASS** (`897 passed, 8 subtests passed in 85.75s`).
- `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`: **PASS** (`runtime tasks=13, validation tasks=8`).
- `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`: **PASS** (`runtime files=3, test files=4`).
