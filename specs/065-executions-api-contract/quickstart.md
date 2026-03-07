# Quickstart: Executions API Contract Runtime Delivery

## 1. Confirm runtime-mode scope

- Read `specs/048-executions-api-contract/spec.md`.
- Treat this feature as runtime implementation mode: production code plus automated validation tests are required.
- Do not treat docs/spec edits alone as completion.

## 2. Review implementation surfaces

- Runtime/service/model:
  - `moonmind/workflows/temporal/service.py`
  - `moonmind/schemas/temporal_models.py`
  - `api_service/db/models.py`
  - `api_service/migrations/versions/202603050001_temporal_execution_lifecycle.py`
- API/router:
  - `api_service/api/routers/executions.py`
  - `api_service/main.py`
- Compatibility surfaces:
  - `api_service/api/routers/task_dashboard_view_model.py`
  - `api_service/static/task_dashboard/dashboard.js`
- Validation suites:
  - `tests/contract/test_temporal_execution_api.py`
  - `tests/unit/api/routers/test_executions.py`
  - `tests/unit/workflows/temporal/test_temporal_service.py`
  - `tests/unit/specs/test_doc_req_traceability.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py` as needed for migration bridge assertions

## 3. Validate the six documented execution routes

1. Create one `MoonMind.Run` execution and one `MoonMind.ManifestIngest` execution.
2. Verify create responses return:
   - `201 Created`
   - `workflowId`, `runId`, `workflowType`, `state`, `temporalStatus`
   - baseline `searchAttributes` and `memo`
3. Verify list behavior:
   - owner scoping for non-admin callers
   - admin-capable `ownerId` filtering
   - ordering by `updatedAt` descending then `workflowId` descending
   - opaque `nextPageToken`
   - `count` plus `countMode`
4. Verify describe behavior:
   - `200 OK` for visible executions
   - `404 execution_not_found` for missing or hidden executions
5. Verify update behavior:
   - `UpdateInputs`
   - `SetTitle`
   - `RequestRerun`
   - terminal execution returns `accepted=false`
6. Verify signal behavior:
   - `ExternalEvent`
   - `Approve`
   - `Pause`
   - `Resume`
   - invalid or terminal signals return `409 signal_rejected`
7. Verify cancel behavior:
   - graceful cancel -> `state=canceled`, `closeStatus=canceled`
   - forced termination -> `state=failed`, `closeStatus=terminated`
   - terminal cancel returns unchanged execution body

## 4. Verify migration compatibility invariants

- Confirm `workflowId` remains the canonical execution identifier in all `/api/executions` responses.
- Confirm direct execution responses do not expose `taskId`.
- Confirm task-oriented compatibility or dashboard adapter paths preserve `taskId == workflowId` where execution data is adapted for user-facing task surfaces.
- Confirm non-`exact` counts, if introduced in a future path, are not treated as authoritative totals.

## 5. Run repository-standard unit tests

Run the targeted DOC-REQ traceability gate before the full suite when you need to confirm the active feature mappings stay complete:

```bash
./tools/test_unit.sh tests/unit/specs/test_doc_req_traceability.py
```

Then run the repository-standard unit suite:

```bash
./tools/test_unit.sh
```

Validation results recorded on 2026-03-06:

- `./tools/test_unit.sh tests/contract/test_temporal_execution_api.py tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/specs/test_doc_req_traceability.py tests/unit/api/routers/test_task_dashboard_view_model.py` passed.
- `./tools/test_unit.sh` completed successfully in the current runtime implementation workspace.

Notes:

- This repository requires `./tools/test_unit.sh` for unit acceptance.
- Do not replace it with direct `pytest`.
- In WSL, the script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.

## 6. Optional integration validation path

Use the Docker-based integration path when validating broader workflow behavior:

```bash
docker compose -f docker-compose.test.yaml run --rm orchestrator-tests
```

## 7. Runtime scope validation gates

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope checks pass with production runtime code changes and automated validation coverage.

Validation results recorded on 2026-03-06:

- `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` passed with `runtime tasks=14, validation tasks=13`.
- `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` passed with `runtime files=2, test files=3`.

## 8. Planning artifact completion gate

Before running `/speckit.tasks`, ensure these artifacts exist:

- `specs/048-executions-api-contract/plan.md`
- `specs/048-executions-api-contract/research.md`
- `specs/048-executions-api-contract/data-model.md`
- `specs/048-executions-api-contract/quickstart.md`
- `specs/048-executions-api-contract/contracts/executions-api-contract.openapi.yaml`
- `specs/048-executions-api-contract/contracts/requirements-traceability.md`
