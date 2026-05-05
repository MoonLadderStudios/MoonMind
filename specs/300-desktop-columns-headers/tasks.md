# Tasks: Desktop Columns and Compound Headers

**Input**: `specs/300-desktop-columns-headers/spec.md`  
**Plan**: `specs/300-desktop-columns-headers/plan.md`  
**Unit test command**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration/API test command**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`  
**Source Traceability**: `MM-587`; FR-001 through FR-013; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-027.

## Story

As an operator scanning tasks on desktop, I want each visible task column to own sorting and filtering controls so the table behaves like a compact operational spreadsheet.

## Tasks

- [X] T001 Create MoonSpec artifacts in `specs/300-desktop-columns-headers/` preserving MM-587.
- [X] T002 [P] Add failing UI tests for separate sort and filter header targets in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-003, FR-004, FR-005, FR-006, SC-002, SC-003, DESIGN-REQ-011)
- [X] T003 [P] Add failing UI tests for status, repository, runtime header filters, clickable chips, clear filters, and excluded columns in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-001, FR-002, FR-008, FR-009, FR-010, FR-012, SC-001, SC-004, DESIGN-REQ-008, DESIGN-REQ-010, DESIGN-REQ-027)
- [X] T004 [P] Add failing API route test for `targetRuntime` filtering in `tests/unit/api/routers/test_executions.py`. (SC-005)
- [X] T005 Implement compound table header controls and filter popovers in `frontend/src/entrypoints/tasks-list.tsx`. (FR-003, FR-004, FR-005, FR-006, FR-008, FR-009, FR-010, FR-012)
- [X] T006 Implement runtime filter request and URL state in `frontend/src/entrypoints/tasks-list.tsx`. (FR-008, FR-010, SC-005)
- [X] T007 Implement `targetRuntime` Temporal query filtering in `api_service/api/routers/executions.py`. (SC-005)
- [X] T008 Add compound header and popover styling in `frontend/src/styles/mission-control.css`. (FR-003, FR-005)
- [X] T009 Verify existing row formatting and dependency summary tests still pass in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-011, DESIGN-REQ-006, DESIGN-REQ-007)
- [X] T010 Run focused UI tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`.
- [X] T011 Run focused API tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`.
- [X] T012 Run full unit suite: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T013 Run final `/moonspec-verify` and write `specs/300-desktop-columns-headers/verification.md`. (FR-013, SC-006)

## Dependencies

T001 before all other tasks. T002, T003, and T004 before implementation tasks. T005 through T008 before T010 and T011. T012 before final verification.
