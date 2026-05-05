# Tasks: Empty/Error States and Regression Coverage for Final Rollout

**Input**: `specs/305-empty-error-states-rollout/spec.md`  
**Plan**: `specs/305-empty-error-states-rollout/plan.md`  
**Research**: `specs/305-empty-error-states-rollout/research.md`  
**Data Model**: `specs/305-empty-error-states-rollout/data-model.md`  
**Contract**: `specs/305-empty-error-states-rollout/contracts/tasks-list-empty-error-rollout.md`  
**Unit Test Command**: `node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration Test Command**: `node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx`  
**Final Verification**: `/speckit.verify`

**Source Traceability**: The original `MM-592` Jira preset brief is preserved in `spec.md`. This task list covers exactly one story, FR-001 through FR-013, acceptance scenarios 1 through 8, SC-001 through SC-008, and DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, and DESIGN-REQ-028.

**Requirement Status Summary**: `plan.md` marks structured API error handling and final regression coverage as partial or implemented-unverified. The task sequence is TDD-first: add focused failing tests, confirm red-first for the structured API error gap, implement the error-detail parser, then run focused and final validation.

## Phase 1: Setup

- [X] T001 Confirm `.specify/feature.json` points at `specs/305-empty-error-states-rollout/` and `spec.md` preserves the `MM-592` Jira preset brief. (FR-013, SC-008)
- [X] T002 Create planning artifacts `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/tasks-list-empty-error-rollout.md`. (FR-013, DESIGN-REQ-028)
- [X] T003 Confirm the implementation scope is `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-001 through FR-012)

## Phase 2: Foundational

- [X] T004 Inspect `docs/UI/TasksListPage.md` sections 5.8, 17, 19, 20, and 21 and map them in `spec.md`. (DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-028)
- [X] T005 Inspect existing Tasks List loading, error, empty, pagination, facet fallback, validation, and old-control tests before adding MM-592 coverage. (FR-001 through FR-012)

## Phase 3: Story - Recoverable Final Column-Filter Rollout

**Story Summary**: Operators keep recoverable loading, error, empty, pagination, facet fallback, invalid-filter, and final parity behavior after the old top filter form is removed.

**Independent Test**: Render the Tasks List page with mocked loading, API error, empty first-page, empty later-page, facet-failure, invalid-filter, and populated responses, then verify visible recovery paths, preserved filter state, old-control absence, and final rollout regression evidence.

**Traceability IDs**: FR-001 through FR-013; acceptance scenarios 1 through 8; SC-001 through SC-008; DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-028.

**Unit Test Plan**: Add focused Vitest/Testing Library tests in `frontend/src/entrypoints/tasks-list.test.tsx` for pending loading, structured API error detail, and empty first-page active-filter recovery.

**Integration Test Plan**: Use the same rendered Tasks List component harness as the UI integration surface; existing tests already cover empty later-page pagination, facet fallback, local invalid-filter recovery, old-control absence, and workflow-kind non-goals.

### Unit Tests

- [X] T006 Add a loading-state regression test in `frontend/src/entrypoints/tasks-list.test.tsx` that holds the list request pending and asserts `Loading tasks...`. (FR-001, SC-001, DESIGN-REQ-006)
- [X] T007 Add a structured API error regression test in `frontend/src/entrypoints/tasks-list.test.tsx` that returns a failed list response with `detail.message` and expects that message in a visible notice. (FR-002, FR-009, SC-002, SC-006, DESIGN-REQ-024)
- [X] T008 Add an empty first-page active-filter recovery test in `frontend/src/entrypoints/tasks-list.test.tsx` that applies a filter, receives no rows, sees `No tasks found for the current filters.`, and has an enabled `Clear filters` action. (FR-003, FR-004, SC-003, DESIGN-REQ-006, DESIGN-REQ-024)
- [X] T009 Preserve existing empty later-page pagination, facet fallback, invalid local filter recovery, old-control absence, and workflow-kind non-goal tests in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-005 through FR-008, FR-010, FR-011, SC-004, SC-005, SC-007, DESIGN-REQ-026, DESIGN-REQ-027)

### Integration Tests

- [X] T010 Run the focused Tasks List UI test file after T006-T008 to confirm the structured API error test fails before implementation and existing recovery tests remain stable. (FR-001 through FR-012)

### Red-First Confirmation

- [X] T011 Record the red-first failure for the structured API error message in `specs/305-empty-error-states-rollout/verification.md` or implementation notes before production changes. (FR-002, FR-009, SC-002, SC-006)

### Implementation

- [X] T012 Add a sanitized list-response error detail helper in `frontend/src/entrypoints/tasks-list.tsx` and use it when the list response is not OK. (FR-002, FR-009, DESIGN-REQ-024)
- [X] T013 Keep the existing loading, empty first-page, empty later-page, facet fallback, local validation, old-control absence, and task-scope behavior unchanged while implementing T012. (FR-001, FR-003 through FR-008, FR-010, FR-011, DESIGN-REQ-006, DESIGN-REQ-026, DESIGN-REQ-027)

### Story Validation

- [X] T014 Run `node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx` and record the result in `verification.md`. (FR-001 through FR-013, SC-001 through SC-008)
- [X] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` when feasible and record pass/fail/blocker details in `verification.md`. (FR-001 through FR-013, SC-001 through SC-008)

## Phase 4: Polish And Verification

- [X] T016 Review `specs/305-empty-error-states-rollout/contracts/tasks-list-empty-error-rollout.md` against `tasks-list.tsx` and `tasks-list.test.tsx`. (FR-001 through FR-012)
- [X] T017 Run source traceability check for `MM-592` and DESIGN-REQ-006/DESIGN-REQ-024/DESIGN-REQ-026/DESIGN-REQ-027/DESIGN-REQ-028 across `specs/305-empty-error-states-rollout`. (FR-013, SC-008)
- [X] T018 Run `/speckit.verify` and write `specs/305-empty-error-states-rollout/verification.md` with the final verdict against the original `MM-592` preset brief. (FR-013, SC-008)

## Dependencies and Execution Order

1. T001-T003 establish active feature artifacts and source/test scope.
2. T004-T005 establish source-design and repo behavior context.
3. T006-T008 add missing UI tests before production changes.
4. T010-T011 confirm red-first behavior for the structured API error gap.
5. T012-T013 implement the bounded UI error-detail change without regressing existing recovery behavior.
6. T014-T015 validate focused and full unit evidence.
7. T016-T018 complete contract review, traceability, and final MoonSpec verification.

## Parallel Examples

- T006 and T008 can be drafted in parallel only in separate workspaces because they both touch `frontend/src/entrypoints/tasks-list.test.tsx`; merge serially.
- T016 and T017 can run in parallel because one reviews behavior against the contract and one runs artifact traceability.

## Implementation Strategy

Add the missing regression tests first. The structured API error test should fail against the current generic `statusText` behavior, proving the implementation gap. Then add a small sanitized error-detail parser in `tasks-list.tsx` and rerun the focused Tasks List suite. Preserve all existing final rollout tests, especially old-control absence and task-scope non-goal safety.
