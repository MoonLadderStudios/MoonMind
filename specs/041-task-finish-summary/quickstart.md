# Quickstart: Task Finish Summary System

## 1. Confirm runtime-mode scope

- Read `specs/041-task-finish-summary/spec.md` and verify `Implementation Intent` is runtime implementation.
- Confirm this feature requires production code + tests (docs/spec-only output is not acceptable).
- Keep runtime-vs-docs behavior aligned by treating this feature as runtime mode end-to-end.

## 2. Review planned implementation surfaces

- Worker finalization/classification: `moonmind/agents/codex_worker/worker.py`
- Queue persistence and schemas: `moonmind/workflows/agent_queue/{models,repositories,service}.py`, `moonmind/schemas/agent_queue_models.py`
- API routes: `api_service/api/routers/agent_queue.py`, `api_service/api/routers/task_proposals.py`
- Migration: `api_service/migrations/versions/202602240001_task_finish_summary.py`
- Dashboard UI: `api_service/static/task_dashboard/dashboard.js`
- Tests: `tests/unit/agents/codex_worker/test_worker.py`, `tests/unit/api/routers/test_agent_queue.py`, `tests/unit/api/routers/test_task_proposals.py`, `tests/task_dashboard/test_queue_layouts.js`

## 3. Implement finish summary behavior

- Add deterministic finish outcome classification (`PUBLISHED_PR`, `PUBLISHED_BRANCH`, `NO_CHANGES`, `PUBLISH_DISABLED`, `FAILED`, `CANCELLED`).
- Build and redact finish summary payload before persistence/output.
- Persist finish metadata on success/failure/cancel-ack terminal transitions.
- Emit `reports/run_summary.json` best effort and optional compact failure artifact.

## 4. Implement API/UI exposure

- Ensure queue list returns outcome code/stage/reason and omits heavy `finishSummary` by default.
- Ensure queue detail returns `finishSummary` and supports `/jobs/{id}/finish-summary` retrieval.
- Add dashboard outcome badge and detail finish summary panel.
- Support proposals deep-link filtering with `originSource=queue&originId=<jobId>`.

## 5. Validate with repository-standard test command

Run the full validation gate:

```bash
./tools/test_unit.sh
```

Notes:
- In WSL, this command automatically delegates to `./tools/test_unit_docker.sh` unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set.
- Do not replace this with direct `pytest` invocation for acceptance.

## 6. Manual smoke checklist (post-test)

- Queue list shows outcome badge with stage/reason context for terminal jobs.
- Queue detail shows finish summary outcome, publish summary, and proposals summary.
- Proposals deep link from queue detail opens filtered list for originating `originId`.
- Representative terminal runs each classify to expected outcome code.

## 7. Planning artifact completion gate

Ensure these files are present and coherent before moving to `/speckit.tasks`:

- `specs/041-task-finish-summary/plan.md`
- `specs/041-task-finish-summary/research.md`
- `specs/041-task-finish-summary/data-model.md`
- `specs/041-task-finish-summary/quickstart.md`
- `specs/041-task-finish-summary/contracts/task-finish-summary.openapi.yaml`
- `specs/041-task-finish-summary/contracts/requirements-traceability.md`

## 8. Implementation and validation evidence (2026-02-24)

- Checklist gate status (`checklists/requirements.md`): `total=16 completed=16 incomplete=0` (PASS).
- Unit/runtime validation command: `./tools/test_unit.sh`.
- Unit/runtime validation result: `781 passed, 288 warnings, 8 subtests passed in 107.70s (0:01:47)`.
- Runtime tasks scope gate command: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`.
- Runtime tasks scope gate result: `Scope validation passed: tasks check (runtime tasks=18, validation tasks=10).`
- Runtime diff scope gate command: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`.
- Runtime diff scope gate result: `Scope validation passed: diff check (runtime files=10, test files=4).`
- Queue list smoke evidence: `tests/unit/api/routers/test_agent_queue.py::test_list_jobs_omits_finish_summary_by_default`.
- Queue detail finish-summary smoke evidence: `tests/unit/api/routers/test_agent_queue.py::test_get_job_finish_summary_returns_json_payload`.
- Proposals deep-link smoke evidence: `tests/unit/api/routers/test_task_proposals.py::test_list_proposals_supports_filters`.
- Dashboard outcome rendering smoke evidence: `tests/task_dashboard/test_queue_layouts.js`.
- DOC-REQ deterministic coverage audit result: `rows=19 coverage=PASS` (one implementation task and one validation task per `DOC-REQ-*` row).
