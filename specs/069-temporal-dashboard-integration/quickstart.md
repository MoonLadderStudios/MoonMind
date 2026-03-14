# Quickstart: Temporal Dashboard Integration

## 1. Confirm selected orchestration mode

- This feature is **runtime implementation mode**.
- Required deliverables include production runtime code changes and validation tests.
- Docs-only updates are not sufficient for completion.

Docs-mode note for orchestration consistency:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode docs
```

Expected result: docs mode may skip runtime scope checks, but this feature remains runtime-gated.

## 2. Review planned implementation surfaces

- Runtime config and settings:
  - `moonmind/config/settings.py`
  - `api_service/api/routers/task_dashboard_view_model.py`
- Dashboard routing and API integration:
  - `api_service/api/routers/task_dashboard.py`
  - `api_service/api/routers/executions.py`
  - `api_service/api/routers/temporal_artifacts.py`
- Dashboard client:
  - `api_service/static/task_dashboard/dashboard.js`
  - `api_service/templates/task_dashboard.html`
- Validation:
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/unit/api/routers/test_executions.py`
  - `tests/task_dashboard/test_queue_layouts.js`
  - `tests/task_dashboard/test_temporal_dashboard.js`
  - `tests/contract/test_temporal_execution_api.py`
  - `tests/contract/test_temporal_artifact_api.py`
  - `tests/e2e/test_task_create_submit_browser.py`

## 3. Start the local stack with Temporal-backed APIs available

```bash
docker compose up -d api-db temporal-db temporal temporal-namespace-init api celery-worker
```

Expected baseline:

- The dashboard shell is served by the API service.
- Temporal lifecycle endpoints are reachable only through MoonMind REST APIs.
- Artifact listing/download paths for executions are available through MoonMind-owned endpoints.

## 4. Validate Temporal read integration

1. Open `/tasks/list` with Temporal dashboard read flags enabled.
2. Confirm mixed-source mode shows Temporal-backed rows as informational convenience rows.
3. Pin `source=temporal` and verify:
   - `workflowType`, `state`, `entry`, `repo`, and allowed owner filters pass through,
   - `count` and `nextPageToken` match `/api/executions`,
   - Temporal rows link to `/tasks/{taskId}?source=temporal`.
4. Open `/tasks/{taskId}` for a Temporal-backed row and verify:
   - server-side source resolution succeeds,
   - detail loads execution first, then latest-run artifacts,
   - the page stays task-oriented while still showing raw/debug metadata when enabled.

## 5. Validate action gating and operator copy

1. Enable the Temporal actions feature flag.
2. Open Temporal-backed detail views across multiple lifecycle states.
3. Verify:
   - only valid actions render for the current state,
   - copy uses task language (`Task title`, `Pause task`, `Rerun`),
   - post-action refresh reflects updated state without requiring raw Temporal history views.

## 6. Validate submit behavior without exposing a Temporal runtime

1. Open `/tasks/new`.
2. Confirm the runtime picker does **not** include `temporal`.
3. With submit routing enabled for eligible flows:
   - submit a run-shaped request routed to Temporal,
   - submit a manifest-oriented request routed to Temporal,
   - confirm success redirects to `/tasks/{taskId}?source=temporal`.
4. Verify artifact-first behavior for large inputs where required.

## 7. Run repository-standard unit and dashboard validation

```bash
./tools/test_unit.sh
```

Notes:

- Use `./tools/test_unit.sh` as the required unit-test entrypoint.
- Do not replace the unit-test path with direct `pytest`.

## 8. Run targeted contract and browser validation

```bash
python -m pytest \
  tests/contract/test_temporal_execution_api.py \
  tests/contract/test_temporal_artifact_api.py \
  tests/e2e/test_task_create_submit_browser.py
```

Notes:

- These suites are not executed by `./tools/test_unit.sh` today.
- Run them with the same project interpreter/environment used for implementation work.

## 9. Run runtime scope guards

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected result: runtime scope gates pass only when production runtime changes and validation tests are both represented.

## 10. Planning artifact completion gate

Before `/agentkit.tasks`, ensure these artifacts exist:

- `specs/048-temporal-dashboard-integration/plan.md`
- `specs/048-temporal-dashboard-integration/research.md`
- `specs/048-temporal-dashboard-integration/data-model.md`
- `specs/048-temporal-dashboard-integration/quickstart.md`
- `specs/048-temporal-dashboard-integration/contracts/temporal-dashboard-view-model.md`
- `specs/048-temporal-dashboard-integration/contracts/requirements-traceability.md`
