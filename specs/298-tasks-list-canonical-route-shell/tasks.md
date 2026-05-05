# Tasks: Tasks List Canonical Route and Shell

**Input**: `specs/298-tasks-list-canonical-route-shell/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/tasks-list-route-shell.md`, `quickstart.md`

**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`
**Targeted Backend Command**: `pytest tests/unit/api/routers/test_task_dashboard.py -q`
**Targeted Frontend Command**: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`
**Integration Test Command**: Not required for this already-covered route/shell story unless focused unit/UI evidence fails; browser smoke remains gated by `RUN_E2E_TESTS=1`.

## Source Traceability Summary

- `MM-585` is preserved as the canonical Jira preset brief in `spec.md`.
- `DESIGN-REQ-001`: `/tasks/list` is the canonical Tasks List route.
- `DESIGN-REQ-002`: `/tasks` and `/tasks/tasks-list` redirect to `/tasks/list`.
- `DESIGN-REQ-003`: FastAPI hosts the shared Mission Control React/Vite shell.
- `DESIGN-REQ-004`: The server renders page key `tasks-list`, wide layout metadata, and dashboard configuration.
- `DESIGN-REQ-006`: The browser uses MoonMind APIs only and the shell preserves live updates, polling, disabled, page-size, pagination, control deck, and data slab surfaces.

## Story

As a MoonMind operator, I want `/tasks/list` to be the canonical Tasks List route with redirected legacy routes and a server-configured Mission Control shell so I always land on the supported task list experience.

**Independent Test**: Request `/tasks/list`, `/tasks`, and `/tasks/tasks-list`; inspect the server-rendered boot payload and layout metadata; and render the Tasks List React entrypoint to confirm one control deck, one data slab, live update controls, polling/disabled state, page-size/pagination surfaces, and MoonMind API requests.

## Task Phases

### Phase 1: Setup

- [X] T001 Create MoonSpec feature directory and preserve MM-585 Jira preset brief in `specs/298-tasks-list-canonical-route-shell/spec.md`
- [X] T002 Create planning, research, data model, route contract, quickstart, and checklist artifacts under `specs/298-tasks-list-canonical-route-shell/`

### Phase 2: Foundational Verification

- [X] T003 Inspect existing route implementation in `api_service/api/routers/task_dashboard.py` for `/tasks/list`, `/tasks`, `/tasks/tasks-list`, `tasks-list` page key, dashboard config, and wide data-panel layout. (FR-001, FR-002, FR-003, FR-004, FR-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T004 Inspect existing backend tests in `tests/unit/api/routers/test_task_dashboard.py` for canonical route rendering, redirect, dashboard config, and `dataWidePanel:true` coverage. (SC-001, SC-002)
- [X] T005 Inspect existing frontend implementation and tests in `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/tasks-list.test.tsx` for one control deck, one data slab, live updates, polling copy, disabled notice, page-size, pagination, and MoonMind API request coverage. (FR-006, FR-007, FR-008, FR-009, FR-010, SC-003, SC-004, SC-005, DESIGN-REQ-006)

### Phase 3: Story Implementation

- [X] T006 Confirm no production code changes are required because current implementation already satisfies FR-001 through FR-010 in `api_service/api/routers/task_dashboard.py` and `frontend/src/entrypoints/tasks-list.tsx`
- [X] T007 Confirm no new test code is required because current backend and frontend tests already satisfy SC-001 through SC-005 in `tests/unit/api/routers/test_task_dashboard.py` and `frontend/src/entrypoints/tasks-list.test.tsx`

### Phase 4: Validation

- [X] T008 Run targeted backend route validation: `pytest tests/unit/api/routers/test_task_dashboard.py -q`
- [X] T009 Run targeted frontend shell validation: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` or direct local Vitest binary if npm cannot resolve `vitest`
- [X] T010 Run final unit wrapper: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`
- [X] T011 Run final `/moonspec-verify` equivalent and record verdict in `specs/298-tasks-list-canonical-route-shell/verification.md`

## Verification Notes

- Targeted backend route validation passed on 2026-05-05: `pytest tests/unit/api/routers/test_task_dashboard.py -q` -> 44 passed.
- Direct npm frontend command was blocked in this managed shell because `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` could not resolve `vitest` even after wrapper dependency preparation. The equivalent local binary command passed on 2026-05-05: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` -> 1 file and 19 tests passed.
- Final unit wrapper passed on 2026-05-05: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` -> Python 4318 passed, 1 xpassed, 16 subtests passed; focused UI 1 file and 19 tests passed.
- Traceability preserved for Jira issue `MM-585` and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006.

## Dependencies

- T003 through T005 depend on T001 and T002.
- T006 and T007 depend on T003 through T005.
- T008 through T011 depend on T006 and T007.

## Implementation Strategy

Use verification-first completion because the MM-585 behavior is already implemented and covered by focused tests. Do not regenerate existing route code or UI code. If any validation command fails, add the smallest failing test or production fix needed inside `api_service/api/routers/task_dashboard.py`, `tests/unit/api/routers/test_task_dashboard.py`, `frontend/src/entrypoints/tasks-list.tsx`, or `frontend/src/entrypoints/tasks-list.test.tsx`, then rerun the focused commands before final verification.
