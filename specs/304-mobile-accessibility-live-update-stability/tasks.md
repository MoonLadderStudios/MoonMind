# Tasks: Mobile, Accessibility, and Live-Update Stability

**Input**: `specs/304-mobile-accessibility-live-update-stability/spec.md`  
**Plan**: `specs/304-mobile-accessibility-live-update-stability/plan.md`  
**Research**: `specs/304-mobile-accessibility-live-update-stability/research.md`  
**Data Model**: `specs/304-mobile-accessibility-live-update-stability/data-model.md`  
**Contract**: `specs/304-mobile-accessibility-live-update-stability/contracts/tasks-list-filter-behavior.md`  
**Unit Test Command**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`  
**Integration Test Command**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx`  
**Final Verification**: `/speckit.verify`

**Source Traceability**: The original `MM-591` Jira preset brief is preserved in `spec.md`. This task list covers exactly one story, FR-001 through FR-010, acceptance scenarios 1 through 7, SC-001 through SC-006, and DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, and DESIGN-REQ-023.

**Requirement Status Summary**: `plan.md` currently marks all 20 tracked rows as `implemented_verified`. The tasks below preserve the red-first implementation sequence and identify the existing files that carry the verified evidence. When replaying from a clean baseline, execute the test tasks before implementation tasks; in the current workspace, use the validation and `/speckit.verify` tasks to preserve evidence.

## Phase 1: Setup

- [X] T001 Confirm `.specify/feature.json` points at `specs/304-mobile-accessibility-live-update-stability/` and that `specs/304-mobile-accessibility-live-update-stability/spec.md` preserves the `MM-591` Jira preset brief. (FR-010, SC-006)
- [X] T002 Confirm `specs/304-mobile-accessibility-live-update-stability/plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/tasks-list-filter-behavior.md` exist before story task execution. (FR-010, SC-006)
- [X] T003 Confirm `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/tasks-list.test.tsx` are the only source/test files required by the implementation plan. (FR-001 through FR-009)

## Phase 2: Foundational

- [X] T004 Inspect `docs/UI/TasksListPage.md` sections 5.7, 14, 15, and 16 and confirm the source mappings in `specs/304-mobile-accessibility-live-update-stability/spec.md`. (DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023)
- [X] T005 Inspect existing task-only query, filter parsing, URL serialization, and mobile card behavior in `frontend/src/entrypoints/tasks-list.tsx` before adding tests. (FR-001 through FR-009)

## Phase 3: Story - Stable Accessible Task Filters

**Story Summary**: Operators on mobile, desktop, and assistive technologies can use equivalent task filters without live updates disrupting staged selections.

**Independent Test**: Render the Tasks List page with mocked execution data, operate desktop filter dialogs by keyboard, operate mobile filters for ID, Title, status, runtime, skill, repository, and dates, and verify task-scoped API URLs plus focus behavior.

**Traceability IDs**: FR-001 through FR-010; acceptance scenarios 1 through 7; SC-001 through SC-006; DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023.

**Unit Test Plan**: Use `frontend/src/entrypoints/tasks-list.test.tsx` to verify filter state parsing, mobile control reachability, task-scoped URL serialization, staged filter behavior, Enter apply behavior, active chips, and workflow-kind guardrails.

**Integration Test Plan**: Use the same Testing Library component harness in `frontend/src/entrypoints/tasks-list.test.tsx` as the UI integration surface for the rendered Tasks List page; no compose-backed integration is required for this frontend-only story.

### Unit Tests

- [X] T006 Add or preserve the mobile filter reachability test in `frontend/src/entrypoints/tasks-list.test.tsx` for ID, Runtime, Skill, Repository, Status, Title, Scheduled, Created, and Finished controls. (FR-001, SC-001, DESIGN-REQ-023)
- [X] T007 Add or preserve the mobile filter URL test in `frontend/src/entrypoints/tasks-list.test.tsx` for task-scoped `taskIdContains`, `titleContains`, status, runtime, repository, and stale-cursor omission. (FR-002, SC-002, DESIGN-REQ-023)
- [X] T008 Add or preserve the desktop filter keyboard test in `frontend/src/entrypoints/tasks-list.test.tsx` for focus-in and Enter-to-apply behavior. (FR-004, FR-006, SC-003, SC-004, DESIGN-REQ-022)
- [X] T009 Preserve staging, Escape, outside-click, active-chip, status-label, workflow-kind, and mobile-card guardrail tests in `frontend/src/entrypoints/tasks-list.test.tsx`. (FR-003, FR-005, FR-008, FR-009, DESIGN-REQ-006, DESIGN-REQ-021, DESIGN-REQ-022)

### Integration Tests

- [X] T010 Add or preserve UI integration-style component coverage in `frontend/src/entrypoints/tasks-list.test.tsx` that renders the Tasks List, applies mobile filters, and verifies the resulting `/api/executions` request remains task-scoped. (Acceptance scenarios 1, 2, 7; FR-001, FR-002, FR-009)
- [X] T011 Add or preserve UI integration-style component coverage in `frontend/src/entrypoints/tasks-list.test.tsx` that opens a desktop filter dialog, stages a text filter, applies it with Enter, and verifies the request URL. (Acceptance scenarios 3, 5; FR-004, FR-006)
- [X] T012 Add or preserve UI integration-style component coverage in `frontend/src/entrypoints/tasks-list.test.tsx` that verifies cancel, Escape, and outside-click dismissal do not submit staged filter changes. (Acceptance scenario 4; FR-005)

### Red-First Confirmation

- [ ] T013 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` after T006-T012 and before T015-T018 to confirm newly authored or replayed tests fail for the intended reason on an unimplemented baseline. (FR-001 through FR-009)
- [X] T014 Record red-first or already-verified status in `specs/304-mobile-accessibility-live-update-stability/verification.md`; in the current workspace, cite the existing passing test evidence because `plan.md` marks all tracked rows `implemented_verified`. (SC-006)

### Implementation

- [X] T015 Add or preserve `taskId` and `title` text filters in `frontend/src/entrypoints/tasks-list.tsx` across filter types, empty state, parsing, URL serialization, summaries, active chips, desktop popovers, and mobile controls. (FR-001, FR-002, DESIGN-REQ-023)
- [X] T016 Add or preserve desktop filter focus management and Enter-to-apply handling in `frontend/src/entrypoints/tasks-list.tsx`. (FR-004, FR-006, DESIGN-REQ-022)
- [X] T017 Add or preserve the live-update stability guard in `frontend/src/entrypoints/tasks-list.tsx` so list refetch intervals pause while a filter editor is open. (FR-007, DESIGN-REQ-021)
- [X] T018 Preserve task-only scope enforcement, workflow-kind fail-safe behavior, active filter indicators, and mobile-card visibility in `frontend/src/entrypoints/tasks-list.tsx`. (FR-003, FR-008, FR-009, DESIGN-REQ-006)

### Story Validation

- [X] T019 Run focused unit and UI integration validation with `./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx` and record the result in `specs/304-mobile-accessibility-live-update-stability/verification.md`. (FR-001 through FR-010, SC-001 through SC-006)
- [ ] T020 Run final repository unit validation with `./tools/test_unit.sh` and record the result in `specs/304-mobile-accessibility-live-update-stability/verification.md`. (FR-001 through FR-010, SC-001 through SC-006)

## Phase 4: Polish And Verification

- [X] T021 Review `specs/304-mobile-accessibility-live-update-stability/contracts/tasks-list-filter-behavior.md` against `frontend/src/entrypoints/tasks-list.tsx` and `frontend/src/entrypoints/tasks-list.test.tsx` to confirm public UI behavior remains aligned. (FR-001 through FR-009)
- [X] T022 Review `specs/304-mobile-accessibility-live-update-stability/quickstart.md` and confirm it lists the focused and final validation commands for the story. (SC-001 through SC-006)
- [ ] T023 Run `/speckit.verify` and update `specs/304-mobile-accessibility-live-update-stability/verification.md` with final comparison against the original `MM-591` preset brief. (FR-010, SC-006)

## Dependencies and Execution Order

1. T001-T003 confirm the active feature artifacts and source/test scope.
2. T004-T005 establish source-design and implementation context.
3. T006-T012 author or preserve unit and UI integration tests before implementation work.
4. T013-T014 confirm red-first behavior on an unimplemented baseline, or preserve current already-verified evidence.
5. T015-T018 implement or preserve the bounded UI changes.
6. T019-T020 validate the story with focused and full unit runners.
7. T021-T023 complete contract, quickstart, and final `/speckit.verify` evidence.

## Parallel Examples

- T001 and T002 can run in parallel because they inspect different artifact files.
- T006, T007, T008, and T009 all touch `frontend/src/entrypoints/tasks-list.test.tsx`; draft them independently only in separate workspaces, then merge serially.
- T015, T016, T017, and T018 all touch `frontend/src/entrypoints/tasks-list.tsx` and must be applied serially.
- T021 and T022 can run in parallel because they inspect different MoonSpec artifacts.

## Implementation Strategy

The updated `plan.md` marks all tracked requirement, scenario, success-criterion, and source-design rows as `implemented_verified`. For the current workspace, execute this task list as a verification and traceability-preservation workflow. For a clean replay, keep the TDD ordering: author unit and UI integration tests first, run the focused test command to confirm red-first failure, implement the Tasks List UI changes, run focused validation, run the full unit suite, then run `/speckit.verify`.
