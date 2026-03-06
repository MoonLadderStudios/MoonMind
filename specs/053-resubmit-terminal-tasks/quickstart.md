# Quickstart: Resubmit Terminal Tasks

## 1. Confirm runtime-mode scope

- Read `specs/043-resubmit-terminal-tasks/spec.md` and confirm `Implementation Intent` is runtime implementation.
- Treat this feature as runtime mode end-to-end: production code + validation tests are required.
- Do not treat docs/spec updates alone as completion.

## 2. Review implementation surfaces

- Queue API routes: `api_service/api/routers/agent_queue.py`
- Queue service behavior: `moonmind/workflows/agent_queue/service.py`
- Queue request schemas: `moonmind/schemas/agent_queue_models.py`
- Dashboard runtime config: `api_service/api/routers/task_dashboard_view_model.py`
- Dashboard prefill/submit UI: `api_service/static/task_dashboard/dashboard.js`
- Documentation contracts:
  - `docs/TaskEditingSystem.md`
  - `docs/TaskQueueSystem.md`
  - `docs/TaskUiArchitecture.md`
- Coverage suites:
  - `tests/unit/workflows/agent_queue/test_service_resubmit.py`
  - `tests/unit/api/routers/test_agent_queue.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
  - `tests/task_dashboard/test_submit_runtime.js`

## 3. Implement backend resubmit behavior

- Add/confirm `POST /api/queue/jobs/{jobId}/resubmit` router endpoint using authenticated user context.
- Ensure request envelope matches create/update fields with optional `note`.
- In service layer, enforce owner authorization parity and source eligibility (`task` + failed/cancelled).
- Normalize payload with existing task normalization path and reject attachment mutation fields.
- Create a new queued job, append source/new lineage events, and commit transactionally.

## 4. Implement dashboard create/edit/resubmit mode behavior

- Reuse `/tasks/queue/new?editJobId=<jobId>` prefill route and resolve mode from source job detail.
- Show `Resubmit` action on eligible terminal task detail pages.
- In resubmit mode:
  - submit to `sources.queue.resubmit`
  - keep cancel navigation to source detail
  - redirect to new detail with `resubmittedFrom` success notice
  - display explicit attachment no-copy guidance
- Keep existing queued edit behavior unchanged.

## 5. Keep docs + runtime config aligned

- Ensure dashboard runtime config includes `sources.queue.resubmit`.
- Update docs for terminal-task resubmit behavior, endpoint contract, and prefill mode semantics.
- Keep v1 attachment behavior explicit: old attachments are not copied automatically.

## 6. Run validation with repository-standard command

```bash
./tools/test_unit.sh
```

Notes:
- In WSL, this command delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not replace this acceptance step with direct `pytest`.

## 7. Runtime scope guard checks (implementation stage)

Run when implementation tasks are generated/executed:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected outcome: runtime/task checks pass with production runtime files and validation tests represented.

## 8. Planning artifact completion gate

Before moving to `/speckit.tasks`, ensure these artifacts are present:

- `specs/043-resubmit-terminal-tasks/plan.md`
- `specs/043-resubmit-terminal-tasks/research.md`
- `specs/043-resubmit-terminal-tasks/data-model.md`
- `specs/043-resubmit-terminal-tasks/quickstart.md`
- `specs/043-resubmit-terminal-tasks/contracts/task-resubmit.openapi.yaml`
- `specs/043-resubmit-terminal-tasks/contracts/requirements-traceability.md`
