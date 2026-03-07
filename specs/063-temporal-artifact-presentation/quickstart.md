# Quickstart: Temporal Artifact Presentation Contract

## 1. Confirm selected orchestration mode

- This feature is **runtime implementation mode**.
- Required deliverables include production runtime code changes and automated validation tests.
- Docs-only updates are not sufficient for completion.

Docs-mode note for consistency:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode docs
```

Expected result: docs mode may skip runtime scope enforcement, but this feature still requires runtime code plus tests.

## 2. Review implementation surfaces

- Runtime/dashboard surfaces:
  - `api_service/api/routers/task_dashboard.py`
  - `api_service/api/routers/task_dashboard_view_model.py`
  - `api_service/api/routers/temporal_artifacts.py`
  - `api_service/static/task_dashboard/dashboard.js`
  - `api_service/templates/task_dashboard.html`
- Backing Temporal APIs:
  - `api_service/api/routers/executions.py`
  - `moonmind/schemas/temporal_artifact_models.py`
- Validation:
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/unit/api/routers/test_temporal_artifacts.py`
  - `tests/unit/specs/test_doc_req_traceability.py`
  - `tests/task_dashboard/test_temporal_detail_runtime.js`

## 3. Validate route and runtime config behavior

Confirm the dashboard runtime exposes Temporal endpoints and accepts canonical Temporal task IDs:

- `sources.temporal.detail` -> `/api/executions/{workflowId}`
- `sources.temporal.artifacts` -> `/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
- `/tasks/mm:<workflow-id>` is accepted by the route shell
- Temporal remains a dashboard source, not a worker runtime selector value

## 4. Validate Temporal detail fetch ordering

For a Temporal-backed task detail load:

1. Load `/tasks/:taskId` where `taskId == workflowId`.
2. Fetch execution detail from `/api/executions/{workflowId}`.
3. Derive `namespace` and the latest `temporalRunId` from that detail response.
4. Fetch `/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`.

Expected result: the artifact list always follows the latest run returned by execution detail, not stale row cache data.

## 5. Validate artifact presentation behavior

Using an execution with preview-capable and raw-restricted artifacts:

- Preview-capable artifacts expose `Open preview` first.
- Raw-restricted artifacts do not default to inline raw access.
- Raw download is shown only when policy metadata permits it.
- The default detail view shows artifacts from the latest run only.
- The page shows summary/timeline/artifacts rather than raw Temporal history JSON.

## 6. Run repository-standard validation

```bash
./tools/test_unit.sh
```

Expected coverage:

- Python unit tests for route shell and runtime config.
- Python unit tests for artifact metadata and access-policy wiring.
- Node dashboard runtime tests for latest-run resolution and artifact presentation helpers.
- `DOC-REQ-*` traceability validation for the feature contract.

## 7. Run runtime scope guards

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected result: runtime scope guards confirm the feature includes production runtime code paths and validation tests.

## 8. Planning artifact completion gate

Before `/speckit.tasks`, ensure these artifacts exist:

- `specs/047-temporal-artifact-presentation/plan.md`
- `specs/047-temporal-artifact-presentation/research.md`
- `specs/047-temporal-artifact-presentation/data-model.md`
- `specs/047-temporal-artifact-presentation/quickstart.md`
- `specs/047-temporal-artifact-presentation/contracts/temporal-artifact-presentation-contract.md`
- `specs/047-temporal-artifact-presentation/contracts/requirements-traceability.md`
