# Quickstart: Temporal Source of Truth and Projection Model

## Prerequisites

- Branch: `048-source-truth-projection`
- Docker Engine + Docker Compose available
- MoonMind DB migrations applied (`init-db`)
- Temporal foundation services available from `docker-compose.yaml`
- This feature remains in **runtime implementation mode**: production code changes plus validation tests are required

## 1. Start the runtime stack

```bash
docker compose up -d \
  api-db \
  temporal-db \
  temporal \
  temporal-namespace-init \
  temporal-worker-workflow \
  temporal-worker-artifacts \
  temporal-worker-llm \
  temporal-worker-sandbox \
  temporal-worker-integrations \
  api
```

Optional logs while validating:

```bash
docker compose logs -f api temporal temporal-worker-workflow
```

## 2. Start a Temporal-managed execution

```bash
curl -sS -X POST http://localhost:8000/api/executions \
  -H 'Content-Type: application/json' \
  -d '{
    "workflowType": "MoonMind.Run",
    "title": "Temporal source-of-truth validation",
    "idempotencyKey": "source-truth-start-1",
    "initialParameters": {
      "goal": "validate-runtime-authority"
    }
  }'
```

Expected after implementation:

- Response includes canonical `workflowId` and latest `runId`
- Execution exists authoritatively in Temporal before it is treated as real
- Projection/source metadata reflects a Temporal-authoritative row, not a projection-only start

## 3. Validate update, signal, and cancel behavior

Use the returned `workflowId`:

```bash
curl -sS -X POST http://localhost:8000/api/executions/<workflowId>/update \
  -H 'Content-Type: application/json' \
  -d '{
    "updateName": "SetTitle",
    "title": "Updated from Temporal",
    "idempotencyKey": "source-truth-update-1"
  }'

curl -sS -X POST http://localhost:8000/api/executions/<workflowId>/signal \
  -H 'Content-Type: application/json' \
  -d '{
    "signalName": "Pause"
  }'

curl -sS -X POST http://localhost:8000/api/executions/<workflowId>/cancel \
  -H 'Content-Type: application/json' \
  -d '{
    "reason": "source-truth validation complete"
  }'
```

Expected after implementation:

- API acceptance/rejection matches Temporal outcomes
- No projection-only success path exists for Temporal-managed executions
- Projection sync state updates from authoritative outcomes and remains repairable if persistence lags

## 4. Validate list/detail source semantics

```bash
curl -sS "http://localhost:8000/api/executions?workflowType=MoonMind.Run&pageSize=10"
curl -sS "http://localhost:8000/api/executions/<workflowId>"
```

Expected after implementation:

- List/filter/count is backed by Temporal Visibility for Temporal-managed routes
- Detail is sourced from direct execution truth plus safe enrichment
- `countMode` is truthful to the active source (`exact` only when exactness is real)
- Compatibility/task-oriented views preserve canonical identifiers and source metadata

## 5. Validate Continue-As-New projection behavior

Run a flow that triggers rerun or Continue-As-New, then inspect the same `workflowId` again.

Expected after implementation:

- `workflowId` stays stable
- `runId` changes to the latest run
- the existing primary projection row is updated in place
- no duplicate primary execution rows are created for the same Workflow ID

## 6. Validate repair and ghost-row protection

Trigger or simulate these cases during testing:

1. Projection upsert failure after a successful Temporal mutation
2. Missing projection row for an existing Temporal execution
3. Stale `runId` after Continue-As-New
4. Orphaned projection row without authoritative Temporal backing

Expected after implementation:

- repair-on-read and background repair restore canonical projection state where possible
- orphaned rows are quarantined/hidden from normal active reads
- no active ghost row is served as a real execution

## 7. Validate degraded-mode honesty

Test these failure modes:

1. Temporal unavailable for writes
2. Visibility degraded while execution truth remains reachable
3. projection store unavailable while Temporal remains healthy

Expected after implementation:

- production writes fail cleanly when Temporal is unavailable
- stale or partial read fallback only appears on explicitly supported routes
- source outages and fallback posture remain visible in payload metadata and logs

## 8. Run required validation

Repository-standard unit and dashboard validation:

```bash
./tools/test_unit.sh
```

Compose-backed contract validation:

```bash
docker compose -f docker-compose.test.yaml run --rm -e TEST_TYPE=contract pytest
```

Compose-backed Temporal integration validation:

```bash
docker compose -f docker-compose.test.yaml run --rm -e TEST_TYPE=integration/temporal pytest
```

When `tasks.md` exists and runtime code is implemented, run runtime scope gates:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main
```

Expected:

- unit, contract, and Temporal integration validation passes
- runtime scope gates pass in `runtime` mode
- docs-only task sets or docs-only diffs are rejected for this feature
