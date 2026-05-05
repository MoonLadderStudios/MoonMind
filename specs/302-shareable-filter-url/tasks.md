# Tasks: Shareable Filter URL Compatibility

**Input**: Design documents from `/specs/302-shareable-filter-url/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-589 is preserved in `spec.md`. Tasks cover FR-001 through FR-009, SC-001 through SC-005, DESIGN-REQ-006, DESIGN-REQ-016, DESIGN-REQ-017, and DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify existing test tooling and feature artifact structure.

- [X] T001 Verify MM-589 MoonSpec artifacts exist in specs/302-shareable-filter-url/spec.md, specs/302-shareable-filter-url/plan.md, specs/302-shareable-filter-url/research.md, specs/302-shareable-filter-url/data-model.md, specs/302-shareable-filter-url/contracts/tasks-list-url-state.md, and specs/302-shareable-filter-url/quickstart.md
- [X] T002 Verify frontend and backend focused test commands are available in package.json and tools/test_unit.sh

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Identify the existing URL and execution-list boundaries before story implementation.

- [X] T003 Inspect current URL parsing and sync helpers in frontend/src/entrypoints/tasks-list.tsx for FR-001 through FR-008
- [X] T004 Inspect current execution-list filter parsing in api_service/api/routers/executions.py for FR-004 through FR-006
- [X] T005 Inspect existing frontend and API tests in frontend/src/entrypoints/tasks-list.test.tsx and tests/unit/api/routers/test_executions.py

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Shareable Task Filter URLs

**Summary**: As an operator sharing a Tasks List view, I want old and new URLs to load predictably and fail safe so links keep their task-focused meaning without exposing broader workflow scopes.

**Independent Test**: Load legacy and canonical query strings in Tasks List and call `/api/executions` with canonical filters to verify URL state, requests, chips, pagination reset, task-only visibility, and validation errors.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, DESIGN-REQ-006, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, SC-001, SC-002, SC-003, SC-004, SC-005

**Test Plan**:

- Unit: API query validation and repeated-value normalization in tests/unit/api/routers/test_executions.py
- Integration: React Tasks List URL loading, filter application, chip rendering, and cursor reset behavior in frontend/src/entrypoints/tasks-list.test.tsx

### Unit Tests (write first) ⚠️

- [X] T006 [P] Add failing API unit test for repeated canonical filter params covering FR-004 and DESIGN-REQ-018 in tests/unit/api/routers/test_executions.py
- [X] T007 [P] Add failing API unit test for contradictory include/exclude filters covering FR-005 and DESIGN-REQ-018 in tests/unit/api/routers/test_executions.py
- [X] T008 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` to confirm T006-T007 fail for the expected reason

### Integration Tests (write first) ⚠️

- [X] T009 [P] Add failing frontend test for repeated canonical params and raw-value runtime chips covering FR-003, FR-004, FR-008, and SC-003 in frontend/src/entrypoints/tasks-list.test.tsx
- [X] T010 [P] Add failing frontend test for contradictory filter URL validation covering FR-005 and SC-004 in frontend/src/entrypoints/tasks-list.test.tsx
- [X] T011 [P] Add failing frontend test for page-size cursor reset covering FR-001, FR-007, DESIGN-REQ-006, and SC-005 in frontend/src/entrypoints/tasks-list.test.tsx
- [X] T012 Run `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` to confirm T009-T011 fail for the expected reason

### Implementation

- [X] T013 Implement repeated-value parsing and contradiction validation helpers in frontend/src/entrypoints/tasks-list.tsx for FR-004 and FR-005
- [X] T014 Render a clear recoverable validation error for contradictory filter URLs in frontend/src/entrypoints/tasks-list.tsx for FR-005
- [X] T015 Ensure page-size changes clear cursor state before URL/API sync in frontend/src/entrypoints/tasks-list.tsx for FR-007
- [X] T016 Implement repeated-value parsing and contradiction validation in api_service/api/routers/executions.py for FR-004 and FR-005
- [X] T017 Run focused frontend and API tests, fix failures, and verify FR-001 through FR-008 pass end-to-end

**Checkpoint**: The story is fully functional, covered by unit and frontend integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T018 Update specs/302-shareable-filter-url/tasks.md task statuses after implementation evidence is complete
- [X] T019 Run `./tools/test_unit.sh` for required unit-suite verification
- [X] T020 Run `/speckit.verify` for specs/302-shareable-filter-url and record final verification evidence

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on story tests passing

### Within The Story

- T006-T007 and T009-T011 must be authored before implementation.
- T008 and T012 must confirm red-first failures before T013-T016.
- T013-T016 must complete before T017.
- T018-T020 run only after focused tests pass.

### Parallel Opportunities

- T006 and T007 can be authored in parallel.
- T009, T010, and T011 can be authored in parallel.
- T013 and T016 can be implemented in parallel if coordinated because they touch different files.

---

## Implementation Strategy

1. Complete setup and foundational inspection.
2. Add red-first API and frontend tests for repeated values, contradictions, raw-value chips, and cursor reset.
3. Implement frontend URL parser/validation and backend query validation.
4. Run focused frontend and API tests.
5. Run full unit verification.
6. Run final `/speckit.verify` equivalent and preserve MM-589 in the outcome.

---

## Notes

- This task list covers one story only.
- Do not add new filter categories beyond the MM-589 URL compatibility and canonical encoding scope.
- Preserve task-only visibility for normal Tasks List requests.
- Preserve Jira issue key MM-589 in final implementation notes, verification output, commit text, and pull request metadata.
