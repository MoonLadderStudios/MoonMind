# Tasks: Add Step Type Authoring Controls

**Input**: Design documents from `/specs/287-add-step-type-authoring-controls/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. This MM-568 story is verification-first because current Create page code already appears to implement the behavior; if verification fails, add failing tests first, confirm the failure, then implement the smallest code change needed.

**Organization**: Tasks are grouped by phase around MM-568's single user story.

**Source Traceability**: FR-001..FR-007, SC-001..SC-006, SCN-001..SCN-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-017.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx`
- Integration tests: `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create-step-type.test.tsx`
- Traceability: `rg -n "MM-568|DESIGN-REQ-001|DESIGN-REQ-002|DESIGN-REQ-008|DESIGN-REQ-017" specs/287-add-step-type-authoring-controls`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Create MM-568 artifacts and confirm the existing Create page Step Type implementation baseline.

- [X] T001 Create MoonSpec artifacts for MM-568 in `specs/287-add-step-type-authoring-controls/` (SC-006)
- [X] T002 Confirm existing Create page implementation and active tests in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create-step-type.test.tsx`, and `frontend/src/styles/mission-control.css` (FR-001..FR-007)

---

## Phase 2: Foundational

**Purpose**: No new backend, schema, service, or runtime foundation is required; this story verifies Create page Step Type authoring controls.

- [X] T003 Verify existing Step Type selector, helper copy, type switching, preset scoping, governed Tool validation, and hidden Skill-field behavior are already represented in current Create page code/tests (FR-001..FR-007, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-017)

**Checkpoint**: Foundation ready - remaining work is focused verification with contingency implementation only if tests fail.

---

## Phase 3: Story - Step Type Authoring Controls

**Summary**: As a task author, I can choose Tool, Skill, or Preset from one Step Type control so the editor shows only the configuration appropriate for that step.

**Independent Test**: Render the Create page step editor, inspect the Step Type selector, switch among Tool, Skill, and Preset, and verify the options, helper copy, visible configuration controls, instruction preservation, hidden-field safeguards, and absence of internal umbrella labels.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-008, DESIGN-REQ-017

**Test Plan**:

- Unit: Step Type selector, helper copy, type-specific rendering, instruction preservation, hidden-field submission behavior.
- Integration: Create page render/submission tests exercise the public UI contract and task submission boundary.

### Verification Tests

- [X] T004 Add active focused test for one Step Type control with Tool, Skill, and Preset choices in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-001, SC-001, DESIGN-REQ-001)
- [X] T005 Add active focused test for Tool, Skill, and Preset helper copy in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-002, FR-005, SC-002, DESIGN-REQ-002, DESIGN-REQ-017)
- [X] T006 Add active focused test for switching Tool/Skill/Preset configuration areas and preserving instructions in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-003, FR-004, SC-003, DESIGN-REQ-008)
- [X] T007 Add active focused test that hidden Skill fields are not submitted for Tool steps in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-006, SC-004)
- [X] T008 Add active focused test for independent per-step Preset state in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-007, SC-005)
- [X] T009 Run `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create-step-type.test.tsx` to verify the public UI contract (FR-001..FR-007)

### Contingency Implementation

- [X] T010 If T004 or T005 fails, update Step Type selector/options/helper copy in `frontend/src/entrypoints/task-create.tsx` and styling in `frontend/src/styles/mission-control.css` as needed. Not needed; active tests passed. (FR-001, FR-002, FR-005)
- [X] T011 If T006 or T007 fails, update Step Type switching, hidden-field submission, or Tool validation behavior in `frontend/src/entrypoints/task-create.tsx`. Not needed; active tests passed. (FR-003, FR-004, FR-006)
- [X] T012 If T008 fails, update per-step Preset draft state handling in `frontend/src/entrypoints/task-create.tsx`. Not needed; active tests passed. (FR-007)
- [X] T013 If any contingency code changes are made, rerun focused Vitest and fix failures until the story tests pass. Not needed beyond T009; no production code changes were required.

**Checkpoint**: The story is functional and covered by focused Create page tests.

---

## Phase 4: Polish & Verification

**Purpose**: Validate without adding hidden scope.

- [X] T014 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx` (SC-001..SC-005)
- [X] T015 Run traceability check `rg -n "MM-568|DESIGN-REQ-001|DESIGN-REQ-002|DESIGN-REQ-008|DESIGN-REQ-017" specs/287-add-step-type-authoring-controls` (SC-006)
- [X] T016 Run `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-568 (SC-006)

---

## Dependencies & Execution Order

- T001-T003 must complete before story verification.
- T004-T008 inspect existing focused tests before running validation.
- T009 validates focused frontend behavior.
- T010-T013 are conditional and run only if verification exposes a gap.
- T014-T016 are final validation.

## Implementation Strategy

1. Preserve MM-568 traceability in MoonSpec artifacts.
2. Verify current Create page code and focused tests against the MM-568 source brief.
3. Run focused frontend tests.
4. Apply contingency implementation only for concrete failing MM-568 requirements.
5. Run managed unit verification and final MoonSpec verification.
