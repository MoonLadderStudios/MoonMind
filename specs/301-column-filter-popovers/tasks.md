# Tasks: Column Filter Popovers, Chips, and Selection Semantics

**Input**: Design documents from `specs/301-column-filter-popovers/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/tasks-list-column-filter-popovers.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: The canonical `MM-588` Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-025, acceptance scenarios 1 through 8, SC-001 through SC-008, and DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-027.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the existing Tasks List test harness and feature artifacts are ready.

- [X] T001 Verify `specs/301-column-filter-popovers/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/tasks-list-column-filter-popovers.md` are present and preserve `MM-588`.
- [X] T002 Confirm frontend test dependencies are prepared by running `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` before editing production code.
- [X] T003 Confirm API route test dependencies are available by running `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py` before editing production code.

## Phase 2: Foundational

**Purpose**: Establish the shared filter model and API parsing foundation that story implementation depends on.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 Add typed Tasks List filter state helpers for value, repository, and date filters in `frontend/src/entrypoints/tasks-list.tsx` covering FR-003 through FR-013 and DESIGN-REQ-013.
- [X] T005 Add canonical query parsing/building helpers for legacy `state`/`repo`/`targetRuntime` and new include/exclude/date params in `frontend/src/entrypoints/tasks-list.tsx` covering FR-021, FR-022, and FR-023.
- [X] T006 Add bounded value option derivation for runtime, skill, repository, status, and blank values in `frontend/src/entrypoints/tasks-list.tsx` covering FR-001, FR-010, FR-014, and FR-015.
- [X] T007 Add API route helper parsing for comma-separated include/exclude filters in `api_service/api/routers/executions.py` covering FR-006, FR-007, FR-011, and FR-024.

## Phase 3: Story - Column Filter Refinement

**Summary**: As an operator, I want column filters with staged popover editing, include/exclude semantics, blanks, and active chips so I can refine task rows without detached top dropdowns.

**Independent Test**: Render the Tasks List page with representative task rows and facet values, then exercise status, runtime, skill, repository, and date filter popovers to verify staged apply/cancel behavior, include and exclude semantics, blank handling, chip reopening/removal, clear-all behavior, URL/query state, and pagination reset.

**Traceability**: FR-001 through FR-025; SC-001 through SC-008; DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-027.

**Test Plan**:

- Unit: UI state helpers, staged draft behavior, chip summaries, label rendering, URL encoding, date validation, pagination reset.
- Integration: Tasks List user flows and `/api/executions` route query construction with task-scope constraints.

### Unit Tests (write first)

- [X] T008 [P] Add failing UI test for staged checkbox/text/date edits not applying until Apply in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-003, SC-001, and DESIGN-REQ-012.
- [X] T009 [P] Add failing UI test for Cancel, Escape, and outside-click dismissal preserving applied state in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-004, SC-002, and DESIGN-REQ-012.
- [X] T010 [P] Add failing UI test for Status include/exclude mode, canonical lifecycle order, and `Status: not canceled` chip in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-005 through FR-008, SC-003, and DESIGN-REQ-013.
- [X] T011 [P] Add failing UI test for Runtime and Skill raw values, readable labels, and bounded text rendering in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-009, FR-010, FR-014, FR-015, and SC-004.
- [X] T012 [P] Add failing UI test for Repository value selection plus legacy exact text mapping in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-011, FR-022, and SC-005.
- [X] T013 [P] Add failing UI test for Scheduled, Created, and Finished date bounds plus Scheduled/Finished blanks in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-012 and FR-013.
- [X] T014 [P] Add failing UI test for active chip reopen, individual chip removal, Clear filters, canonical URL rewrite, and pagination reset in `frontend/src/entrypoints/tasks-list.test.tsx` covering FR-016 through FR-023, SC-006, and DESIGN-REQ-014.

### Integration Tests (write first)

- [X] T015 [P] Add failing API route test for `stateIn`/`stateNotIn` task-scoped Temporal query construction in `tests/unit/api/routers/test_executions.py` covering FR-006, FR-007, FR-008, FR-024, and SC-007.
- [X] T016 [P] Add failing API route test for `targetRuntimeIn`/`targetRuntimeNotIn`, `targetSkillIn`/`targetSkillNotIn`, and `repoIn`/`repoNotIn` task-scoped query construction in `tests/unit/api/routers/test_executions.py` covering FR-009, FR-010, FR-011, FR-024, and SC-007.
- [X] T017 [P] Add failing API route test for legacy `state`, `repo`, and `targetRuntime` compatibility coexisting with canonical params in `tests/unit/api/routers/test_executions.py` covering FR-021, FR-022, FR-023, and DESIGN-REQ-015.

### Red-First Confirmation

- [X] T018 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` and confirm T008-T014 fail for the expected missing staged popover and chip semantics.
- [X] T019 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and confirm T015-T017 fail for the expected missing canonical API query support.

### Implementation

- [X] T020 Implement applied-vs-draft popover state, Apply, Cancel, Escape, and outside-click dismissal in `frontend/src/entrypoints/tasks-list.tsx` covering FR-003, FR-004, SC-001, and SC-002.
- [X] T021 Implement value-list include/exclude behavior, Select all, Status lifecycle ordering, and `not canceled` chip summaries in `frontend/src/entrypoints/tasks-list.tsx` covering FR-005 through FR-008 and DESIGN-REQ-013.
- [X] T022 Implement Runtime and Skill value-list popovers with raw stored values, readable labels, blank support where meaningful, and safe text rendering in `frontend/src/entrypoints/tasks-list.tsx` covering FR-009, FR-010, FR-014, and FR-015.
- [X] T023 Implement Repository value-list selection, exact text mode, blank support, and legacy `repo=<value>` mapping in `frontend/src/entrypoints/tasks-list.tsx` covering FR-011 and FR-022.
- [X] T024 Implement Scheduled, Created, and Finished date filter popovers with inclusive bounds and allowed blank handling in `frontend/src/entrypoints/tasks-list.tsx` covering FR-012 and FR-013.
- [X] T025 Implement active chip summaries, chip-open behavior, per-chip remove actions, Clear filters for all filter types, canonical URL encoding, and pagination reset in `frontend/src/entrypoints/tasks-list.tsx` covering FR-016 through FR-023.
- [X] T026 Add or adjust Mission Control CSS for accessible popovers, checklist rows, chip remove actions, date controls, and bounded value lists in `frontend/src/styles/mission-control.css` covering DESIGN-REQ-012 and DESIGN-REQ-014.
- [X] T027 Implement canonical include/exclude query construction for Temporal list filters in `api_service/api/routers/executions.py` covering FR-006, FR-007, FR-009, FR-010, FR-011, FR-021, FR-022, FR-023, and FR-024.

### Story Validation

- [X] T028 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` and fix failures until the MM-588 UI story passes.
- [X] T029 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and fix failures until canonical filter route coverage passes.
- [X] T030 Review `frontend/src/entrypoints/tasks-list.tsx` and `api_service/api/routers/executions.py` against `specs/301-column-filter-popovers/contracts/tasks-list-column-filter-popovers.md` for FR-024 and DESIGN-REQ-027 non-goal safety.

## Phase 4: Polish and Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T031 Update `specs/301-column-filter-popovers/tasks.md` to mark completed implementation tasks and preserve MM-588 traceability.
- [X] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full required unit verification.
- [X] T033 Run final `/speckit.verify` equivalent and write `specs/301-column-filter-popovers/verification.md` with verdict, test evidence, requirement coverage, and MM-588 traceability.

## Dependencies and Execution Order

### Phase Dependencies

- Phase 1 Setup has no dependencies.
- Phase 2 Foundational depends on Setup and blocks story work.
- Phase 3 Story depends on Foundational.
- Phase 4 Polish and Verification depends on the story passing focused tests.

### Within The Story

- T008-T017 must be written before implementation.
- T018-T019 must confirm red-first failures before T020-T027.
- T020-T027 implement the missing and partial behavior.
- T028-T030 validate the story before final verification.

### Parallel Opportunities

- T008-T014 can be authored in parallel with care because they all touch `frontend/src/entrypoints/tasks-list.test.tsx`; coordinate merges if multiple agents are used.
- T015-T017 can be authored in parallel in `tests/unit/api/routers/test_executions.py`.
- T026 can run after UI structure is known and is independent from API route implementation T027.

## Implementation Strategy

1. Preserve existing `MM-587` table/header behavior and tests.
2. Add failing tests for every missing MM-588 behavior before implementation.
3. Introduce a compact filter state model in the Tasks List entrypoint instead of spreading independent strings across controls.
4. Keep the API route fail-safe and task-scoped while adding canonical include/exclude query parameters.
5. Validate focused UI and API tests, then run full unit verification and final MoonSpec verification.

## Notes

- This task list covers exactly one story.
- `implemented_verified` rows from `plan.md` are preserved through validation tasks instead of unnecessary rewrites.
- `implemented_unverified` rows are covered by verification tests and extended only where MM-588 requires new behavior.
- Do not add saved views, multi-column sort, raw Temporal query authoring, direct browser calls to Temporal, or system workflow browsing.
