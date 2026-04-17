# Tasks: Create Page Repository Dropdown

**Input**: Design documents from `/specs/204-repository-dropdown/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-393, FR-001 through FR-010, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-004.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Verify existing project and test surfaces needed for this story

- [X] T001 Verify repository option work uses existing dashboard runtime config and Create page files in `api_service/api/routers/task_dashboard_view_model.py` and `frontend/src/entrypoints/task-create.tsx`
- [X] T002 Verify focused backend and frontend test commands from `specs/204-repository-dropdown/quickstart.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core repository option contract and normalization prerequisites

**CRITICAL**: No story implementation work can begin until this phase is complete

- [X] T003 Define the browser-safe repository option contract in `specs/204-repository-dropdown/contracts/repository-options.md` for FR-001, FR-005, FR-006
- [X] T004 Confirm no persistent storage or migration is required in `specs/204-repository-dropdown/plan.md` for FR-004

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - Create Page Repository Dropdown

**Summary**: As a task author, I want the Create page repository field to offer known and credential-visible repositories as selectable options so that I can target a run without manually typing an owner/repo value.

**Independent Test**: Open `/tasks/new` with configured repository options and mocked credential-visible repositories, select an option, submit, and verify the selected repository is submitted while manual entry still works when discovery is unavailable.

**Traceability**: MM-393, FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004

**Test Plan**:

- Unit: repository option normalization, configured repositories, credential-visible GitHub repositories, invalid/duplicate/secret-bearing exclusions, sanitized discovery failure.
- Integration: Create page repository datalist rendering, option selection, submit payload, manual fallback.

### Unit Tests (write first)

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [X] T005 Add failing unit tests for configured default and `GITHUB_REPOS` repository options covering FR-001, FR-002, SC-001 in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T006 Add failing unit tests for credential-visible GitHub repository inclusion and discovery failure degradation covering FR-003, FR-004, SC-002, SC-006 in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T007 Add failing unit tests for invalid, duplicate, and credential-bearing repository exclusion covering FR-005, FR-006, SC-003 in `tests/unit/api/routers/test_task_dashboard_view_model.py`
- [X] T008 Run `./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py` to confirm T005-T007 fail for the expected reason

### Integration Tests (write first)

- [X] T009 Add failing Create page test for repository datalist rendering and option selection covering FR-007, SC-004 in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T010 Add failing Create page test for selected option submission and manual fallback covering FR-008, FR-009, SC-005, SC-006 in `frontend/src/entrypoints/task-create.test.tsx`
- [X] T011 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` to confirm T009-T010 fail for the expected reason

### Implementation

- [X] T012 Implement repository option normalization, configured repository collection, GitHub API best-effort discovery, and sanitized error handling for FR-001 through FR-006 in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T013 Expose `system.repositoryOptions` in Create page runtime config for FR-001 through FR-006 in `api_service/api/routers/task_dashboard_view_model.py`
- [X] T014 Extend the Create page dashboard config type and repository input datalist rendering for FR-007 in `frontend/src/entrypoints/task-create.tsx`
- [X] T015 Preserve existing submit validation and selected/manual repository payload behavior for FR-008 and FR-009 in `frontend/src/entrypoints/task-create.tsx`
- [X] T016 Run focused backend and frontend commands, fix failures, and verify the story passes end-to-end for SC-001 through SC-006

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that strengthen the completed story without changing its core scope

- [X] T017 [P] Update MoonSpec verification evidence and preserve MM-393 references in `specs/204-repository-dropdown/quickstart.md`
- [X] T018 Run `./tools/test_unit.sh` for final required unit-suite verification
- [X] T019 Run `/speckit.verify` to validate the final implementation against the original MM-393 feature request

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS story work
- **Story (Phase 3)**: Depends on Foundational phase completion
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing

### Within The Story

- Unit tests T005-T007 MUST be written and FAIL before implementation
- Integration tests T009-T010 MUST be written and FAIL before implementation
- Red-first confirmation tasks T008 and T011 MUST complete before production code tasks T012-T015
- Backend runtime config support precedes frontend option rendering
- Story complete before polish work

### Parallel Opportunities

- T005, T006, and T007 touch the same backend test file and must be applied sequentially.
- T009 and T010 touch the same frontend test file and must be applied sequentially.
- T017 can run after implementation while final validation is prepared.

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 artifact/contract checks.
2. Add backend unit tests and confirm they fail.
3. Add frontend tests and confirm they fail.
4. Implement backend repository option discovery and runtime config.
5. Implement frontend datalist suggestions without changing manual entry semantics.
6. Run focused backend and frontend tests until passing.
7. Run full `./tools/test_unit.sh`.
8. Run `/speckit.verify`.

---

## Notes

- The task list covers one story only.
- Repository options are suggestions, not an authorization or allowlist boundary.
- GitHub discovery is best-effort and must not block manual authoring.
- Browser payloads must remain free of raw credentials and secret refs.
