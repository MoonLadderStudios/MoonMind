# Tasks: Agentic Skill Step Authoring

**Input**: Design documents from `/specs/283-agentic-skill-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. This story reuses existing Skill production behavior and adds MM-564-focused regression coverage.

**Source Traceability**: MM-564; FR-001 through FR-007; SC-001 through SC-004; DESIGN-REQ-005 and DESIGN-REQ-015.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py -q`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup

**Purpose**: Confirm the active feature and source design scope.

- [X] T001 Confirm MM-564 canonical input exists at `artifacts/moonspec-inputs/MM-564-canonical-moonspec-input.md`.
- [X] T002 Confirm no existing `specs/` feature directory already preserves MM-564 as its source issue.
- [X] T003 Read `docs/Steps/StepTypes.md` sections 5.2, 6.4, 8.3, and 9 for DESIGN-REQ-005 and DESIGN-REQ-015.

---

## Phase 2: Plan and Contract Evidence

**Purpose**: Map the story to existing Create-page and backend contract boundaries.

- [X] T004 [P] Document requirement status and existing implementation evidence in `specs/283-agentic-skill-steps/plan.md` (FR-001..FR-007, DESIGN-REQ-005, DESIGN-REQ-015).
- [X] T005 [P] Document Skill payload and agentic-control data model in `specs/283-agentic-skill-steps/data-model.md` (FR-001, FR-004).
- [X] T006 [P] Document valid and invalid Skill step contracts in `specs/283-agentic-skill-steps/contracts/agentic-skill-step-authoring.md` (FR-002, FR-003, FR-006).

---

## Phase 3: Story - Agentic Skill Authoring

**Summary**: As a task author, I want to configure Skill steps for agentic work so that interpretation, implementation, synthesis, and other open-ended work can be submitted with explicit skill controls.

**Independent Test**: Render the Create page, select/configure a Skill step with MM-564 args and required capabilities, submit it, and verify the payload remains an executable Skill step while invalid shapes are rejected.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-005, DESIGN-REQ-015

### Tests

- [X] T007 [P] Add focused MM-564 Create-page regression in `frontend/src/entrypoints/task-create.test.tsx` for submitted Skill selector, args, capabilities, and Skill classification (FR-001, FR-002, FR-004, FR-007, SC-001).
- [X] T008 [P] Confirm existing Create-page invalid Skill Args coverage blocks non-object or malformed JSON before submission (FR-003, SC-002).
- [X] T009 [P] Confirm existing task contract coverage accepts explicit Skill discriminators and rejects non-skill Tool payloads on Skill steps in `tests/unit/workflows/tasks/test_task_contract.py` (FR-005, FR-006, SC-003).
- [X] T010 [P] Confirm existing task-template service coverage preserves Skill context/permissions/autonomy metadata in `tests/unit/api/test_task_step_templates_service.py` (FR-004, DESIGN-REQ-005).

### Implementation

- [X] T011 Reuse existing Create-page Skill authoring and submission implementation in `frontend/src/entrypoints/task-create.tsx`; no production change required after MM-564 regression passed design review (FR-001..FR-007).
- [X] T012 Reuse existing task contract and template validation implementation in `moonmind/workflows/tasks/task_contract.py` and `api_service/services/task_templates/catalog.py`; no production change required (FR-003, FR-006).

---

## Phase 4: Verification

- [X] T013 Run focused backend unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py -q`.
- [X] T014 Run setup-aware Create-page/full unit verification: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`.
- [X] T015 Write final verification report in `specs/283-agentic-skill-steps/verification.md`.
