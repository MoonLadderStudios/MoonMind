# Tasks: Present Step Type Authoring

**Input**: Design documents from `/specs/281-step-type-authoring/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around MM-562's single user story.

**Source Traceability**: FR-001..FR-006, SC-001..SC-005, SCN-001..SCN-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-009, DESIGN-REQ-018.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm MM-562 artifacts and existing Create page Step Type implementation baseline.

- [X] T001 Create MoonSpec artifacts for MM-562 in `specs/281-step-type-authoring/`
- [X] T002 Confirm existing Create page implementation and tests in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

---

## Phase 2: Foundational

**Purpose**: No new backend, schema, service, or runtime foundation is required; this story refines existing Create page Step Type presentation.

- [X] T003 Verify existing Step Type selector, type switching, preset scoping, and hidden Skill field behavior are already implemented in `frontend/src/entrypoints/task-create.tsx`

**Checkpoint**: Foundation ready - remaining MM-562 work is helper-copy verification and UI presentation.

---

## Phase 3: Story - Step Type Authoring Presentation

**Summary**: As a task author, I can choose Tool, Skill, or Preset from a single Step Type control so the editor shows the right fields without requiring internal runtime vocabulary.

**Independent Test**: Render the Create page step editor, inspect the Step Type selector, switch among Tool, Skill, and Preset, and verify the selector choices, helper copy, visible configuration controls, instruction preservation, and absence of internal discriminator labels.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-009, DESIGN-REQ-018

**Test Plan**:

- Unit: Step Type helper copy, selector options, switching behavior, hidden field submission behavior.
- Integration: Create page render/submission tests exercise the public UI contract and task submission boundary.

### Unit Tests (write first)

- [X] T004 Add failing test for Tool, Skill, and Preset helper copy in `frontend/src/entrypoints/task-create.test.tsx` (FR-002, FR-005, SC-002, DESIGN-REQ-002, DESIGN-REQ-018)
- [X] T005 Confirm existing test for one Step Type control with Tool, Skill, and Preset choices in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, SC-001, DESIGN-REQ-001)
- [X] T006 Confirm existing test for switching Tool/Skill/Preset configuration areas and preserving instructions in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004, SC-003, DESIGN-REQ-009)
- [X] T007 Confirm existing test that hidden Skill fields are not submitted for Tool steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-004, SC-004)
- [X] T008 Confirm existing test for independent per-step Preset state in `frontend/src/entrypoints/task-create.test.tsx` (FR-006, SC-005)
- [X] T009 Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` to confirm the helper-copy test fails before implementation

### Implementation

- [X] T010 Add Step Type helper copy in `frontend/src/entrypoints/task-create.tsx` (FR-002, FR-005, DESIGN-REQ-002, DESIGN-REQ-018)
- [X] T011 Story validation: Run `npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx` and fix failures until focused frontend tests pass

**Checkpoint**: The story is functional and covered by focused Create page tests.

---

## Phase 4: Polish & Verification

**Purpose**: Validate without adding hidden scope.

- [X] T012 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- [X] T013 Run `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-562

---

## Dependencies & Execution Order

- T004 must be written before T010.
- T009 confirms red-first behavior before implementation.
- T011 validates focused behavior after implementation.
- T012-T013 are final validation.

## Implementation Strategy

1. Preserve MM-562 traceability in MoonSpec artifacts.
2. Add a failing focused frontend test for Step Type helper copy.
3. Add concise helper copy to the existing Step Type selector.
4. Run focused frontend and managed unit verification.
5. Verify artifacts and implementation against MM-562.
