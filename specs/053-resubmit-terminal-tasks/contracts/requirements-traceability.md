# Requirements Traceability: Resubmit Terminal Tasks

| DOC-REQ ID | Source Reference | Mapped FR(s) | Implemented Surfaces | Validation Evidence |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/TaskEditingSystem.md` §12 terminal-task resubmit mode | FR-002, FR-004, FR-005, FR-011, FR-015, FR-017 | `api_service/static/task_dashboard/dashboard.js`, `moonmind/workflows/agent_queue/service.py`, `docs/TaskEditingSystem.md` | `tests/unit/workflows/agent_queue/test_service_resubmit.py`, `tests/task_dashboard/test_submit_runtime.js`, plus documentation update in `docs/TaskEditingSystem.md` |
| DOC-REQ-002 | `docs/TaskQueueSystem.md` §3.1 queue API surface | FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-017 | `moonmind/schemas/agent_queue_models.py`, `api_service/api/routers/agent_queue.py`, `moonmind/workflows/agent_queue/service.py`, `docs/TaskQueueSystem.md` | `tests/unit/api/routers/test_agent_queue.py`, `tests/unit/workflows/agent_queue/test_service_resubmit.py`, plus documentation update in `docs/TaskQueueSystem.md` |
| DOC-REQ-003 | `docs/TaskUiArchitecture.md` §5.4 queue prefill modes | FR-003, FR-004, FR-012, FR-013, FR-014, FR-017 | `api_service/static/task_dashboard/dashboard.js`, `api_service/api/routers/task_dashboard_view_model.py`, `docs/TaskUiArchitecture.md` | `tests/task_dashboard/test_submit_runtime.js`, `tests/unit/api/routers/test_task_dashboard_view_model.py`, plus documentation update in `docs/TaskUiArchitecture.md` |

## Validation Evidence

- `./tools/test_unit.sh` passed (`909 passed, 8 subtests passed`).
- `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` passed.
- `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime` passed.

## Runtime Mode Alignment Gate

- `spec.md` sets `Implementation Intent: Runtime implementation`; planning and tasks must include production runtime code changes plus validation tests.
- FR-018 and SC-006 are satisfied only when implementation/test surfaces (not docs/spec-only changes) are represented and executed.

## Coverage Gate

- Row count matches source requirements: `3` `DOC-REQ-*` rows.
- Every row maps to at least one FR, one implementation surface, and one validation strategy.
- Runtime-vs-docs alignment is explicit via the Runtime Mode Alignment Gate and remains a release blocker if unmet.
