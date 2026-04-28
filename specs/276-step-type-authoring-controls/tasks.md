# Tasks: Step Type Authoring Controls

**Input**: Design documents from `/specs/276-step-type-authoring-controls/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around MM-556's single user story.

**Source Traceability**: FR-001..FR-006, SC-001..SC-004, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-015.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing Create page frontend test harness and no new project structure is required.

- [X] T001 Confirm existing Create page implementation and test files in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`
- [X] T002 Create MoonSpec artifacts for MM-556 in `specs/276-step-type-authoring-controls/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new backend, schema, or service foundation is required; this story reuses existing preset and task submission surfaces.

- [X] T003 Verify existing preset expansion endpoint usage remains in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - Unified Step Type Selection

**Summary**: As a task author, I want one Step Type control in the step editor so I can choose Tool, Skill, or Preset without learning MoonMind internal runtime terminology.

**Independent Test**: Render the Create page, switch Step Type among Skill, Tool, and Preset, and verify only the relevant configuration area appears while compatible instructions persist.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-015

**Test Plan**:

- Unit: Step Type state, conditional rendering, hidden Skill field submission, canonical section order.
- Integration: Create page render/submission tests exercise the UI contract and existing task submission boundary.

### Unit Tests (write first) ⚠️

- [X] T004 [P] Add failing test for one Step Type control with Tool, Skill, and Preset in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, SC-001, DESIGN-REQ-001)
- [X] T005 [P] Add failing test for switching Skill/Tool/Preset configuration areas and preserving instructions in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, FR-003, FR-004, FR-006, SC-002, DESIGN-REQ-002)
- [X] T006 [P] Add failing test that hidden Skill advanced fields are not submitted after switching away from Skill in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, SC-003)
- [X] T007 [P] Update canonical section-order test to omit separate Task Presets authoring section in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, SC-004)
- [X] T008 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` to confirm new tests fail for expected Step Type gaps

### Integration Tests (write first) ⚠️

- [X] T009 Treat focused Create page Vitest render/submission coverage as the story integration boundary and confirm failures from T008 cover the public UI contract in `frontend/src/entrypoints/task-create.test.tsx`

### Implementation

- [X] T010 Add Step Type draft state and type-change handling in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-006)
- [X] T011 Render one Step Type control per step in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-005)
- [X] T012 Gate Skill controls and advanced Skill fields behind Skill Step Type in `frontend/src/entrypoints/task-create.tsx` (FR-002, FR-003, FR-006)
- [X] T013 Add Tool-specific configuration area in `frontend/src/entrypoints/task-create.tsx` (FR-002)
- [X] T014 Move preset-use controls into the Preset Step Type area in `frontend/src/entrypoints/task-create.tsx` (FR-002, FR-004)
- [X] T015 Update Mission Control styles for Step Type controls in `frontend/src/styles/mission-control.css` (FR-001, FR-002)
- [X] T016 Story validation: Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and fix failures until the story tests pass

**Checkpoint**: The story is functional, covered by focused frontend tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validate without adding hidden scope.

- [X] T017 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- [X] T018 Run `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-556

---

## Dependencies & Execution Order

- Phase 1 and Phase 2 are complete.
- T004-T007 must be written before implementation.
- T010-T015 follow red-first confirmation.
- T016 validates focused frontend behavior.
- T017-T018 are final validation.

## Implementation Strategy

1. Add failing frontend tests for the MM-556 UI contract.
2. Implement Step Type state and conditional rendering in the Create page.
3. Reuse existing preset expansion behavior inside the Preset Step Type area.
4. Run focused UI tests and managed unit validation.
5. Verify artifacts and implementation against MM-556.
