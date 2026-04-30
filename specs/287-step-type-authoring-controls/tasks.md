# Tasks: Add Step Type Authoring Controls

**Input**: Design documents from `specs/287-step-type-authoring-controls/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style frontend component tests are REQUIRED. This story is UI-only; the Create page Vitest boundary is the executable integration surface for the rendered authoring workflow.

**Source Traceability**: MM-568 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story: FR-001 through FR-006, acceptance scenarios 1-5, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, and DESIGN-REQ-017.

**Test Commands**:

- Unit/type validation: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- Focused integration UI verification: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx`
- Dashboard integration suite: `./tools/test_unit.sh --dashboard-only`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing frontend source, test tooling, and feature-local artifact structure for the story.

- [X] T001 Confirm active feature artifacts preserve MM-568 in `specs/287-step-type-authoring-controls/spec.md` (SC-006)
- [X] T002 Confirm Create page source and test files in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, and `frontend/src/entrypoints/task-create-step-type.test.tsx`
- [X] T003 Confirm frontend unit/type and dashboard integration commands are available through `./node_modules/.bin/tsc` and `./tools/test_unit.sh --dashboard-only`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Reuse existing Create page draft state and Vitest harness; no new infrastructure is required.

- [X] T004 Verify existing Step Type selector state and helper-copy coverage in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-002, FR-003, FR-005, FR-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-017)
- [X] T005 Verify the MM-568 gap from `specs/287-step-type-authoring-controls/research.md`: incompatible Step Type data needed visible discard handling (FR-004, SC-003, DESIGN-REQ-008)

**Checkpoint**: Foundation ready - one-story test and implementation work can begin

---

## Phase 3: Story - Step Type Authoring Controls

**Summary**: As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.

**Independent Test**: Render the Create page step editor, verify one Step Type selector with Tool, Skill, and Preset options, switch among types, and confirm visible forms, preserved instructions, explicit incompatible-data discard feedback, helper copy, and label vocabulary.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, acceptance scenarios 1-5, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-017

**Unit Test Plan**:

- Type-check the Create page Step Type state model and focused test harness.
- Command: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`

**Integration Test Plan**:

- Render `TaskCreatePage`, interact with the Step Type selector, and verify visible UI behavior through Testing Library.
- Command: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx`
- Broader dashboard regression command: `./tools/test_unit.sh --dashboard-only`

### Unit Tests (write first)

- [X] T006 Add focused TypeScript-covered test harness for Step Type behavior in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-001, FR-004, SC-001, SC-003)
- [X] T007 Run `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` to validate the Step Type state and test harness types (FR-004)

### Integration Tests (write first)

- [X] T008 Add focused rendered Create page integration test in `frontend/src/entrypoints/task-create-step-type.test.tsx` for one Step Type selector, preserved shared instructions, visible Skill discard notice, and cleared incompatible Skill fields (FR-001, FR-003, FR-004, SC-001, SC-002, SC-003, DESIGN-REQ-008)
- [X] T009 Align existing skipped Create page coverage in `frontend/src/entrypoints/task-create.test.tsx` with the new incompatible-data discard expectation for future unskip work (FR-004, SC-003)

### Red-First Confirmation

- [X] T010 Confirm the pre-implementation behavior gap from `specs/287-step-type-authoring-controls/research.md`: `handleStepTypeChange` previously only changed `stepType` and cleared `presetPreview`, so incompatible Skill, Tool, and Preset values were hidden rather than visibly discarded (FR-004, DESIGN-REQ-008)
- [X] T011 Confirm the focused integration test exercises the gap by asserting the visible discard notice and cleared Skill fields in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-004, SC-003)

### Implementation

- [X] T012 Add transient Step Type discard feedback state in `frontend/src/entrypoints/task-create.tsx` (FR-004, DESIGN-REQ-008)
- [X] T013 Update `handleStepTypeChange` in `frontend/src/entrypoints/task-create.tsx` to preserve shared instructions while clearing incompatible Skill, Tool, or Preset configuration with visible feedback (FR-004, SC-003, DESIGN-REQ-008)
- [X] T014 Render the Step Type discard notice in `frontend/src/entrypoints/task-create.tsx` near the Step Type selector (FR-004)

### Story Validation

- [X] T015 Run focused integration UI verification: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx` (FR-001 through FR-006, SC-001 through SC-005)
- [X] T016 Run dashboard integration regression suite: `./tools/test_unit.sh --dashboard-only` (FR-001 through FR-006)
- [X] T017 Run unit/type validation: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` (FR-004)

**Checkpoint**: The single story is fully functional, covered by type validation and rendered UI integration tests, and testable independently.

---

## Phase 4: Polish & Verification

**Purpose**: Verify feature-local artifacts and final test evidence without adding hidden scope.

- [X] T018 Create feature-local planning, data model, contract, quickstart, and checklist artifacts in `specs/287-step-type-authoring-controls/` (SC-006)
- [X] T019 Preserve MM-568 and the original Jira preset brief in `specs/287-step-type-authoring-controls/spec.md` for final verification (SC-006)
- [X] T020 Run broader Python unit verification through `./tools/test_unit.sh` as final required unit evidence where environment allows
- [X] T021 Run `/moonspec-verify` equivalent read-only verification against `specs/287-step-type-authoring-controls/spec.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion
- **Story (Phase 3)**: Depends on Foundational completion
- **Polish & Verification (Phase 4)**: Depends on story implementation and validation

### Within The Story

- Unit test tasks T006-T007 precede implementation tasks T012-T014.
- Integration test tasks T008-T009 precede implementation tasks T012-T014.
- Red-first confirmation tasks T010-T011 document the verified FR-004 gap before implementation.
- Story validation tasks T015-T017 run after implementation.
- Final `/moonspec-verify` task T021 runs after tests pass.

### Parallel Opportunities

- T006 and T008 can be prepared in parallel because they touch the focused test file and validate different test dimensions.
- T007 and T015-T016 are command-only validation tasks and should run after the relevant tests exist.
- No production implementation tasks should run in parallel because the story touches one frontend source file.

## Implementation Strategy

1. Preserve MM-568 and the trusted preset brief in `spec.md`.
2. Reuse existing Step Type selector implementation and tests for already-covered requirements.
3. Add focused unit/type and rendered UI integration coverage for the incompatible-data gap.
4. Confirm the existing gap in `handleStepTypeChange`, then visibly discard prior type-specific state on Step Type changes.
5. Validate with focused UI, full dashboard, type-check, broader unit verification, and final `/moonspec-verify`.

## Notes

- This task list covers exactly one story.
- The public/integration boundary is the rendered Create page step editor.
- Existing `frontend/src/entrypoints/task-create.test.tsx` remains intentionally skipped by its outer `describe.skip`; the executable MM-568 regression coverage lives in `frontend/src/entrypoints/task-create-step-type.test.tsx`.
