# Tasks: Mission Control Layout and Table Composition Patterns

**Input**: Design artifacts from `specs/214-mission-control-layout-table-composition/`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/layout-table-composition.md`, `quickstart.md`  
**Tests**: Add focused UI composition tests before production markup/CSS changes. Confirm the new tests fail for the intended missing-structure reason, then implement until they pass.

## Phase 1: Setup

- [X] T001 Review `.specify/memory/constitution.md`, `README.md`, Jira Orchestrate preset behavior, and relevant Mission Control design docs.
- [X] T002 Create MM-426 MoonSpec artifacts under `specs/214-mission-control-layout-table-composition/` and preserve the supplied Jira brief under `docs/tmp/jira-orchestration-inputs/`.

## Phase 2: Tests First

- [X] T003 Add focused task-list UI tests proving the control deck and data slab composition are present. (FR-001, FR-002, SC-001)
- [X] T004 Add focused task-list UI tests proving active filter chips render and clear filters. (FR-003, SC-002)
- [X] T005 Run the focused task-list test and record red/repair evidence. The first direct run failed on the new composition tests because the initial assertions used an unsupported matcher and ambiguous chip text query; after repairing the tests, the direct focused suite passed. (FR-008)

## Phase 3: Implementation

- [X] T006 Update `frontend/src/entrypoints/tasks-list.tsx` to group filters/utilities in a control deck and results/pagination/table/cards in a data slab. (FR-001, FR-002)
- [X] T007 Add active filter chips and a clear-filters action without changing request/query behavior. (FR-003, FR-007)
- [X] T008 Update `frontend/src/styles/mission-control.css` with control/data slab classes, sticky table headers, responsive control-grid behavior, and shared table slab styling. (FR-004, FR-005)
- [X] T009 Update `frontend/src/components/tables/DataTable.tsx` to emit shared Mission Control data-table classes. (FR-006)

## Phase 4: Verification

- [X] T010 Attempt `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`, then run the direct Vitest equivalent after the npm script cannot resolve `vitest` in this container. (FR-007, FR-008)
- [X] T011 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` or document the exact blocker. (FR-008)
- [X] T012 Write `specs/214-mission-control-layout-table-composition/verification.md` with coverage, commands, and verdict. (FR-009, SC-005)
- [X] T013 Commit the completed MM-426 work without pushing or creating a pull request.
