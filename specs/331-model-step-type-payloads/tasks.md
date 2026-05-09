# Tasks: Model Explicit Step Type Payloads and Validation

**Input**: Design documents from `/work/agent_jobs/mm:39219996-0c55-46b6-b755-ecb17f3bca83/repo/specs/331-model-step-type-payloads/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/step-type-validation-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around exactly one story: Validate Explicit Step Payloads.

**Source Traceability**: Preserves `MM-569`, `manual-mm-569-mm-574`, FR-001 through FR-011, SCN-001 through SCN-006, SC-001 through SC-006, and DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, and DESIGN-REQ-021.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused backend unit tests: `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py`
- Focused frontend unit tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Task Format

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work.
- Every task names exact file paths and requirement, scenario, success criterion, or source IDs where applicable.

## Phase 1: Setup

**Purpose**: Confirm the active MoonSpec artifacts and validation targets before test authoring.

- [ ] T001 Confirm `.specify/feature.json` points to `specs/331-model-step-type-payloads` and that `specs/331-model-step-type-payloads/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/step-type-validation-contract.md`, and `quickstart.md` preserve `MM-569` and `manual-mm-569-mm-574`.
- [ ] T002 Confirm the focused test targets exist in `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/api/test_task_step_templates_service.py`, `frontend/src/entrypoints/task-create-step-type.test.tsx`, `tests/integration/api/test_task_contract_normalization.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py`.
- [ ] T003 Confirm `specs/331-model-step-type-payloads/plan.md` `## Requirement Status` is still aligned with `specs/331-model-step-type-payloads/spec.md` before writing red-first tests for FR-001 through FR-011 and DESIGN-REQ-012 through DESIGN-REQ-021.

---

## Phase 2: Foundational

**Purpose**: Add reusable test scaffolding and inventory checks that block reliable story implementation.

**CRITICAL**: No production implementation work begins until foundational test fixtures and traceability inventory are ready.

- [ ] T004 [P] Add reusable Step Type payload fixture helpers for Tool, Skill, Preset, legacy, and mixed-payload cases in `tests/helpers/step_type_payloads.py` covering FR-001, FR-002, FR-009, DESIGN-REQ-012, DESIGN-REQ-013, and DESIGN-REQ-021.
- [ ] T005 [P] Add traceability inventory assertions for `MM-569`, `manual-mm-569-mm-574`, FR-001 through FR-011, SC-001 through SC-006, and DESIGN-REQ-012 through DESIGN-REQ-021 in `tests/unit/specs/test_mm569_traceability.py`.
- [ ] T006 Update `specs/331-model-step-type-payloads/contracts/step-type-validation-contract.md` only if red-first task design reveals a missing contract clause for field-addressable errors or unresolved Preset rejection, preserving FR-003, FR-007, and DESIGN-REQ-018.

**Checkpoint**: Shared test fixtures and traceability checks are ready; story test authoring can begin.

---

## Phase 3: Story - Validate Explicit Step Payloads

**Summary**: As a platform maintainer, I can validate draft and submitted steps as explicit Step Type payloads so invalid mixed-type payloads fail before execution.

**Independent Test**: Create draft and submission payloads for Tool, Skill, and Preset steps, then verify matching type-specific payloads pass, mixed or incomplete payloads fail with field-addressable errors, unresolved Preset runtime submission is blocked unless linked-preset mode is explicit, and `MM-569` plus `manual-mm-569-mm-574` traceability is preserved.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, DESIGN-REQ-021.

**Unit Test Plan**:

- `tests/unit/workflows/tasks/test_task_contract.py`: executable submission step model, mixed/missing payload failures, unresolved Preset rejection, field-addressable errors where exposed.
- `tests/unit/api/test_task_step_templates_service.py`: Tool, Skill, Preset, legacy-reader, generated-step, warning, and policy-limit validation.
- `frontend/src/entrypoints/task-create-step-type.test.tsx`: Preset failure state, input preservation, visible validation errors, and explicit normalized Step Type emission.
- `tests/unit/specs/test_mm569_traceability.py`: artifact traceability.

**Integration Test Plan**:

- `tests/integration/api/test_task_contract_normalization.py`: API-visible task contract normalization and field-addressable validation details.
- `tests/integration/temporal/test_task_shaped_submission_normalization.py`: executable submission boundary rejects unresolved Preset steps and preserves flat Tool/Skill provenance.

### Unit Tests (write first)

- [ ] T007 [P] Add failing unit tests for valid Tool, Skill, and Preset draft examples with stable identity, title/label, Step Type, and matching sub-payloads in `tests/unit/api/test_task_step_templates_service.py` covering FR-001, SCN-001, SC-001, DESIGN-REQ-012, and DESIGN-REQ-013.
- [ ] T008 [P] Add failing unit tests for executable Tool/Skill payloads, missing type-specific payloads, mixed payloads, and non-executable `type: preset` cases in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-002, FR-007, SCN-002, SCN-006, SC-002, SC-004, DESIGN-REQ-012, DESIGN-REQ-013, and DESIGN-REQ-018.
- [ ] T009 [P] Add failing unit tests for field-addressable Step Type validation errors in `tests/unit/workflows/tasks/test_task_contract.py` and `tests/unit/api/test_task_step_templates_service.py` covering FR-003, SC-003, and DESIGN-REQ-013.
- [ ] T010 [P] Add failing unit tests for Tool step validation in `tests/unit/api/test_task_step_templates_service.py` covering tool existence/version metadata, schema inputs, authorization metadata, worker capability, forbidden fields, retry policy, side-effect policy, and command-like tool policy rules for FR-004, SCN-003, DESIGN-REQ-014, and SC-005.
- [ ] T011 [P] Add failing unit tests for Skill step validation in `tests/unit/api/test_task_step_templates_service.py` covering skill resolution metadata, contract inputs, runtime compatibility, required context, allowed tools or permissions, and autonomy constraints for FR-005, SCN-004, DESIGN-REQ-015, and SC-005.
- [ ] T012 [P] Add failing unit tests for Preset validation in `tests/unit/api/test_task_step_templates_service.py` covering preset existence, active version, schema inputs, deterministic expansion, generated-step validation, policy limits, visible warnings, and failed expansion input preservation for FR-006, FR-008, SCN-005, DESIGN-REQ-018, SC-003, and SC-005.
- [ ] T013 [P] Add failing Create-page unit tests for failed Preset expansion preserving user inputs and visible field-addressable errors in `frontend/src/entrypoints/task-create-step-type.test.tsx` covering FR-008, SCN-005, and DESIGN-REQ-018.
- [ ] T014 [P] Add failing unit tests proving legacy-reader payloads remain accepted while new authoring/template output emits explicit normalized Step Type shapes in `tests/unit/api/test_task_step_templates_service.py` and `frontend/src/entrypoints/task-create-step-type.test.tsx` covering FR-009, DESIGN-REQ-021, and SC-005.

### Integration Tests (write first)

- [ ] T015 [P] Add failing API integration tests for unresolved Preset submission rejection and field-addressable validation details in `tests/integration/api/test_task_contract_normalization.py` covering FR-003, FR-007, SCN-006, SC-003, SC-004, DESIGN-REQ-013, and DESIGN-REQ-018.
- [ ] T016 [P] Add failing Temporal submission integration tests for flat executable Tool/Skill steps with preset provenance and no live preset runtime lookup in `tests/integration/temporal/test_task_shaped_submission_normalization.py` covering FR-010, SCN-001, SCN-006, DESIGN-REQ-012, DESIGN-REQ-018, and DESIGN-REQ-021.
- [ ] T017 [P] Add failing integration coverage for failed submit-time Preset expansion preserving input values and recoverable errors in `tests/integration/api/test_task_contract_normalization.py` covering FR-006, FR-008, SCN-005, SC-003, and DESIGN-REQ-018.

### Red-First Confirmation

- [ ] T018 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py` and confirm T007 through T012 and T014 fail for the expected missing Step Type validation behavior before production changes.
- [ ] T019 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx` and confirm T013 and the frontend portion of T014 fail for the expected missing Preset failure-preservation or explicit-emission behavior before production changes.
- [ ] T020 Run focused integration checks with `pytest tests/integration/api/test_task_contract_normalization.py tests/integration/temporal/test_task_shaped_submission_normalization.py -q --tb=short` and confirm T015 through T017 fail for the expected unresolved Preset or boundary-validation gaps before production changes.

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [ ] T021 If T008 shows existing mixed-payload rejection is incomplete, update executable step validation in `moonmind/workflows/tasks/task_contract.py` for FR-002, SCN-002, SC-002, DESIGN-REQ-012, and DESIGN-REQ-013.
- [ ] T022 If T015 shows unresolved Preset rejection is incomplete at the API boundary, update task submission normalization in `moonmind/workflows/tasks/task_contract.py` and `api_service/api/routers/executions.py` for FR-007, SCN-006, SC-004, and DESIGN-REQ-018.
- [ ] T023 If T005 or final traceability checks fail, update `specs/331-model-step-type-payloads/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/step-type-validation-contract.md`, `quickstart.md`, and `tasks.md` to preserve `MM-569`, `manual-mm-569-mm-574`, and the original preset brief for FR-011 and SC-006.

### Implementation

- [ ] T024 Update Step Payload model validation in `moonmind/workflows/tasks/task_contract.py` to require stable identity or generated label, explicit executable Step Type, exactly one matching Tool/Skill payload, field-addressable error details where exposed, and default rejection of unresolved Preset runtime steps for FR-001, FR-002, FR-003, FR-007, FR-010, DESIGN-REQ-012, DESIGN-REQ-013, and DESIGN-REQ-018.
- [ ] T025 Update template Step Type validation in `api_service/services/task_templates/catalog.py` to validate Tool, Skill, Preset authoring/expansion payloads, legacy-reader migration behavior, active preset version, generated-step validation, policy limits, warnings, and explicit normalized emissions for FR-001, FR-004, FR-005, FR-006, FR-008, FR-009, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, and DESIGN-REQ-021.
- [ ] T026 Update task payload compilation in `moonmind/workflows/tasks/payload.py` only if required by failing tests to preserve applied preset metadata, required capabilities, and executable Tool/Skill payload correctness for FR-010 and DESIGN-REQ-018.
- [ ] T027 Update Create-page Step Type behavior in `frontend/src/entrypoints/task-create.tsx` to preserve Preset inputs and visible field-addressable errors on failed expansion and emit explicit normalized Step Type payloads for FR-008, FR-009, SCN-005, DESIGN-REQ-018, and DESIGN-REQ-021.
- [ ] T028 Update API execution validation response handling in `api_service/api/routers/executions.py` only if T015 exposes missing structured validation details for FR-003, SC-003, and DESIGN-REQ-013.
- [ ] T029 Update any affected public types or generated TypeScript helpers in `frontend/src/entrypoints/task-create.tsx` or adjacent frontend type files if the Step Type payload shape changes for FR-001, FR-008, FR-009, DESIGN-REQ-012, and DESIGN-REQ-021.
- [ ] T030 Ensure no new persistent storage or secret-bearing payload fields are introduced while implementing `moonmind/workflows/tasks/task_contract.py`, `api_service/services/task_templates/catalog.py`, and `frontend/src/entrypoints/task-create.tsx` for Constitution principles II, IV, IX, XII, and XIII.

### Story Validation

- [ ] T031 Run `./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/test_task_step_templates_service.py` and verify backend unit coverage passes for FR-001 through FR-010, SC-001 through SC-005, and DESIGN-REQ-012 through DESIGN-REQ-021.
- [ ] T032 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create-step-type.test.tsx` and verify Create-page Step Type behavior passes for FR-008, FR-009, SCN-005, DESIGN-REQ-018, and DESIGN-REQ-021.
- [ ] T033 Run `pytest tests/integration/api/test_task_contract_normalization.py tests/integration/temporal/test_task_shaped_submission_normalization.py -q --tb=short` and verify focused integration coverage passes for FR-003, FR-007, FR-008, FR-010, SCN-001, SCN-005, SCN-006, and SC-003 through SC-005.
- [ ] T034 Run `rg -n "MM-569|manual-mm-569-mm-574|DESIGN-REQ-012|DESIGN-REQ-013|DESIGN-REQ-014|DESIGN-REQ-015|DESIGN-REQ-018|DESIGN-REQ-021" specs/331-model-step-type-payloads` and confirm traceability remains present for FR-011 and SC-006.

**Checkpoint**: The story is fully functional, covered by red-first unit and integration tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T035 [P] Refactor duplicated Step Type validation helpers in `moonmind/workflows/tasks/task_contract.py` and `api_service/services/task_templates/catalog.py` without changing the contract covered by FR-001 through FR-010.
- [ ] T036 [P] Review `specs/331-model-step-type-payloads/data-model.md`, `contracts/step-type-validation-contract.md`, and `quickstart.md` against the final implementation and update only feature-local artifacts if implementation evidence requires clarification for FR-011 and SC-006.
- [ ] T037 Run full unit verification with `./tools/test_unit.sh` and fix failures in the touched code or tests before proceeding.
- [ ] T038 Run hermetic integration verification with `./tools/test_integration.sh` and fix failures in the touched code or tests before proceeding.
- [ ] T039 Run quickstart validation commands from `specs/331-model-step-type-payloads/quickstart.md` and record any environment blockers in final verification evidence.
- [ ] T040 Run `/moonspec-verify` after implementation and tests pass, validating against `specs/331-model-step-type-payloads/spec.md`, `plan.md`, `tasks.md`, and the preserved `MM-569` Jira preset brief for FR-001 through FR-011, SC-001 through SC-006, and DESIGN-REQ-012 through DESIGN-REQ-021.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on Story validation passing.

### Within The Story

- Unit tests T007 through T014 must be written before implementation.
- Integration tests T015 through T017 must be written before implementation.
- Red-first confirmation T018 through T020 must complete before production code tasks T021 through T030.
- Conditional fallback implementation tasks T021 through T023 run only when verification tests expose gaps in implemented-unverified rows.
- Core implementation tasks T024 through T030 precede story validation T031 through T034.
- Full unit, integration, quickstart, and `/moonspec-verify` tasks run only after story validation succeeds.

### Parallel Opportunities

- T004 and T005 can run in parallel because they touch different test-support files.
- T007 through T017 can be split across backend unit, frontend unit, and integration files after foundational fixtures are available.
- T024, T025, and T027 should not run in parallel with tests that depend on their exact behavior, but can be assigned to separate implementers after red-first failures are captured because they touch distinct production files.
- T035 and T036 can run in parallel after story validation passes.

## Parallel Example

```bash
# After Phase 2, independent test authoring can be split:
Task: "T008 Add executable task contract Step Type tests in tests/unit/workflows/tasks/test_task_contract.py"
Task: "T012 Add Preset validation tests in tests/unit/api/test_task_step_templates_service.py"
Task: "T013 Add Create-page Preset failure tests in frontend/src/entrypoints/task-create-step-type.test.tsx"
Task: "T015 Add API integration unresolved Preset rejection tests in tests/integration/api/test_task_contract_normalization.py"
```

## Implementation Strategy

1. Preserve `MM-569`, `manual-mm-569-mm-574`, and original preset brief traceability before changing code.
2. Add fixture helpers and traceability checks.
3. Write unit and integration tests first across task contract, template catalog, Create page, and API/Temporal submission boundaries.
4. Run red-first commands and confirm failures are for the expected missing validation or preservation behavior.
5. Implement the smallest changes in existing boundaries: `moonmind/workflows/tasks/task_contract.py`, `api_service/services/task_templates/catalog.py`, `moonmind/workflows/tasks/payload.py`, `api_service/api/routers/executions.py`, and `frontend/src/entrypoints/task-create.tsx`.
6. Re-run focused unit and integration tests, then full `./tools/test_unit.sh` and `./tools/test_integration.sh`.
7. Run `/moonspec-verify` against the preserved source request and feature artifacts.

## Requirement Status Coverage Summary

- Code and tests required for partial rows: FR-001, FR-003, FR-004, FR-005, FR-006, FR-008, FR-009, FR-010, SCN-001, SCN-003, SCN-004, SCN-005, SC-001, SC-003, SC-005, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-018, DESIGN-REQ-021.
- Verification tests plus conditional fallback required for implemented-unverified rows: FR-002, FR-007, FR-011, SCN-002, SCN-006, SC-002, SC-004, SC-006.
- Already-verified rows: none.
- Final traceability validation required for all rows because final verification compares implementation against the original `MM-569` Jira preset brief.

## Notes

- This task list covers exactly one story.
- All production implementation is preceded by unit tests, integration tests, and red-first confirmation tasks.
- No commits, pull requests, Jira transitions, or implementation work are part of task generation.
