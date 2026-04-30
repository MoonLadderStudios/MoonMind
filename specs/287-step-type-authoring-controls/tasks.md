# Tasks: Add Step Type Authoring Controls

**Input**: Design documents from `specs/287-step-type-authoring-controls/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style frontend component tests are REQUIRED. This story is UI-only; the Create page Vitest suite is the integration boundary for the rendered authoring workflow.

**Source Traceability**: MM-568 Jira preset brief is preserved in `spec.md`. Tasks cover FR-001 through FR-006, acceptance scenarios 1-5, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, and DESIGN-REQ-017.

**Test Commands**:

- Focused UI verification: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx`
- Full unit verification: `./tools/test_unit.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing frontend test and source locations for the story.

- [X] T001 Confirm active feature artifacts preserve MM-568 in `specs/287-step-type-authoring-controls/spec.md` (SC-006)
- [X] T002 Confirm Create page source and test files in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new infrastructure is needed; use existing Create page draft state and Vitest harness.

- [X] T003 Verify existing Step Type selector state and helper-copy coverage in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-002, FR-003, FR-005, FR-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-017)

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - Step Type Authoring Controls

**Summary**: As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.

**Independent Test**: Render the Create page step editor, verify one Step Type selector with Tool, Skill, and Preset options, switch among types, and confirm visible forms, preserved instructions, explicit incompatible-data discard feedback, helper copy, and label vocabulary.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-017

**Test Plan**:

- Unit/UI: Create page Vitest tests for selector, helper copy, switching panels, independent step state, and incompatible data discard feedback.
- Integration: Rendered Create page workflow through Testing Library; no backend or compose integration is changed.

### Tests (write first)

- [X] T004 Add focused Step Type test for visible incompatible Skill data discard in `frontend/src/entrypoints/task-create-step-type.test.tsx` and align existing skipped coverage in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, SC-003, DESIGN-REQ-008)
- [X] T005 Run focused UI verification with `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx` and record result (FR-001 through FR-006)

### Implementation

- [X] T006 Add transient Step Type discard feedback state in `frontend/src/entrypoints/task-create.tsx` (FR-004, DESIGN-REQ-008)
- [X] T007 Update `handleStepTypeChange` in `frontend/src/entrypoints/task-create.tsx` to preserve shared instructions while clearing incompatible Skill, Tool, or Preset configuration with visible feedback (FR-004, SC-003, DESIGN-REQ-008)
- [X] T008 Render the Step Type discard notice in `frontend/src/entrypoints/task-create.tsx` near the Step Type selector (FR-004)

**Checkpoint**: The story is functionally complete once T005 passes.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Verify feature-local artifacts and final test evidence.

- [X] T009 Create feature-local planning, data model, contract, and quickstart artifacts in `specs/287-step-type-authoring-controls/` (SC-006)
- [X] T010 Run Python unit suite and dashboard tests for final required unit verification
- [X] T011 Run `/speckit.verify` equivalent read-only verification against `specs/287-step-type-authoring-controls/spec.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup completion
- **Story (Phase 3)**: Depends on Foundational completion
- **Polish (Phase 4)**: Depends on story implementation and focused test evidence

### Within The Story

- T004 should precede T006-T008 in strict TDD runs.
- T005 validates T004 and T006-T008 together.
- T010 and T011 are final verification tasks.

### Parallel Opportunities

- Feature artifact review and focused UI test authoring can run independently before implementation.
- No production code tasks should run in parallel because the story touches one frontend source file.

## Implementation Strategy

1. Preserve MM-568 and the trusted preset brief in `spec.md`.
2. Reuse existing Step Type selector implementation and tests for already-covered requirements.
3. Complete the incompatible-data gap by visibly discarding prior type-specific state on Step Type changes.
4. Verify through focused Create page UI tests, then full unit verification when feasible.
5. Run final MoonSpec verification and preserve evidence in the final report.
