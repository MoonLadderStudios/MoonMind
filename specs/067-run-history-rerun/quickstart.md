# Quickstart: Run History and Rerun Semantics

## 1. Confirm runtime-mode scope

- Read `specs/048-run-history-rerun/spec.md`.
- Treat this feature as runtime implementation mode: production code plus automated validation tests are required.
- Do not treat docs/spec edits alone as completion.

## 2. Review planned implementation surfaces

- Temporal lifecycle runtime:
  - `moonmind/workflows/temporal/service.py`
  - `moonmind/schemas/temporal_models.py`
- Execution and artifact APIs:
  - `api_service/api/routers/executions.py`
  - `api_service/api/routers/temporal_artifacts.py`
- Task dashboard compatibility/runtime config:
  - `api_service/api/routers/task_dashboard.py`
  - `api_service/api/routers/task_dashboard_view_model.py`
  - `api_service/static/task_dashboard/dashboard.js`
- Validation suites:
  - `tests/unit/workflows/temporal/test_temporal_service.py`
  - `tests/unit/api/routers/test_executions.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/contract/test_temporal_execution_api.py`
  - `tests/unit/api/routers/test_temporal_artifacts.py`
  - `tests/task_dashboard/test_temporal_run_history.js`

## 3. Validate logical identity and rerun behavior

1. Create a `MoonMind.Run` execution through `/api/executions`.
2. Capture the returned `workflowId`, `taskId`, `runId`, and `temporalRunId`.
3. Confirm:
   - `taskId == workflowId`
   - `latestRunView == true`
   - `runId == temporalRunId`
4. Submit `POST /api/executions/{workflowId}/update` with `updateName="RequestRerun"`.
5. Re-fetch `/api/executions/{workflowId}` and confirm:
   - `workflowId` is unchanged
   - `taskId` is unchanged
   - `runId` changed
   - `continueAsNewCause == manual_rerun`
   - `startedAt` is unchanged

## 4. Validate latest-run artifact resolution

1. Load detail through `/api/executions/{workflowId}`.
2. Read the returned `namespace` and `temporalRunId`.
3. Fetch `/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`.
4. Trigger rerun or another Continue-As-New transition.
5. Repeat the detail fetch and confirm the artifact request now uses the new `temporalRunId` while the task/detail route remains `/tasks/{workflowId}`.

## 5. Validate list/detail compatibility semantics

- Confirm task/dashboard routes continue to use `/tasks/{taskId}` for Temporal-backed work.
- Confirm Temporal-backed task rows use stable logical row identity based on `workflowId`/`taskId`, not `runId`.
- Confirm rerun or lifecycle rollover refreshes current run metadata on the same logical row rather than creating a sibling row.
- Confirm current run metadata may be displayed in detail, but v1 does not require choosing historical runs.

## 6. Validate negative and distinction paths

- Terminal rerun:
  - Move an execution to a terminal state.
  - Submit `RequestRerun`.
  - Confirm the response is non-applied and the execution does not silently restart.
- Automatic Continue-As-New:
  - Trigger threshold- or reconfiguration-driven Continue-As-New.
  - Confirm the logical identity remains stable but `continueAsNewCause` is not `manual_rerun`.

## 7. Run repository-standard unit tests

```bash
./tools/test_unit.sh
```

Notes:

- This repository requires `./tools/test_unit.sh` for unit acceptance.
- Do not replace it with direct `pytest`.
- In WSL, the script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.

## 8. Runtime scope validation gates

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope checks pass with backend and dashboard implementation work plus automated validation coverage.

## 9. Validation record

- 2026-03-06: `./tools/test_unit.sh` passed after adding the Temporal dashboard regression test to the standard JS runner.
- 2026-03-06: `node tests/task_dashboard/test_temporal_run_history.js` passed.
- 2026-03-06: `python -m pytest -q tests/contract/test_temporal_execution_api.py` passed.
- 2026-03-06: `python -m pytest -q tests/contract/test_temporal_artifact_api.py` passed.
- 2026-03-06: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` passed.
- 2026-03-06: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` passed.

## 10. Planning artifact completion gate

Before running `/agentkit.tasks`, ensure these artifacts exist:

- `specs/048-run-history-rerun/plan.md`
- `specs/048-run-history-rerun/research.md`
- `specs/048-run-history-rerun/data-model.md`
- `specs/048-run-history-rerun/quickstart.md`
- `specs/048-run-history-rerun/contracts/temporal-run-history-rerun.openapi.yaml`
- `specs/048-run-history-rerun/contracts/requirements-traceability.md`
