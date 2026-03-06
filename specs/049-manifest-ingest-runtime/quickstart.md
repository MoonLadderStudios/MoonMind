# Quickstart: Manifest Ingest Runtime

## 1. Set feature context for `.specify` scope checks

The current workspace branch is a MoonMind task branch rather than `049-manifest-ingest-runtime`, so use the explicit feature override when invoking `.specify` scripts:

```bash
export SPECIFY_FEATURE=049-manifest-ingest-runtime
```

## 2. Start the local Temporal runtime stack

Bring up the API service, Temporal server, and all Temporal worker fleets used by manifest ingest:

```bash
docker compose up \
  api \
  temporal \
  temporal-worker-workflow \
  temporal-worker-artifacts \
  temporal-worker-llm \
  temporal-worker-sandbox \
  temporal-worker-integrations
```

Expected result:

- `api` serves `/api/manifests`, `/api/executions`, and artifact endpoints.
- `temporal-worker-workflow` runs actual workflow definitions instead of bootstrap-only topology reporting.
- activity workers remain bound to `mm.activity.*` queues.

## 3. Submit a manifest ingest

Registry-backed submit path:

```bash
curl -sS -X POST http://localhost:8000/api/manifests/demo/runs \
  -H 'Content-Type: application/json' \
  -d '{
    "action": "run",
    "failurePolicy": "fail_fast",
    "maxConcurrency": 25,
    "tags": {"env": "local"}
  }'
```

Artifact-first execution path:

```bash
curl -sS -X POST http://localhost:8000/api/executions \
  -H 'Content-Type: application/json' \
  -d '{
    "workflowType": "MoonMind.ManifestIngest",
    "manifestArtifactRef": "art_manifest_123",
    "failurePolicy": "best_effort",
    "initialParameters": {
      "requestedBy": {"type": "user", "id": "demo-user"},
      "executionPolicy": {"maxConcurrency": 10}
    }
  }'
```

Expected result:

- Response identifies `workflowType=MoonMind.ManifestIngest`.
- Execution state starts at `initializing`.
- Manifest payload itself remains artifact-backed rather than embedded in the response.

## 4. Inspect status and lineage

Read bounded ingest status:

```bash
curl -sS http://localhost:8000/api/executions/mm:manifest-1/manifest-status
```

Read paginated child-run lineage:

```bash
curl -sS 'http://localhost:8000/api/executions/mm:manifest-1/manifest-nodes?state=running&limit=50'
```

Expected result:

- Execution detail and status use shared lifecycle fields (`initializing`, `executing`, `finalizing`, `succeeded`, `failed`, `canceled`).
- Child-run pagination resolves from `runIndexArtifactRef`, not from ad hoc Search Attributes or a second DB projection.

## 5. Exercise edit operations

Example concurrency update:

```bash
curl -sS -X POST http://localhost:8000/api/executions/mm:manifest-1/update \
  -H 'Content-Type: application/json' \
  -d '{
    "updateName": "SetConcurrency",
    "maxConcurrency": 5,
    "idempotencyKey": "manifest-1-concurrency-1"
  }'
```

Example future-work replacement:

```bash
curl -sS -X POST http://localhost:8000/api/executions/mm:manifest-1/update \
  -H 'Content-Type: application/json' \
  -d '{
    "updateName": "UpdateManifest",
    "newManifestArtifactRef": "art_manifest_456",
    "mode": "REPLACE_FUTURE",
    "idempotencyKey": "manifest-1-replace-1"
  }'
```

Expected result:

- Valid update requests return acknowledged apply semantics.
- Attempts to mutate already-started work reject deterministically.
- Workflow state, not DB-side edits, remains the source of truth.

## 6. Run required validation

Repository-standard unit and dashboard validation:

```bash
./tools/test_unit.sh
```

Runtime scope gates:

```bash
SPECIFY_FEATURE=049-manifest-ingest-runtime \
  ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime

SPECIFY_FEATURE=049-manifest-ingest-runtime \
  ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected result:

- `./tools/test_unit.sh` passes with manifest ingest runtime coverage included.
- Both runtime scope checks pass, proving runtime code and test changes are present.

Validation evidence from this workspace on 2026-03-06:

- `./tools/test_unit.sh`
  - Result: passed
  - Evidence: `1320 passed, 14 subtests passed`
- `SPECIFY_FEATURE=049-manifest-ingest-runtime ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
  - Result: passed
  - Evidence: `Scope validation passed: tasks check (runtime tasks=18, validation tasks=14).`
- `SPECIFY_FEATURE=049-manifest-ingest-runtime ./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
  - Result: passed
  - Evidence: `Scope validation passed: diff check (runtime files=18, test files=11).`

## 7. Mode note

Docs-mode scope checks are intentionally not sufficient for this feature:

```bash
SPECIFY_FEATURE=049-manifest-ingest-runtime \
  ./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode docs
```

That command should report a docs-mode skip, but this feature's selected orchestration mode is runtime, so successful delivery still requires production code changes plus tests.
