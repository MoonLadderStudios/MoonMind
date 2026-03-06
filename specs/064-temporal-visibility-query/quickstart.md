# Quickstart: Temporal Visibility Query Model

## Goal

Validate the runtime implementation of `docs/Temporal/VisibilityAndUiQueryModel.md` through the existing execution APIs, Temporal service behavior, and task-oriented compatibility semantics.

## Preconditions

- Repo dependencies are installed for the local development environment.
- If validating against the full stack, the API service and any required local services are available through the normal MoonMind Docker Compose workflow.
- Treat this feature as **runtime implementation mode**: docs/spec updates alone are not a passing result.

## Validation flow

1. Review the feature artifacts in `specs/047-temporal-visibility-query/` to confirm the target contract and traceability matrix.
2. Implement runtime changes in the execution service, API router/schema, and projection helpers/migrations for the selected compatibility-adapter UI path.
3. Verify the canonical filter surface with the compatibility-adapter path:

```bash
curl --get http://localhost:8000/api/executions \
  --data-urlencode 'workflowType=MoonMind.Run' \
  --data-urlencode 'ownerType=user' \
  --data-urlencode 'entry=run' \
  --data-urlencode 'state=executing'
```

4. Confirm update responses expose an immediately patchable execution plus refresh metadata for compatibility consumers:

```bash
curl -X POST http://localhost:8000/api/executions/<workflowId>/update \
  -H 'content-type: application/json' \
  -d '{"updateName":"SetTitle","title":"Renamed title","idempotencyKey":"demo-1"}'
```

Expected compatibility fields:

- `execution.workflowId`
- `execution.taskId`
- `refresh.patchedExecution`
- `refresh.listStale`
- `refresh.refetchSuggested`
- `refresh.refreshedAt`

5. Run the repository-standard unit and dashboard validation entrypoint:

```bash
./tools/test_unit.sh
```

6. Run the focused execution contract validation that sits outside `./tools/test_unit.sh`:

```bash
python -m pytest -q tests/contract/test_temporal_execution_api.py
```

7. Run the runtime scope gates for the MoonMind task branch context:

```bash
SPECIFY_FEATURE=047-temporal-visibility-query .specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
SPECIFY_FEATURE=047-temporal-visibility-query .specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

8. If validating end-to-end compatibility behavior or Temporal worker interactions, bring up the relevant local services through Docker Compose and exercise:
   - `GET /api/executions`
   - `GET /api/executions/{workflowId}`
   - `POST /api/executions/{workflowId}/update`
   - `POST /api/executions/{workflowId}/signal`
   - `POST /api/executions/{workflowId}/cancel`

## Required assertions

- Temporal-backed list/detail payloads expose `workflowId` as the durable handle and keep `taskId == workflowId` on compatibility surfaces.
- Search Attributes and Memo obey the bounded v1 contract.
- Supported exact filters, ordering, page-token invalidation, and count semantics match the source document.
- `awaiting_external` preserves exact state plus bounded wait metadata and compatibility `dashboardStatus`.
- Standard users cannot query another user's executions or non-user-owned executions through end-user surfaces.
- Compatibility adapters preserve canonical identifiers and status semantics without requiring a first-class `temporal` dashboard source or a worker runtime change.
- Operator-facing compatibility payloads expose `staleState`, `degradedCount`, and `refreshedAt`, and update responses expose a refresh envelope suitable for row patching plus background refetch.

## Failure conditions

The feature is not complete if any of the following remain true:

- The diff is documentation-only.
- Automated validation does not cover the mapped `DOC-REQ-*` contract.
- Compatibility layers invent identifiers, status rules, ordering, or pagination semantics that differ from the canonical Temporal-backed contract.
