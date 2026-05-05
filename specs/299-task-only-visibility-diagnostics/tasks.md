# Tasks: Task-only Visibility and Diagnostics Boundary

**Input**: `specs/299-task-only-visibility-diagnostics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/tasks-list-visibility-boundary.md`, `quickstart.md`

**Unit Test Command**: `pytest tests/unit/api/test_executions_temporal.py -q`
**Targeted Frontend Command**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`
**Integration Test Command**: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`
**Final Unit Wrapper**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`

## Source Traceability Summary

- `MM-586` is preserved as the canonical Jira preset brief in `spec.md`.
- `DESIGN-REQ-005`: normal Tasks List is task-oriented, not a workflow-kind browser.
- `DESIGN-REQ-008`: normal table excludes `Kind`, `Workflow Type`, and `Entry` and defaults to task-run semantics.
- `DESIGN-REQ-009`: system and manifest rows cannot appear through filters or old URL params.
- `DESIGN-REQ-017`: old URL params fail safe while preserving task-compatible filters.
- `DESIGN-REQ-025`: filter params cannot bypass authorization or expose hidden workflow categories.

## Requirement Status Refresh

The upstream `plan.md` and `research.md` were refreshed after implementation to mark all MM-586 requirements, scenarios, and in-scope source design requirements as `implemented_verified`. This `tasks.md` remains the completed execution record for the test-first work that produced that evidence: red-first backend unit tests, red-first frontend integration-style tests, production implementation tasks, story validation commands, alignment, and final `/moonspec-verify`.

## Story

As an ordinary Tasks List user, I want `/tasks/list` to show only user-visible task runs and not become a workflow-kind browser.

**Independent Test**: Render `/tasks/list` with default and legacy workflow-scope URLs, inspect the browser request and visible controls, and query the execution list boundary to confirm only task-run visibility is requested or returned while recoverable messaging appears for ignored workflow-scope URL state.

## Unit Test Plan

- Backend unit coverage in `tests/unit/api/test_executions_temporal.py` verifies the source-temporal list boundary fails safe to task-run query semantics when ordinary users supply broad `scope`, `workflowType`, or `entry` parameters.
- Existing unknown-scope validation remains covered.

## Integration Test Plan

- Integration-style UI coverage in `frontend/src/entrypoints/tasks-list.test.tsx` verifies broad controls are absent, task filters remain, legacy broad URL params are normalized with a recoverable notice, request URLs stay task-scoped, and forbidden table headers are absent.

## Task Phases

### Phase 1: Setup

- [X] T001 Create MoonSpec feature directory and preserve MM-586 Jira preset brief in `specs/299-task-only-visibility-diagnostics/spec.md`
- [X] T002 Create planning, research, data model, visibility contract, quickstart, and checklist artifacts under `specs/299-task-only-visibility-diagnostics/`

### Phase 2: Test-First Coverage

- [X] T003 Add failing backend unit coverage for broad source-temporal scope normalization in `tests/unit/api/test_executions_temporal.py`. (FR-006, FR-007, SC-005, DESIGN-REQ-005, DESIGN-REQ-009, DESIGN-REQ-025)
- [X] T004 Add failing frontend integration-style coverage for hidden Scope/Workflow Type/Entry controls, task-scoped requests, legacy URL normalization, recoverable notice, preserved Status/Repository filters, and forbidden table headers in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-008, DESIGN-REQ-017)
- [X] T005 Run red-first targeted backend command `pytest tests/unit/api/test_executions_temporal.py -q` and confirm the new MM-586 backend assertions fail before production changes. (T003)
- [X] T006 Run red-first targeted frontend command `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` and confirm the new MM-586 UI assertions fail before production changes. (T004)

### Phase 3: Story Implementation

- [X] T007 Harden source-temporal list scope normalization in `api_service/api/routers/executions.py` so broad scope/workflowType/entry parameters fail safe to task-run query semantics while unknown scopes still validate. (FR-001, FR-006, FR-007, DESIGN-REQ-005, DESIGN-REQ-009, DESIGN-REQ-025)
- [X] T008 Remove ordinary workflow-kind controls and state from `frontend/src/entrypoints/tasks-list.tsx` while preserving Status, Repository, live updates, pagination, and sorting. (FR-002, FR-005, DESIGN-REQ-005, DESIGN-REQ-008)
- [X] T009 Normalize legacy workflow-scope URL parameters and add recoverable ignored-scope notice in `frontend/src/entrypoints/tasks-list.tsx`. (FR-003, FR-008, FR-009, DESIGN-REQ-017)
- [X] T010 Preserve task table forbidden-column behavior and text rendering in `frontend/src/entrypoints/tasks-list.tsx`. (FR-004, FR-010, DESIGN-REQ-008, DESIGN-REQ-025)

### Phase 4: Story Validation

- [X] T011 Run targeted backend validation: `pytest tests/unit/api/test_executions_temporal.py -q`
- [X] T012 Run targeted frontend validation: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`
- [X] T013 Run final unit wrapper: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`

### Final Phase: Polish And Verification

- [X] T014 Run MoonSpec alignment and record result in `specs/299-task-only-visibility-diagnostics/moonspec_align_report.md`
- [X] T015 Run final `/moonspec-verify` and record verdict in `specs/299-task-only-visibility-diagnostics/verification.md`

## Dependencies

- T003 and T004 depend on T001 and T002.
- T005 depends on T003; T006 depends on T004.
- T007 through T010 depend on red-first confirmation T005 and T006.
- T011 through T013 depend on implementation tasks T007 through T010.
- T014 and T015 depend on validation passing.

## Parallel Opportunities

- T003 and T004 may be authored in parallel because they touch different test files.
- T007 and T008/T009 touch different production files after red-first confirmation, but T008 and T009 both edit `tasks-list.tsx` and should be coordinated sequentially.

## Implementation Strategy

Completed via strict TDD: backend and frontend tests were added and confirmed failing against the broad workflow-browsing behavior, then the smallest scoped changes were made in `api_service/api/routers/executions.py` and `frontend/src/entrypoints/tasks-list.tsx`. Existing route/shell behavior from MM-585 was preserved, no diagnostics route was added, and final verification preserved MM-586 plus all in-scope DESIGN-REQ IDs.
