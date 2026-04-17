# Tasks: Create Button Right Arrow

**Input**: Design documents from `specs/203-create-button-right-arrow/`
**Prerequisites**: plan.md, spec.md, research.md, quickstart.md, contracts/

**Tests**: Unit tests and UI/request-shape integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code.

**Test Commands**:

- Focused unit and UI integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Hermetic integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, FR-002, SC-001, acceptance scenario 1: primary Create action displays a right-pointing arrow while remaining recognizable.
- FR-003, FR-004, FR-005, SC-002, SC-005, acceptance scenarios 2 and 3: disabled, loading, validation, and explicit submit behavior remain unchanged.
- FR-006, SC-004, acceptance scenario 5, edge cases for narrow mobile and adjacent controls: primary action remains layout-stable.
- FR-007, SC-003, acceptance scenario 4: accessible action name continues to communicate Create or task creation.
- FR-008: automated coverage validates the presentation and behavior.
- FR-009, SC-006: MM-390 and original Jira preset brief remain visible in artifacts and verification.
- DESIGN-REQ-001: submit action remains in the existing shared Steps card submit area.
- DESIGN-REQ-002: submit remains explicit and no task is created by non-submit interactions.
- DESIGN-REQ-003: task-shaped create flow and payload meaning remain unchanged.
- Contract `contracts/create-button-right-arrow.md`: visible arrow, accessible name, unchanged submission behavior, responsive/state behavior, and verification obligations.

## Phase 1: Setup

**Purpose**: Confirm the active feature context and existing Create Page test surface before writing red tests.

- [ ] T001 Confirm MM-390 source input, single-story spec, and active feature locator in `docs/tmp/jira-orchestration-inputs/MM-390-moonspec-orchestration-input.md`, `specs/203-create-button-right-arrow/spec.md`, and `.specify/feature.json` (FR-009, SC-006).
- [ ] T002 Confirm existing Create Page submit control and test harness locations in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx` (DESIGN-REQ-001, DESIGN-REQ-003).

## Phase 2: Foundational

**Purpose**: Confirm there are no blocking schema, fixture, or tooling prerequisites before story test work.

- [ ] T003 Confirm no data model, migration, backend endpoint, or new dependency is required by `specs/203-create-button-right-arrow/plan.md` and `specs/203-create-button-right-arrow/contracts/create-button-right-arrow.md` (FR-005, DESIGN-REQ-003).
- [ ] T004 Confirm focused frontend test command is available for `frontend/src/entrypoints/task-create.test.tsx` using `specs/203-create-button-right-arrow/quickstart.md` (FR-008).

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

## Phase 3: Story - Create Button Right Arrow

**Summary**: As a Mission Control user, I want the Create Page primary submit action to use a right-pointing arrow so that the action visually communicates moving forward with task creation.

**Independent Test**: Open the Create Page with a valid task draft, inspect the primary submit action in normal, disabled, and loading states, and submit the draft. The story passes when the primary submit action displays a right-pointing arrow, retains an accessible Create action name, remains layout-stable, and submits through the same create flow as before.

**Traceability**: FR-001 through FR-009, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-003, MM-390.

### Unit Tests

- [ ] T005 Add failing unit-style render assertion for the Create action's right-pointing arrow and Create-oriented accessible name in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-002, FR-007, SC-001, SC-003, acceptance scenarios 1 and 4).
- [ ] T006 Add failing unit-style state assertion for disabled or validation behavior preserving the Create action identity in `frontend/src/entrypoints/task-create.test.tsx` (FR-005, FR-007, SC-002, acceptance scenario 2).
- [ ] T007 Add failing unit-style responsive/layout stability assertion for the Create action presentation in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, SC-004, acceptance scenario 5).

### Integration Tests

- [ ] T008 Add failing UI request-shape integration test proving a valid draft still submits through the existing create endpoint after the arrow presentation change in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004, FR-005, DESIGN-REQ-002, DESIGN-REQ-003, SC-005, acceptance scenario 3).
- [ ] T009 Add failing UI integration guardrail test proving Jira, preset, dependency, runtime, attachment, and publish controls are not changed by the Create action presentation in `frontend/src/entrypoints/task-create.test.tsx` (FR-005, DESIGN-REQ-003).

### Red-First Confirmation

- [ ] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T005-T009 fail for the expected missing right-arrow or unchanged-behavior assertions in `frontend/src/entrypoints/task-create.test.tsx`.

### Implementation

- [ ] T011 Implement the right-pointing arrow presentation on the primary Create action in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-002, DESIGN-REQ-001, SC-001).
- [ ] T012 Preserve accessible name, disabled state, loading state, and validation behavior while adding the arrow in `frontend/src/entrypoints/task-create.tsx` (FR-005, FR-006, FR-007, SC-002, SC-003, acceptance scenarios 2 and 4).
- [ ] T013 Preserve existing task submission and request payload behavior after the presentation change in `frontend/src/entrypoints/task-create.tsx` (FR-003, FR-004, FR-005, DESIGN-REQ-002, DESIGN-REQ-003, SC-005).
- [ ] T014 Update test expectations to their final passing form without weakening coverage in `frontend/src/entrypoints/task-create.test.tsx` (FR-001 through FR-008, SC-001 through SC-005).
- [ ] T015 Run focused unit and UI integration tests, then fix only MM-390-scoped failures in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx` (FR-001 through FR-008, DESIGN-REQ-001 through DESIGN-REQ-003).

### Story Validation

- [ ] T016 Validate the independent story manually or with test output evidence against `specs/203-create-button-right-arrow/spec.md` and `specs/203-create-button-right-arrow/contracts/create-button-right-arrow.md` (FR-001 through FR-009, SC-001 through SC-006).

**Checkpoint**: The story is fully functional, covered by unit and UI integration tests, and testable independently.

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T017 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and address only MM-390-scoped regressions in `frontend/src/entrypoints/task-create.tsx` or `frontend/src/entrypoints/task-create.test.tsx`.
- [ ] T018 Run hermetic integration suite with `./tools/test_integration.sh` when Docker is available, or record the exact Docker/runtime blocker in `specs/203-create-button-right-arrow/verification.md` (SC-005).
- [ ] T019 Confirm MM-390 traceability remains present in `specs/203-create-button-right-arrow/spec.md`, `specs/203-create-button-right-arrow/tasks.md`, implementation notes, commit text, and pull request metadata (FR-009, SC-006).
- [ ] T020 Run `/moonspec-verify` and record final verification evidence in `specs/203-create-button-right-arrow/verification.md` after implementation and tests pass (FR-009, SC-006).

## Dependencies & Execution Order

- T001-T004 must complete before story tests.
- T005-T009 must be written before T011-T014.
- T010 must confirm red-first failures before production implementation begins.
- T011-T014 must complete before T015.
- T016 runs after focused tests pass.
- T017-T020 run after story implementation is complete.

## Parallel Opportunities

- T001 and T002 can be done in parallel because they inspect different artifact groups.
- T003 and T004 can be done in parallel because they inspect planning artifacts and test command readiness.
- T005-T009 all modify `frontend/src/entrypoints/task-create.test.tsx`; draft carefully as one coherent red-test change rather than parallel edits.
- T011-T013 all modify `frontend/src/entrypoints/task-create.tsx`; implement as one coherent UI change rather than parallel edits.
- T019 can be prepared after T016 while full verification commands run.

## Implementation Strategy

1. Confirm setup and foundation tasks.
2. Write the focused test coverage in `frontend/src/entrypoints/task-create.test.tsx`.
3. Run the focused command and confirm the new tests fail for the intended reason.
4. Implement the Create action arrow and preserve behavior in `frontend/src/entrypoints/task-create.tsx`.
5. Run focused tests until they pass.
6. Run full unit verification and hermetic integration verification when available.
7. Run `/moonspec-verify` against the preserved MM-390 brief.

## Notes

- This task list covers exactly one story: MM-390 Create Button Right Arrow.
- Unit and UI integration tests are required before production implementation.
- Red-first confirmation is required before editing production code.
- Do not change task payload shape, Jira import behavior, presets, dependencies, runtime controls, attachments, publish controls, or backend execution behavior for this story.
