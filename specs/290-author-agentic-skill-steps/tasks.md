# Tasks: Author Agentic Skill Steps

**Input**: Design documents from `/specs/290-author-agentic-skill-steps/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Red-first coverage is required before production edits; for this story, an MM-577-focused regression was added to existing Create-page coverage, and no production edit was required after tests demonstrated the current implementation already satisfied the story.

**Source Traceability**: MM-577; FR-001 through FR-008; SC-001 through SC-004; DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-019.

**Test Commands**:

- Unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py -q`
- Integration tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

## Phase 1: Setup

**Purpose**: Confirm active feature and source design scope.

- [X] T001 Confirm MM-577 canonical input exists at `artifacts/moonspec-inputs/MM-577-canonical-moonspec-input.md`.
- [X] T002 Confirm no existing `specs/` feature directory already preserves MM-577 as its source issue.
- [X] T003 Read `docs/Steps/StepTypes.md` sections 5.2, 6.4, and 8.3 for DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-019.

---

## Phase 2: Plan and Contract Evidence

**Purpose**: Map the story to existing Create-page and backend contract boundaries.

- [X] T004 [P] Document requirement status and existing implementation evidence in `specs/290-author-agentic-skill-steps/plan.md` (FR-001..FR-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-019).
- [X] T005 [P] Document Skill draft and payload data model in `specs/290-author-agentic-skill-steps/data-model.md` (FR-001, FR-004).
- [X] T006 [P] Document valid and invalid Skill step contracts in `specs/290-author-agentic-skill-steps/contracts/agentic-skill-step-authoring.md` (FR-002, FR-003, FR-006, FR-008).

---

## Phase 3: Story - Agentic Skill Step Authoring

**Summary**: As a task author, I can configure a Skill step for agentic work so interpretation, implementation, planning, and synthesis use reusable skill behavior with clear runtime boundaries.

**Independent Test**: Render the Create page, select/configure a Skill step with MM-577 args and required capabilities, submit it, and verify the payload remains an executable Skill step while invalid shapes are rejected.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, SC-004, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-019

### Red-First Unit Tests

- [X] T007 [P] Confirm unit coverage accepts explicit Skill discriminators and rejects non-skill Tool payloads on Skill steps in `tests/unit/workflows/tasks/test_task_contract.py` before considering production contract edits (FR-005, FR-006, SC-002, DESIGN-REQ-019).
- [X] T008 [P] Confirm unit coverage preserves Skill context/permissions/autonomy metadata in `tests/unit/api/test_task_step_templates_service.py` before considering production template edits (FR-004, DESIGN-REQ-010).

### Red-First Integration Tests

- [X] T009 Add focused MM-577 Create-page regression in `frontend/src/entrypoints/task-create.test.tsx` for submitted Skill selector, args, capabilities, Skill classification, and MM-577 traceability before considering Create-page production edits (FR-001, FR-002, FR-004, FR-007, FR-008, SC-001, DESIGN-REQ-009, DESIGN-REQ-010).
- [X] T010 Confirm existing Create-page invalid Skill Args coverage blocks non-object or malformed JSON before submission before considering validation edits (FR-003, FR-005, SC-002, DESIGN-REQ-019).

### Implementation

- [X] T011 Reuse existing Create-page Skill authoring and submission implementation in `frontend/src/entrypoints/task-create.tsx`; no production change required after MM-577 regression passed (FR-001..FR-008).
- [X] T012 Reuse existing task contract and template validation implementation in `moonmind/workflows/tasks/task_contract.py` and `api_service/services/task_templates/catalog.py`; no production change required (FR-003, FR-005, FR-006).

---

## Phase 4: Verification

- [X] T013 Run focused backend unit tests: `pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py -q`.
- [X] T014 Run setup-aware Create-page/full unit verification: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`.
- [X] T015 Story validation: verify MM-577 remains a single-story runtime feature, all FR/SC/DESIGN mappings are covered, and no production change is required beyond MM-577 regression evidence.
- [X] T016 Run final `/moonspec-verify` work and write final verification report in `specs/290-author-agentic-skill-steps/verification.md`.
