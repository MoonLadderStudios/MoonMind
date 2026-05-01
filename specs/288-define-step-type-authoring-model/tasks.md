# Tasks: Define Step Type Authoring Model

**Input**: Design documents from `specs/288-define-step-type-authoring-model/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and rendered Create page integration-style frontend tests are REQUIRED. This story spans UI authoring state and runtime payload validation.

**Source Traceability**: MM-575 Jira preset brief is preserved in `spec.md`. Tasks cover exactly one story: FR-001 through FR-006, acceptance scenarios 1-5, SC-001 through SC-005, and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-014.

**Test Commands**:

- Focused UI verification: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx`
- Runtime contract verification: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py`
- Unit/type validation: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

- [X] T001 Preserve MM-575 trusted Jira preset brief in `specs/288-define-step-type-authoring-model/spec.md` and `artifacts/moonspec/MM-575-orchestration-input.md` (SC-005)
- [X] T002 Confirm source requirements in `docs/Steps/StepTypes.md` cover DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-014
- [X] T003 Confirm relevant existing implementation and tests are in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create-step-type.test.tsx`, and `tests/unit/workflows/tasks/test_task_contract.py`

## Phase 2: Foundational Validation

- [X] T004 Verify existing draft state has explicit `stepType` and separated Skill, Tool, and Preset state in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-002)
- [X] T005 Verify runtime task contract rejects non-executable Step Type values and mixed payloads in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004, DESIGN-REQ-005)

## Phase 3: Story - Step Type Authoring Model

**Summary**: As a task author, I can choose exactly one Step Type for each step so the editor shows the right fields and uses consistent product terminology.

**Independent Test**: Render the Create page step editor, verify one Step Type selector with Tool, Skill, and Preset options, switch among types, and confirm visible forms, preserved instructions, explicit incompatible-data handling, runtime payload rejection, and terminology.

### Unit Tests (write/confirm first)

- [X] T006 Confirm runtime contract tests cover rejection of `preset`, `activity`, and `Activity` executable step types in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004, SC-004)
- [X] T007 Confirm runtime contract tests cover Tool-with-Skill and Skill-with-non-Skill-Tool mixed payload rejection in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004, SC-004)

### Integration Tests (write/confirm first)

- [X] T008 Confirm rendered Create page test covers one Step Type selector with Skill, Tool, and Preset options in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-001, SC-001)
- [X] T009 Confirm rendered Create page test covers preserved shared instructions and visible incompatible Skill discard on Step Type change in `frontend/src/entrypoints/task-create-step-type.test.tsx` (FR-002, FR-003, SC-002, SC-003)

### Red-First Confirmation

- [X] T010 Confirm no new production code is required because existing related Step Type implementation already includes explicit state, visible discard handling, and runtime rejection coverage (FR-001 through FR-006)

### Implementation

- [X] T011 Reuse existing Step Type selector rendering and `handleStepTypeChange` behavior in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-002, FR-003, FR-005, FR-006)
- [X] T012 Reuse existing runtime task contract validation in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004)

### Story Validation

- [X] T013 Run focused UI verification: `./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx` (FR-001, FR-002, FR-003, FR-005, FR-006)
- [X] T014 Run runtime contract verification: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py` (FR-004)
- [X] T015 Run unit/type validation: `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json` (FR-001 through FR-006)

## Phase 4: Polish & Verification

- [X] T016 Create feature-local planning, data model, contract, quickstart, checklist, and task artifacts in `specs/288-define-step-type-authoring-model/` (SC-005)
- [X] T017 Run `/moonspec-verify` equivalent read-only verification and write `specs/288-define-step-type-authoring-model/verification.md` (SC-005)

## Dependencies & Execution Order

- T001-T003 precede validation.
- T006-T009 are test-confirmation tasks and precede implementation closure.
- T013-T015 run after artifact creation and before final verification.
- T017 runs after tests pass or exact blockers are documented.

## Implementation Strategy

1. Preserve MM-575 and the trusted preset brief in feature artifacts.
2. Treat `docs/Steps/StepTypes.md` as runtime source requirements.
3. Reuse existing Step Type UI and runtime validation where evidence passes.
4. Run focused UI, runtime contract, and type validation.
5. Write final `verification.md` with requirement coverage and test evidence.
