# Tasks: Validate Tool and Skill Executable Steps

**Input**: Design documents from `/specs/277-validate-tool-skill-executable-steps/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped by phase around MM-557's single user story.

**Source Traceability**: FR-001..FR-014, SC-001..SC-005, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-017.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py`
- Integration tests: focused async task template service tests in `tests/unit/api/test_task_step_templates_service.py` exercise create/save/expand service boundaries without compose
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create MM-557 MoonSpec artifacts and confirm the service boundary.

- [X] T001 Create MoonSpec artifacts for MM-557 in `specs/277-validate-tool-skill-executable-steps/`
- [X] T002 Confirm executable step validation currently lives at task template schema/service boundaries in `api_service/api/schemas.py`, `api_service/services/task_templates/catalog.py`, and `api_service/services/task_templates/save.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new persistence or external services are required; validation will reuse existing task template errors.

- [X] T003 Verify no database migration or external registry dependency is required for structural executable step validation in `api_service/services/task_templates/catalog.py`

**Checkpoint**: Foundation ready - story test and implementation work can begin

---

## Phase 3: Story - Validate Executable Step Contracts

**Summary**: As an operator, I want Tool and Skill executable steps validated against distinct contracts so deterministic operations and agentic work are configured safely before execution.

**Independent Test**: Create/save/expand task step templates containing Tool and Skill steps and verify valid examples persist while mixed, malformed, forbidden, or shell-shaped steps fail with validation errors.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-013, DESIGN-REQ-017

**Test Plan**:

- Unit: task template catalog/save service validation for Tool and Skill payloads.
- Integration boundary: async service tests persist and expand templates through the same service paths used by API routes.

### Unit Tests (write first)

- [X] T004 [P] Add failing test for valid Jira transition Tool step persistence and expansion in `tests/unit/api/test_task_step_templates_service.py` (FR-001, FR-002, FR-003, FR-004, FR-011, SC-001, SC-004, DESIGN-REQ-003, DESIGN-REQ-013)
- [X] T005 [P] Add failing test for explicit Jira triage Skill step and legacy Skill-shaped step validation in `tests/unit/api/test_task_step_templates_service.py` (FR-005, FR-006, FR-007, FR-012, SC-001, DESIGN-REQ-004)
- [X] T006 [P] Add failing tests for unsupported Step Type and mixed Tool/Skill payload rejection in `tests/unit/api/test_task_step_templates_service.py` (FR-008, FR-009, FR-010, FR-013, SC-002, DESIGN-REQ-005, DESIGN-REQ-013)
- [X] T007 [P] Add failing tests for arbitrary shell field rejection and bounded typed command Tool acceptance in `tests/unit/api/test_task_step_templates_service.py` (FR-014, SC-003, DESIGN-REQ-017)
- [X] T008 Run `./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` to confirm new tests fail for expected validation gaps

### Integration Tests (write first)

- [X] T009 Treat async create/save/expand tests in `tests/unit/api/test_task_step_templates_service.py` as the service integration boundary for MM-557 because they exercise persistence and expansion without external dependencies

### Implementation

- [X] T010 Add Step Type, Tool payload, and Skill metadata request/response schema fields in `api_service/api/schemas.py` (FR-001, FR-004, FR-005, FR-007)
- [X] T011 Add common executable Step Type normalization and shell-field rejection in `api_service/services/task_templates/catalog.py` (FR-008, FR-010, FR-014)
- [X] T012 Add Tool step validation and normalization in `api_service/services/task_templates/catalog.py` (FR-001, FR-002, FR-003, FR-004, FR-011)
- [X] T013 Add explicit Skill step validation, broader Skill metadata preservation, and mixed payload rejection in `api_service/services/task_templates/catalog.py` (FR-005, FR-006, FR-007, FR-009, FR-012, FR-013)
- [X] T014 Preserve and validate Tool/Skill step payloads in save-from-task sanitization in `api_service/services/task_templates/save.py` (FR-001, FR-004, FR-005, FR-007, FR-010)
- [X] T015 Preserve normalized Tool and Skill Step Type payloads during expansion in `api_service/services/task_templates/catalog.py` (FR-004, FR-007, SC-004)
- [X] T016 Story validation: Run `./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py` and fix failures until MM-557 tests pass

**Checkpoint**: The story is functional, covered by focused service tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Validate without adding hidden scope.

- [X] T017 Run `./tools/test_unit.sh`
- [X] T018 Run `/moonspec-verify` equivalent by checking spec, plan, tasks, changed code, and test evidence against MM-557

---

## Dependencies & Execution Order

- Phase 1 and Phase 2 are complete.
- T004-T007 must be written before implementation.
- T010-T015 follow red-first confirmation.
- T016 validates focused backend behavior.
- T017-T018 are final validation.

## Implementation Strategy

1. Add failing task template service tests for the MM-557 Tool/Skill validation contract.
2. Extend API schemas and service sanitization to preserve explicit Step Type payloads.
3. Implement common, Tool-specific, Skill-specific, and shell-snippet validation.
4. Reuse existing template validation error paths so failures surface before persistence or expansion.
5. Run focused unit tests, then the managed unit suite when feasible.
6. Verify artifacts and implementation against MM-557.
