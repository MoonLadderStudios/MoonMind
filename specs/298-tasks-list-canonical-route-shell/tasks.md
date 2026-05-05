# Tasks: Tasks List Canonical Route and Shell

**Input**: `specs/298-tasks-list-canonical-route-shell/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/tasks-list-route-shell.md`, `quickstart.md`

**Unit Test Command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`
**Targeted Backend Command**: `pytest tests/unit/api/routers/test_task_dashboard.py -q`
**Targeted Frontend Command**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`
**Integration Test Command**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`; optional browser smoke remains gated by `RUN_E2E_TESTS=1`.

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

## Unit Test Plan

- Backend route unit evidence lives in `tests/unit/api/routers/test_task_dashboard.py` and covers `/tasks/list`, legacy redirects, dashboard configuration, and wide data-panel metadata. Because the plan classifies these rows as `implemented_verified`, no new red-first backend test file is generated; rerunning the existing focused unit suite preserves the evidence.
- Final wrapper evidence uses `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` so the required Python unit suite and focused UI suite run through the repository test runner.

## Integration Test Plan

- Integration-style UI evidence lives in `frontend/src/entrypoints/tasks-list.test.tsx` and covers the React shell composition, live updates, polling copy, disabled notice, page-size, pagination, and MoonMind API request behavior.
- The managed-shell command is `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`. `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` is the developer wrapper when npm resolves local binaries.
- Optional Playwright smoke in `tests/e2e/test_mission_control_react_mount_browser.py` remains gated by `RUN_E2E_TESTS=1` and is not required for this already-covered route/shell story.

## Task Phases

### Phase 1: Setup

- [X] T001 Create MoonSpec feature directory and preserve MM-585 Jira preset brief in `specs/298-tasks-list-canonical-route-shell/spec.md`
- [X] T002 Create planning, research, data model, route contract, quickstart, and checklist artifacts under `specs/298-tasks-list-canonical-route-shell/`

### Phase 2: Test-First Coverage Inventory

- [X] T003 [P] Confirm red-first backend unit coverage is already represented by existing focused route tests in `tests/unit/api/routers/test_task_dashboard.py` for `/tasks/list`, `/tasks`, `/tasks/tasks-list`, dashboard config, and `dataWidePanel:true`. (FR-001, FR-002, FR-003, FR-004, FR-005, SC-001, SC-002, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T004 [P] Confirm red-first integration-style UI coverage is already represented by existing render tests in `frontend/src/entrypoints/tasks-list.test.tsx` for one control deck, one data slab, live updates, polling copy, disabled notice, page-size, pagination, and MoonMind API request behavior. (FR-006, FR-007, FR-008, FR-009, FR-010, SC-003, SC-004, SC-005, DESIGN-REQ-006)
- [X] T005 Confirm no additional failing test tasks are generated because every in-scope FR, SC, and DESIGN-REQ row is `implemented_verified` in `specs/298-tasks-list-canonical-route-shell/plan.md`; if later verification fails, add the smallest failing unit or integration test before any production fix. (FR-001 through FR-011, SC-001 through SC-006)

### Phase 3: Story Implementation

- [X] T006 Inspect existing route implementation in `api_service/api/routers/task_dashboard.py` and confirm it satisfies `/tasks/list`, `/tasks`, `/tasks/tasks-list`, `tasks-list` page key, dashboard config, and wide data-panel layout without production changes. (FR-001, FR-002, FR-003, FR-004, FR-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004)
- [X] T007 Inspect existing frontend implementation in `frontend/src/entrypoints/tasks-list.tsx` and confirm it satisfies one control deck, one data slab, live updates, polling copy, disabled notice, page-size, pagination, and MoonMind API request behavior without production changes. (FR-006, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-006)
- [X] T008 Confirm traceability implementation is complete by preserving MM-585 in `specs/298-tasks-list-canonical-route-shell/spec.md`, `plan.md`, `tasks.md`, and `verification.md`. (FR-011, SC-006)

### Phase 4: Story Validation

- [X] T009 Run targeted backend route validation: `pytest tests/unit/api/routers/test_task_dashboard.py -q`
- [X] T010 Run targeted frontend shell validation: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`
- [X] T011 Run final unit wrapper: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`

### Final Phase: Polish And Verification

- [X] T012 Run MoonSpec alignment and record result in `specs/298-tasks-list-canonical-route-shell/moonspec_align_report.md`
- [X] T013 Run final `/moonspec-verify` and record verdict in `specs/298-tasks-list-canonical-route-shell/verification.md`

## Verification Notes

- Targeted backend route validation passed on 2026-05-05: `pytest tests/unit/api/routers/test_task_dashboard.py -q` -> 44 passed.
- Direct npm frontend command was blocked in this managed shell because `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` could not resolve `vitest` even after wrapper dependency preparation. The equivalent local binary command passed on 2026-05-05: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` -> 1 file and 19 tests passed.
- Final unit wrapper passed on 2026-05-05: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` -> Python 4318 passed, 1 xpassed, 16 subtests passed; focused UI 1 file and 19 tests passed.
- Traceability preserved for Jira issue `MM-585` and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006.

## Dependencies

- T003 through T005 depend on T001 and T002.
- T006 through T008 depend on T003 through T005.
- T009 through T011 depend on T006 through T008.
- T012 and T013 depend on story validation passing.

## Implementation Strategy

Use verification-first completion because the MM-585 behavior is already implemented and covered by focused tests. All in-scope rows are `implemented_verified`, so red-first work is represented by confirming existing unit and integration-style tests before implementation inspection. Do not regenerate existing route code or UI code. If any validation command fails, add the smallest failing unit or integration test first, then apply the smallest production fix needed inside `api_service/api/routers/task_dashboard.py`, `tests/unit/api/routers/test_task_dashboard.py`, `frontend/src/entrypoints/tasks-list.tsx`, or `frontend/src/entrypoints/tasks-list.test.tsx`, then rerun focused validation and `/moonspec-verify`.
