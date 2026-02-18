# Tasks: Queue Live Logs + SSE

**Input**: Design documents from `/specs/023-queue-live-sse/`  
**Prerequisites**: `plan.md`, `spec.md`

**Tests**: Required for this feature (worker, API, and dashboard behavior).

## Phase 1: Implementation

- [X] T001 [US1] Add live-log worker config toggles and batching/throttling settings in `moonmind/agents/codex_worker/worker.py`.
- [X] T002 [US1] Stream subprocess stdout/stderr incrementally with callback hooks in `moonmind/agents/codex_worker/handlers.py`.
- [X] T003 [US1] Emit redacted queue log-chunk events (`payload.kind=log`) from worker command execution paths in `moonmind/agents/codex_worker/worker.py`.
- [X] T004 [US2] Add SSE endpoint `GET /api/queue/jobs/{job_id}/events/stream` in `api_service/api/routers/agent_queue.py`.
- [X] T005 [US2] Expose queue events stream endpoint in dashboard runtime config via `api_service/api/routers/task_dashboard_view_model.py`.
- [X] T006 [US3] Implement queue detail Live Output panel + filter/follow/copy controls in `api_service/static/task_dashboard/dashboard.js`.
- [X] T007 [US3] Add required styles for live output UI in `api_service/static/task_dashboard/dashboard.css`.

## Phase 2: Validation

- [X] T008 [US1] Add/adjust worker unit tests for incremental log callback emission and redaction in `tests/unit/agents/codex_worker/test_handlers.py` and `tests/unit/agents/codex_worker/test_worker.py`.
- [X] T009 [US2] Add router unit tests for SSE stream behavior in `tests/unit/api/routers/test_agent_queue.py`.
- [X] T010 [US3] Add/update dashboard config/UI tests in `tests/unit/api/routers/test_task_dashboard_view_model.py`.
- [X] T011 [US1] Run validation suite using `./tools/test_unit.sh`.
- [X] T012 [US1] Run scope checks with `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main`.
