# Tasks: Composable Preset Expansion

**Input**: Design documents from `/specs/195-composable-preset-expansion/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: MM-383 Jira preset brief is preserved in `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-383-moonspec-orchestration-input.md`. Tasks cover FR-001 through FR-012, acceptance scenarios 1-6, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-010 plus DESIGN-REQ-025 and DESIGN-REQ-026.

**Test Commands**:

- Unit tests: `pytest tests/unit/api/test_task_step_templates_service.py -q`
- Integration tests: `./tools/test_integration.sh` when Docker is available
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm active feature pointer is `specs/195-composable-preset-expansion` in `.specify/feature.json`
- [X] T002 [P] Review existing task template catalog expansion service in `api_service/services/task_templates/catalog.py`
- [X] T003 [P] Review current task preset documentation in `docs/Tasks/TaskPresetsSystem.md`

## Phase 2: Foundational

- [X] T004 Define include-entry validation expectations for FR-001 through FR-003 in `tests/unit/api/test_task_step_templates_service.py`
- [X] T005 Define recursive expansion failure expectations for FR-005 through FR-007 in `tests/unit/api/test_task_step_templates_service.py`
- [X] T006 Define provenance and composition response expectations for FR-008 and FR-009 in `tests/unit/api/test_task_step_templates_service.py`

## Phase 3: Story - Composable Preset Expansion

**Summary**: As a task platform engineer, I want task presets to support pinned include entries that expand deterministically so reusable preset building blocks can be composed before runtime execution.

**Independent Test**: Create parent and child presets, expand the parent, and verify flattened steps, provenance, include rejection rules, cycle and limit failures, and executor-boundary documentation without running a Temporal workflow.

**Traceability**: FR-001-FR-012, SC-001-SC-005, DESIGN-REQ-001-DESIGN-REQ-010, DESIGN-REQ-025, DESIGN-REQ-026

### Unit Tests

- [X] T007 [P] Add failing unit test for successful child include flattening with deterministic IDs, provenance, composition metadata, and capabilities in `tests/unit/api/test_task_step_templates_service.py` (FR-001, FR-004, FR-008, FR-009, SC-001)
- [X] T008 [P] Add failing unit test rejecting global parent to personal child include in `tests/unit/api/test_task_step_templates_service.py` (FR-007, DESIGN-REQ-006, SC-002)
- [X] T009 [P] Add failing unit tests for include cycle paths, flattened limit paths, inactive child versions, and incompatible child inputs in `tests/unit/api/test_task_step_templates_service.py` (FR-005, FR-006, FR-007, DESIGN-REQ-007, DESIGN-REQ-009, SC-002)

### Integration Tests

- [X] T010 [P] Add failing API-boundary integration-style test proving expand responses retain `composition` metadata in `tests/unit/api/routers/test_task_step_templates.py` (FR-009, DESIGN-REQ-025, SC-003)

### Red-First Confirmation

- [X] T011 Run `pytest tests/unit/api/test_task_step_templates_service.py tests/unit/api/routers/test_task_step_templates.py -q` and confirm new unit and API-boundary tests fail for missing include behavior

### Implementation

- [X] T012 Implement `kind: step` / `kind: include` validation, pinned versions, aliases, and input mapping in `api_service/services/task_templates/catalog.py` (FR-001, FR-002, FR-003)
- [X] T013 Implement recursive include expansion, scope checks, inactive/missing/incompatible rejection, cycle detection, and flattened limit enforcement in `api_service/services/task_templates/catalog.py` (FR-004, FR-005, FR-006, FR-007)
- [X] T014 Implement flattened-step deterministic provenance and `composition` response metadata in `api_service/services/task_templates/catalog.py` and `api_service/api/schemas.py` (FR-008, FR-009)
- [X] T015 Update `docs/Tasks/TaskPresetsSystem.md` with composable preset terminology, include storage semantics, expansion pipeline, save-as-preset detachment semantics, and executor boundary rules (FR-010, FR-011, DESIGN-REQ-001-DESIGN-REQ-010, DESIGN-REQ-025, DESIGN-REQ-026)

### Story Validation

- [X] T016 Run `pytest tests/unit/api/test_task_step_templates_service.py tests/unit/api/routers/test_task_step_templates.py -q` until focused unit and API-boundary tests pass

### Integration And Story Validation

- [X] T017 Validate existing task template API/service contract compatibility through focused service tests and document Docker integration status in verification evidence (SC-003)
- [X] T018 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or document the exact blocker
- [X] T019 Run `./tools/test_integration.sh` when Docker is available or document the exact blocker

## Phase 4: Polish And Verification

- [X] T020 Review MM-383 traceability across `spec.md`, `tasks.md`, implementation notes, and docs (FR-012, SC-005)
- [X] T021 Run `/moonspec-verify` and record the final verification result

## Verification Notes

- Focused service/API command: `pytest tests/unit/api/test_task_step_templates_service.py tests/unit/api/routers/test_task_step_templates.py -q` passed with 27 tests.
- Full unit command: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3466 Python tests, 16 subtests, and 236 frontend tests.
- Hermetic integration command was not run because Docker is unavailable in this managed workspace: `dial unix /var/run/docker.sock: connect: no such file or directory`.
- Final `/moonspec-verify` verdict: `FULLY_IMPLEMENTED` with integration-suite execution blocked only by the workspace Docker socket, while service/API boundary tests cover the MM-383 composition contract.

## Dependencies & Execution Order

- Phase 1 before Phase 2.
- T004-T006 define test expectations before T007-T010.
- T012-T015 depend on failing tests from T011.
- T016 must pass before T017-T021.
- T018 and T019 are final-suite checks after focused tests pass.

## Implementation Strategy

Use the existing async service test fixture and catalog service boundary. Keep persistence unchanged by storing include entries in the existing version `steps` JSON. Preserve concrete-step-only compatibility and avoid changing executor behavior.
