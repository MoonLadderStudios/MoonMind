# Tasks: Mission Control Layout and Table Composition Patterns

**Input**: Design artifacts from `specs/214-mission-control-layout-table-composition/`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `contracts/layout-table-composition.md`, `quickstart.md`
**Story**: Exactly one story, "Layout and Table Composition"
**Independent Test**: Render the task list with Temporal rows and verify the control deck, data slab, active filter chips, sticky table header posture, existing request/sorting/pagination behavior, and mobile-card behavior.
**Source Traceability**: FR-001 through FR-009, SC-001 through SC-005, acceptance scenarios 1-5, and DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-019 from the trusted MM-426 Jira preset brief.
**Unit Test Plan**: Use focused Vitest coverage in `frontend/src/entrypoints/tasks-list.test.tsx` for semantic UI structure, active-filter chips, clear behavior, sticky header posture, and existing task-list behavior.
**Integration Test Plan**: Use the task-list browser component boundary in `frontend/src/entrypoints/tasks-list.test.tsx` as integration-style coverage for rendered filters, request/query shape, routing links, pagination, mobile cards, active-filter chips, and table structure. No compose-backed integration test is required because backend contracts, persistence, and Temporal behavior are unchanged.

## Phase 1: Setup

- [X] T001 Review `.specify/memory/constitution.md`, `README.md`, Jira Orchestrate preset behavior, `docs/UI/MissionControlDesignSystem.md`, and the trusted MM-426 Jira preset brief in `docs/tmp/jira-orchestration-inputs/MM-426-moonspec-orchestration-input.md`. (FR-009, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-019)
- [X] T002 Create and align MM-426 MoonSpec artifacts under `specs/214-mission-control-layout-table-composition/`, preserving the trusted Jira preset brief and source design coverage IDs. (FR-009, SC-005)

## Phase 2: Tests First

- [X] T003 Add red-first focused unit tests in `frontend/src/entrypoints/tasks-list.test.tsx` proving `.task-list-control-deck.panel--controls` and `.task-list-data-slab.panel--data` are present and contain the expected controls/results. (FR-001, FR-002, SC-001, DESIGN-REQ-012, DESIGN-REQ-019)
- [X] T004 Add red-first focused unit tests in `frontend/src/entrypoints/tasks-list.test.tsx` proving active filter chips render and the clear-filters action resets filter state. (FR-003, SC-002, DESIGN-REQ-014)
- [X] T005 Add integration-style task-list render coverage in `frontend/src/entrypoints/tasks-list.test.tsx` for request/query shape, routing links, pagination, mobile cards, table-first desktop structure, and sticky header posture. (FR-004, FR-005, FR-007, FR-008, SC-003, SC-004, DESIGN-REQ-019)
- [X] T006 Confirm red-first evidence by running the focused task-list tests before production changes; record the initial failure and repair notes. The first direct run failed on new composition assertions because of an unsupported matcher and ambiguous chip text query, then passed after test repair. (FR-008)

## Phase 3: Implementation

- [X] T007 Update `frontend/src/entrypoints/tasks-list.tsx` to group filters/utilities in a control deck and results/pagination/table/cards in a data slab. (FR-001, FR-002, DESIGN-REQ-012, DESIGN-REQ-019)
- [X] T008 Add active filter chips and a clear-filters action in `frontend/src/entrypoints/tasks-list.tsx` without changing request/query behavior. (FR-003, FR-007, DESIGN-REQ-014)
- [X] T009 Update `frontend/src/styles/mission-control.css` with control/data slab classes, sticky table headers, responsive control-grid behavior, constrained table cell behavior, and shared table slab styling. (FR-004, FR-005, DESIGN-REQ-012, DESIGN-REQ-019)
- [X] T010 Update `frontend/src/components/tables/DataTable.tsx` to emit shared Mission Control data-table slab, table, and empty-state classes. (FR-006)

## Phase 4: Story Validation

- [X] T011 Run `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`, then run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` after the npm script cannot resolve `vitest` in this container. (FR-001 through FR-008, SC-001 through SC-004)
- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` to validate the canonical unit wrapper and targeted UI coverage. (FR-001 through FR-008, SC-001 through SC-004)
- [X] T013 Run build/manifest validation with `npm run ui:build:check`, `./node_modules/.bin/vite build --config frontend/vite.config.ts`, and `bash tools/run_repo_python.sh tools/verify_vite_manifest.py`, documenting npm script binary-resolution blockers when present. (FR-006, FR-007)

## Phase 5: Final Verification

- [X] T014 Run final `/moonspec-verify` read-only verification for `specs/214-mission-control-layout-table-composition/spec.md` and write `specs/214-mission-control-layout-table-composition/verification.md` with coverage, commands, source traceability, and verdict. (FR-009, SC-005)
- [X] T015 Commit the completed MM-426 work without pushing or creating a pull request.

## Dependencies And Execution Order

1. Complete setup tasks T001-T002.
2. Write unit and integration-style tests first in T003-T005.
3. Confirm red-first evidence in T006 before implementation tasks.
4. Complete implementation tasks T007-T010.
5. Run story validation tasks T011-T013.
6. Run final `/moonspec-verify` work in T014.
7. Commit in T015.

## Implementation Strategy

All rows in the refreshed `plan.md` are `implemented_verified`. This task list preserves the original red-first sequence and completed evidence while keeping the single-story MM-426 scope isolated. Future reruns should not add implementation work unless verification regresses or the trusted Jira preset brief changes.
