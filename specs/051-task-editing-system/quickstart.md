# Quickstart: Task Editing System

## 1. Confirm runtime-mode scope

- Read `specs/042-task-editing-system/spec.md` and verify `Implementation Intent` is runtime implementation.
- Treat this feature as runtime mode end-to-end: production code + validation tests are required.
- Do not treat docs/spec updates alone as completion for this feature.

## 2. Review implementation surfaces

- Queue API + mapping: `api_service/api/routers/agent_queue.py`
- Queue service/repository logic: `moonmind/workflows/agent_queue/{service,repositories}.py`
- Queue schemas: `moonmind/schemas/agent_queue_models.py`
- Dashboard runtime config: `api_service/api/routers/task_dashboard_view_model.py`
- Dashboard edit flow/UI: `api_service/static/task_dashboard/dashboard.js`
- Coverage suites:
  - `tests/unit/workflows/agent_queue/test_service_update.py`
  - `tests/unit/api/routers/test_agent_queue.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/task_dashboard/test_submit_runtime.js`

## 3. Implement queued update backend behavior

- Ensure update contract mirrors create envelope with optional `expectedUpdatedAt` and `note`.
- Enforce editability invariants (`task`, queued, never-started) and owner authorization.
- Reuse task payload normalization/runtime-gate checks from create flow.
- Update mutable fields in place, refresh `updated_at`, append `Job updated` event payload.

## 4. Implement dashboard edit-mode behavior

- Use `/tasks/queue/new?editJobId=<jobId>` as edit route.
- Prefill form from job detail API and store `updatedAt` for optimistic concurrency.
- Submit via queue update endpoint template (`sources.queue.update`) with `expectedUpdatedAt` when present.
- Show actionable conflict/auth/validation messages and preserve cancel navigation to detail.

## 5. Keep docs + API surface aligned

- Ensure queue API/UI architecture docs include the queue update endpoint and edit-mode behavior.
- Keep explicit v1 non-goals intact (no running-job/orchestrator/attachment edits).

## 6. Run validation with repository-standard command

```bash
./tools/test_unit.sh
```

Notes:
- In WSL, this command delegates to `./tools/test_unit_docker.sh` by default unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not substitute direct `pytest` invocation for acceptance.

## 7. Runtime scope guard checks (implementation stage)

Run when implementation tasks are generated/executed:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime/task checks pass with production/runtime files and validation tests represented.

## 8. Planning artifact completion gate

Before moving to `/speckit.tasks`, ensure these artifacts are present:

- `specs/042-task-editing-system/plan.md`
- `specs/042-task-editing-system/research.md`
- `specs/042-task-editing-system/data-model.md`
- `specs/042-task-editing-system/quickstart.md`
- `specs/042-task-editing-system/contracts/task-editing.openapi.yaml`
- `specs/042-task-editing-system/contracts/requirements-traceability.md`
