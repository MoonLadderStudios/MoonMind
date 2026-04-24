# Tasks: Canonical Create Page Shell

**Input**: Design documents from `specs/195-canonical-create-page-shell/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and UI/request-shape integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code.

**Test Commands**:

- Backend route tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard.py`
- Focused UI tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, FR-002, SC-001, DESIGN-REQ-003: `/tasks/new` server shell and boot payload.
- FR-003, SC-002, DESIGN-REQ-003: compatibility route redirects to `/tasks/new`.
- FR-004, FR-005, FR-006, SC-003, SC-004, DESIGN-REQ-001, DESIGN-REQ-004: one composition form and canonical section order across create/edit/rerun.
- FR-007, SC-005, DESIGN-REQ-002, DESIGN-REQ-003: browser actions use MoonMind REST endpoints.
- FR-008, SC-006, DESIGN-REQ-001, DESIGN-REQ-002: manual authoring works without optional presets, Jira, or image upload.
- FR-009, DESIGN-REQ-004: scope stays task-first and MoonMind-native.
- FR-010, SC-007: MM-376 remains visible in artifacts and verification.

## Phase 1: Setup

- [X] T001 Confirm MM-376 source input and single-story traceability in `spec.md` (Input) and `specs/195-canonical-create-page-shell/spec.md`.
- [X] T002 Confirm existing Create page route and shell surfaces in `api_service/api/routers/task_dashboard.py`, `api_service/api/routers/task_dashboard_view_model.py`, and `frontend/src/entrypoints/task-create.tsx`.

## Phase 2: Foundational

- [X] T003 Confirm existing test harnesses cover backend dashboard routes and Create page UI behavior in `tests/unit/api/routers/test_task_dashboard.py` and `frontend/src/entrypoints/task-create.test.tsx`.

## Phase 3: Story - Canonical Create Page Shell

**Summary**: As a task author, I want `/tasks/new` to render one MoonMind-native task composition form so that create, edit, and rerun entry points use the same route, hosting model, section order, and MoonMind API boundaries.

**Independent Test**: Render and submit the Create page with optional integrations disabled and enabled. The story passes when `/tasks/new` receives the server boot payload, compatibility aliases redirect to `/tasks/new`, the canonical section order is exposed as Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, Submit, submission uses MoonMind REST endpoints only, and manual task authoring remains available without presets, Jira, or image upload.

**Traceability**: FR-001 through FR-010, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-004, MM-376.

### Unit Tests

- [X] T004 Add backend route test assertions for `/tasks/new` boot payload page and current-path runtime config in `tests/unit/api/routers/test_task_dashboard.py` (FR-001, FR-002, SC-001, DESIGN-REQ-003).
- [X] T005 Add frontend shell tests for canonical Create page section order in create mode in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, FR-006, SC-003, DESIGN-REQ-004).
- [X] T006 Add frontend shell tests proving edit and rerun modes reuse the Create page composition surface in `frontend/src/entrypoints/task-create.test.tsx` (FR-005, SC-004, DESIGN-REQ-001).
- [X] T007 Add frontend tests proving manual authoring remains available without optional Jira, attachment policy, or task preset catalog in `frontend/src/entrypoints/task-create.test.tsx` (FR-008, SC-006, DESIGN-REQ-002).

### Integration Tests

- [X] T008 Add or update UI request-shape test proving task submission uses the configured MoonMind REST create endpoint and no direct external endpoint in `frontend/src/entrypoints/task-create.test.tsx` (FR-007, SC-005, DESIGN-REQ-003).
- [X] T009 Run backend route and focused UI tests to confirm new shell tests fail for missing explicit section metadata or payload assertions before production edits.

### Implementation

- [X] T010 Add stable canonical section metadata and accessible labels around the Create page header, steps, task presets, dependencies, execution context, execution controls, schedule, and submit regions in `frontend/src/entrypoints/task-create.tsx` (FR-004, FR-006, FR-009, DESIGN-REQ-004).
- [X] T011 Preserve route and boot payload behavior in `api_service/api/routers/task_dashboard.py` and `api_service/api/routers/task_dashboard_view_model.py`; make only test-driven fixes if route tests expose drift (FR-001, FR-002, FR-003, DESIGN-REQ-003).
- [X] T012 Run focused backend and UI tests, then fix failures in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, or route tests only as needed (FR-001 through FR-009).

## Phase 4: Polish And Verification

- [X] T013 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T014 Run `/moonspec-verify` and record the result in `specs/195-canonical-create-page-shell/verification.md` (FR-010, SC-007).

## Dependencies & Execution Order

- T001-T003 must complete before story tests.
- T004-T008 must be written before T010-T011.
- T009 confirms red-first behavior before implementation.
- T010-T012 complete the story.
- T013-T014 run after focused tests pass.

## Parallel Opportunities

- T004 and T005 can be authored independently because they touch different test files.
- T006, T007, and T008 can be drafted together in the same UI test file but must be validated as one red-first test batch.

## Notes

- This task list covers exactly one story: MM-376.
