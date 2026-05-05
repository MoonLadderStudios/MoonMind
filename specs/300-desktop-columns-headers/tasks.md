# Tasks: Desktop Columns and Compound Headers

**Input**: `specs/300-desktop-columns-headers/spec.md`  
**Plan**: `specs/300-desktop-columns-headers/plan.md`  
**Unit test command**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration/API test command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`  
**Full validation command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`  
**Source Traceability**: `MM-587`; FR-001 through FR-013; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-027.

## Story

As an operator scanning tasks on desktop, I want each visible task column to own sorting and filtering controls so the table behaves like a compact operational spreadsheet.

**Independent Test**: Render the Tasks List page with sample Temporal rows, activate header sort labels and filter buttons independently, and verify API requests, URL state, active chips, table columns, accessibility labels, and row formatting.

**Traceability IDs**: FR-001 through FR-013; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-027.

## Unit Test Plan

- UI unit coverage in `frontend/src/entrypoints/tasks-list.test.tsx` verifies default/excluded columns, compound header sort/filter separation, `aria-sort`, filter popovers, active chips, clear filters, runtime URL/request state, row formatting, and dependency summaries.
- Shared CSS regression coverage in `frontend/src/entrypoints/mission-control.test.tsx` verifies the new controls do not break existing Mission Control button/control contracts.

## Integration/API Test Plan

- API route-boundary coverage in `tests/unit/api/routers/test_executions.py` verifies `targetRuntime` is included as `mm_target_runtime` in the task-scoped Temporal visibility query.
- Full repository validation uses `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`, which runs Python unit tests and the frontend Vitest suite in this managed container.

## Phase 1: Setup

- [X] T001 Create MoonSpec artifacts in `specs/300-desktop-columns-headers/` preserving MM-587 and the original Jira preset brief. (FR-013, SC-006)

## Phase 2: Foundational

- [X] T002 Verify the active feature points at `specs/300-desktop-columns-headers` in `.specify/feature.json` before task execution. (FR-013)

## Phase 3: Story - Desktop Compound Table Headers

- [X] T003 [P] Add failing UI unit tests for separate sort and filter header targets in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-003, FR-004, FR-005, FR-006, SC-002, SC-003, DESIGN-REQ-011)
- [X] T004 [P] Add failing UI unit tests for status, repository, runtime header filters, clickable chips, clear filters, and excluded columns in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-001, FR-002, FR-008, FR-009, FR-010, FR-012, SC-001, SC-004, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-027)
- [X] T005 [P] Add failing API route-boundary test for `targetRuntime` filtering in `tests/unit/api/routers/test_executions.py`. (SC-005)
- [X] T006 Confirm red-first failures for T003, T004, and T005 before production implementation by running the focused UI/API commands and observing the expected missing-behavior failures. (FR-003, FR-005, FR-008, SC-005)
- [X] T007 Implement compound table header controls and filter popovers in `frontend/src/entrypoints/tasks-list.tsx`. (FR-003, FR-004, FR-005, FR-006, FR-008, FR-009, FR-010, FR-012)
- [X] T008 Implement runtime filter request and URL state in `frontend/src/entrypoints/tasks-list.tsx`. (FR-008, FR-010, SC-005)
- [X] T009 Implement `targetRuntime` Temporal query filtering in `api_service/api/routers/executions.py`. (SC-005)
- [X] T010 Add compound header and popover styling in `frontend/src/styles/mission-control.css`. (FR-003, FR-005)
- [X] T011 Verify existing row formatting, status pill, runtime label, date formatting, and dependency summary tests still pass in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-011, DESIGN-REQ-006, DESIGN-REQ-007)
- [X] T012 Run focused UI story validation: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`. (SC-001, SC-002, SC-003, SC-004)
- [X] T013 Run focused API story validation: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`. (SC-005)

## Final Phase: Polish And Verification

- [X] T014 Run shared Mission Control CSS regression validation: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/mission-control.test.tsx`. (FR-003, FR-005)
- [X] T015 Run frontend typecheck: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`. (FR-001 through FR-012)
- [X] T016 Run full unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`. (FR-001 through FR-013, SC-001 through SC-006)
- [X] T017 Run final `/moonspec-verify` and write `specs/300-desktop-columns-headers/verification.md`. (FR-013, SC-006)

## Dependencies And Execution Order

T001 and T002 precede story work. T003, T004, and T005 are parallel test-authoring tasks and precede T006. T006 precedes implementation tasks T007 through T010. T011 through T013 validate the story after implementation. T014 through T017 are final polish and verification tasks.

## Implementation Strategy

The current plan marks every requirement as `implemented_verified`. This task list preserves the completed TDD sequence and validation evidence rather than adding new implementation work. Future changes to the MM-587 story should re-open the relevant task phase, add failing tests before code changes, and rerun final `/moonspec-verify`.
