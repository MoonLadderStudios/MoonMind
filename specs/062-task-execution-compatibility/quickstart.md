# Quickstart: Task Execution Compatibility

## 1. Confirm runtime-mode scope

- Read `specs/047-task-execution-compatibility/spec.md` and verify the feature intent is runtime implementation.
- Treat this feature as runtime mode end-to-end: production code changes and validation tests are both required.
- Do not treat docs/spec updates alone as completion.

## 2. Review implementation surfaces

- Temporal execution APIs and schemas:
  - `api_service/api/routers/executions.py`
  - `moonmind/workflows/temporal/service.py`
  - `moonmind/schemas/temporal_models.py`
- Task dashboard shell and runtime config:
  - `api_service/api/routers/task_dashboard.py`
  - `api_service/api/routers/task_dashboard_view_model.py`
  - `api_service/static/task_dashboard/dashboard.js`
- Persistence and migrations:
  - `api_service/db/models.py`
  - `api_service/migrations/versions/`
- Planned compatibility surfaces:
  - `api_service/api/routers/task_compatibility.py`
  - `moonmind/schemas/task_compatibility_models.py`
  - `moonmind/workflows/tasks/compatibility.py`
  - `moonmind/workflows/tasks/source_mapping.py`

## 3. Implement the persisted task source index

- Add the `task_source_mappings` persistence model and migration.
- Backfill/update mappings on create and on compatibility reads for legacy rows.
- Switch `/api/tasks/{taskId}/resolution` and unified detail routing to consult the mapping first.

## 4. Implement compatibility list/detail APIs

- Add `GET /api/tasks/list` for normalized task rows with source filters, mixed-source cursor handling, and `countMode`.
- Add `GET /api/tasks/{taskId}` for unified detail payloads.
- Keep `GET /api/tasks` unchanged as the queue alias.

## 5. Normalize Temporal-backed payloads and actions

- Preserve `taskId == workflowId`, `temporalRunId` as detail/debug-only, and stable rerun identity across Continue-As-New.
- Expose normalized `status` while retaining `rawState`, `temporalStatus`, and `closeStatus`.
- Add allowlisted `searchAttributes`, `memo`, `actions`, and `debug` fields.
- Keep create/update/signal/cancel task actions mapped onto the existing `/api/executions*` endpoints.

## 6. Update dashboard integration

- Point `/tasks/list` data loading at `GET /api/tasks/list`.
- Point `/tasks/{taskId}` detail loading at `GET /api/tasks/{taskId}`.
- Preserve `temporal` as a task source and render Temporal manifest executions with `entry=manifest`.
- Ensure queue-backed manifests stay on queue/manifests views until runtime migration occurs.

## 7. Add validation coverage

- Contract tests:
  - `tests/contract/test_task_compatibility_api.py`
  - `tests/contract/test_temporal_execution_api.py`
- Unit tests:
  - `tests/unit/workflows/tasks/test_task_compatibility_service.py`
  - `tests/unit/api/routers/test_task_compatibility.py`
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
- Dashboard runtime tests:
  - `tests/task_dashboard/test_queue_layouts.js`

## 8. Run required validation commands

```bash
./tools/test_unit.sh
```

Notes:
- In WSL, this script delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not replace this acceptance command with direct `pytest`.

Run the targeted contract suites explicitly:

```bash
PYTHON_BIN=.venv/bin/python
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python || command -v python3)"
fi
"$PYTHON_BIN" -m pytest \
  tests/contract/test_task_compatibility_api.py \
  tests/contract/test_temporal_execution_api.py
```

Notes:
- This contract command complements `./tools/test_unit.sh`; it does not replace it.
- Use the same repository environment or virtualenv that you use for the unit/dashboard validation pass.

## 9. Run runtime scope guards

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime scope checks confirm both production runtime code and validation tasks are present.

## 10. Planning completion gate

Before running `/agentkit.tasks`, ensure these artifacts exist:

- `specs/047-task-execution-compatibility/plan.md`
- `specs/047-task-execution-compatibility/research.md`
- `specs/047-task-execution-compatibility/data-model.md`
- `specs/047-task-execution-compatibility/quickstart.md`
- `specs/047-task-execution-compatibility/contracts/task-execution-compatibility.openapi.yaml`
- `specs/047-task-execution-compatibility/contracts/requirements-traceability.md`
