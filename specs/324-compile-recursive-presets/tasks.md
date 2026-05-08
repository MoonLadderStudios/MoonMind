# Tasks: Compile Recursive Task Presets

**Input**: `specs/324-compile-recursive-presets/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-compilation-contract.md`, `quickstart.md`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-compilation-contract.md`, and `quickstart.md` exist for one story.
**Unit Test Command**: `./tools/test_unit.sh`
**Integration Test Command**: `./tools/test_integration.sh`

## Source Traceability

MM-630 and the original Jira preset brief are preserved in `spec.md`. This task list covers exactly one story: Recursive Preset Compilation. Traceability spans FR-001 through FR-008, acceptance scenarios SCN-001 through SCN-006, edge cases, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-008, and the task preset compilation contract.

Requirement status from `plan.md`: partial rows require red-first tests and implementation; implemented_unverified rows require verification tests first plus conditional fallback implementation if verification fails; missing traceability requires final verification evidence. There are no implemented_verified rows.

## Phase 1: Setup

- [ ] T001 Confirm active feature locator points to `specs/324-compile-recursive-presets` in `.specify/feature.json`
- [ ] T002 Confirm `specs/324-compile-recursive-presets/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-compilation-contract.md`, and `quickstart.md` exist before implementation
- [ ] T003 Review current task preset catalog, task contract, Create page, execution router, worker runtime, and integration test fixtures in `api_service/services/task_templates/catalog.py`, `moonmind/workflows/tasks/task_contract.py`, `frontend/src/entrypoints/task-create.tsx`, `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py`

## Phase 2: Foundational

- [ ] T004 Identify or add reusable recursive preset fixture helpers for unit tests in `tests/unit/api/test_task_step_templates_service.py` and `tests/unit/api/routers/test_executions.py` without changing production behavior
- [ ] T005 Identify or add reusable recursive task submission fixture helpers for integration tests in `tests/integration/temporal/test_task_shaped_submission_normalization.py`
- [ ] T006 Confirm no database migration or new persistent table is required by reviewing existing task template and execution snapshot storage in `api_service/db/models.py` and `api_service/api/routers/executions.py`

## Phase 3: Story - Recursive Preset Compilation

**Story Summary**: Task authors can submit tasks with nested presets and receive one final ordered executable step list with durable provenance.

**Independent Test**: Submit a task draft containing manual steps plus a recursive preset include tree, then verify before execution begins that the task has deterministic flattened order, complete preset provenance, and a worker-facing payload that remains executable after live preset catalog changes.

**Traceability IDs**: FR-001 through FR-008; SCN-001 through SCN-006; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-008.

**Unit Test Plan**: Cover catalog validation/order/composition, task contract validation, API task normalization/snapshot metadata, and Create page submission payload preservation.

**Integration Test Plan**: Cover task-shaped submission with recursive preset metadata, no unresolved preset worker payload, live catalog unavailability after submission, and manual-only regression.

### Unit Tests First

- [ ] T007 [P] Add failing unit test for missing or unavailable recursive include targets in `tests/unit/api/test_task_step_templates_service.py` (FR-002, SCN-001, Edge: missing/unauthorized preset)
- [ ] T008 [P] Add failing unit test for deterministic repeated recursive expansion order and stable step IDs in `tests/unit/api/test_task_step_templates_service.py` (FR-003, SCN-002, SC-002, DESIGN-REQ-002)
- [ ] T009 [P] Add failing unit test that recursive expansion returns compact composition metadata suitable for authored preset bindings in `tests/unit/api/test_task_step_templates_service.py` (FR-001, FR-004, FR-006, SC-003, SC-005, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T010 [P] Add failing task contract unit test for recursive `authoredPresets` bindings with slug/version/alias/includePath/inputMapping/scope in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004, DESIGN-REQ-003)
- [ ] T011 [P] Add failing task contract unit test rejecting invalid unresolved preset worker-step shape when represented as executable task work in `tests/unit/workflows/tasks/test_task_contract.py` (FR-005, DESIGN-REQ-005)
- [ ] T012 [P] Add failing API route unit test that task-shaped submission preserves recursive `authoredPresets`, `appliedStepTemplates` composition, final order, runtime, publish, Jira provenance, and attachments in `tests/unit/api/routers/test_executions.py` (FR-001, FR-004, FR-006, FR-007, SCN-003, DESIGN-REQ-001)
- [ ] T013 [P] Add failing API route unit test that manual-only submission does not fabricate `authoredPresets`, composition, or preset source metadata in `tests/unit/api/routers/test_executions.py` (FR-007, SCN-006, SC-006)
- [ ] T014 [P] Add failing Create page unit test that recursive preset expansion submits `steps[].source`, `authoredPresets`, and composition metadata in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004, SCN-003)
- [ ] T015 [P] Add failing Create page unit test that auto-expanding an unresolved recursive preset before submit does not mutate the visible draft and still submits resolved executable steps in `frontend/src/entrypoints/task-create.test.tsx` (FR-001, FR-003, SCN-001, SCN-002)

### Integration Tests First

- [ ] T016 [P] Add failing hermetic integration test for recursive task-shaped submission preserving final flattened order and compiled provenance in `tests/integration/temporal/test_task_shaped_submission_normalization.py` (FR-001, FR-003, FR-004, SCN-001, SCN-002, SCN-003)
- [ ] T017 [P] Add failing hermetic integration test proving worker-facing task payload contains resolved executable steps and no unresolved preset include work after submission in `tests/integration/temporal/test_task_shaped_submission_normalization.py` (FR-005, SCN-004, SC-004, DESIGN-REQ-005)
- [ ] T018 [P] Add failing hermetic integration test simulating live preset catalog change or unavailability after submission while reconstruction uses submitted snapshot metadata in `tests/integration/temporal/test_task_shaped_submission_normalization.py` (FR-006, SCN-005, SC-005)
- [ ] T019 [P] Add integration regression for manual-only task submission staying unchanged in `tests/integration/temporal/test_task_shaped_submission_normalization.py` (FR-007, SCN-006, SC-006, DESIGN-REQ-007, DESIGN-REQ-008)

### Red-First Confirmation

- [ ] T020 Run focused catalog and task contract unit tests and confirm new tests fail for the expected missing behavior using `python -m pytest tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/tasks/test_task_contract.py -q` (FR-001 through FR-006)
- [ ] T021 Run focused API route unit tests and confirm new tests fail for the expected metadata/snapshot gaps using `python -m pytest tests/unit/api/routers/test_executions.py -q` (FR-001, FR-004, FR-006, FR-007)
- [ ] T022 Run focused Create page tests and confirm new tests fail for the expected recursive provenance submission gaps using `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004)
- [ ] T023 Run focused integration tests and confirm new tests fail for unresolved worker payload or missing reconstruction metadata using `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci` (FR-001 through FR-007)

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [ ] T024 If T007 shows missing or unauthorized include target handling is not already adequate, implement explicit include target failure behavior in `api_service/services/task_templates/catalog.py` (FR-002, SCN-001)
- [ ] T025 If T008 shows deterministic expansion is not stable, implement deterministic recursive ordering and stable step ID generation in `api_service/services/task_templates/catalog.py` (FR-003, SCN-002, SC-002)
- [ ] T026 If T011 shows unresolved preset work can enter worker-facing payloads, tighten task step validation in `moonmind/workflows/tasks/task_contract.py` (FR-005, DESIGN-REQ-005)
- [ ] T027 If T017 shows workers still depend on live catalog after submission, adjust worker task preparation in `moonmind/workflows/temporal/worker_runtime.py` to consume only submitted resolved steps (FR-005, SCN-004, SC-004)
- [ ] T028 If T013 or T019 shows manual-only submissions gain preset metadata, remove fabricated preset provenance from `api_service/api/routers/executions.py` or `frontend/src/entrypoints/task-create.tsx` (FR-007, SCN-006, SC-006)

### Production Implementation

- [ ] T029 Implement compact recursive composition output on template expansion in `api_service/services/task_templates/catalog.py` (FR-001, FR-004, FR-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004)
- [ ] T030 Implement derived authored preset binding metadata from recursive composition in `api_service/services/task_templates/catalog.py` or a focused helper module under `api_service/services/task_templates/` (FR-004, SC-003, DESIGN-REQ-003)
- [ ] T031 Preserve recursive composition metadata in `appliedTemplate` or submitted `appliedStepTemplates` without embedding large template content in `api_service/services/task_templates/catalog.py` (FR-006, SC-005, DESIGN-REQ-006)
- [ ] T032 Update Create page expansion/submission mapping to carry recursive `authoredPresets`, `appliedStepTemplates` composition, and `steps[].source` in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-003, FR-004, SCN-003)
- [ ] T033 Update API task-shaped normalization to preserve recursive `authoredPresets`, `appliedStepTemplates` composition, final step order, and existing runtime/publish/Jira/attachment fields in `api_service/api/routers/executions.py` (FR-001, FR-004, FR-006, FR-007)
- [ ] T034 Update task contract models only as needed for compact recursive composition or authored preset metadata in `moonmind/workflows/tasks/task_contract.py` (FR-004, DESIGN-REQ-003)
- [ ] T035 Update worker runtime preparation only as needed to persist and consume submitted resolved steps and compact preset provenance in `moonmind/workflows/temporal/worker_runtime.py` (FR-005, FR-006, DESIGN-REQ-005)
- [ ] T036 Ensure task snapshots preserve compiled recursive preset metadata without live catalog lookup in `api_service/api/routers/executions.py` and related snapshot helpers (FR-006, SCN-005, SC-005)
- [ ] T037 Ensure invalid recursive preset submissions produce explicit recoverable errors without creating execution work in `api_service/services/task_templates/catalog.py` and `api_service/api/routers/executions.py` (FR-002, SCN-001, SC-001)

### Story Validation

- [ ] T038 Re-run focused unit tests for catalog, task contract, API router, and Create page using `python -m pytest tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py -q` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` (FR-001 through FR-007)
- [ ] T039 Re-run focused hermetic integration coverage using `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci` (SCN-001 through SCN-006)
- [ ] T040 Verify the task preset compilation contract remains satisfied by comparing implementation behavior against `specs/324-compile-recursive-presets/contracts/task-preset-compilation-contract.md` (FR-001 through FR-007)
- [ ] T041 Verify `MM-630` and the original Jira preset brief remain preserved in `specs/324-compile-recursive-presets/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-compilation-contract.md`, `quickstart.md`, and `tasks.md` (FR-008)

## Final Phase: Polish And Verification

- [ ] T042 Refactor duplicated recursive preset provenance helpers after tests pass while keeping behavior unchanged in `api_service/services/task_templates/catalog.py`, `api_service/api/routers/executions.py`, and `frontend/src/entrypoints/task-create.tsx`
- [ ] T043 Run the full required unit suite with `./tools/test_unit.sh` (FR-001 through FR-008)
- [ ] T044 Run the required hermetic integration suite with `./tools/test_integration.sh` (SCN-001 through SCN-006)
- [ ] T045 Confirm no provider verification or credentialed tests are required for MM-630 and document any intentionally skipped provider checks in final verification notes in `specs/324-compile-recursive-presets/verification.md`
- [ ] T046 Run `/moonspec-verify` after implementation and tests pass, and ensure verification covers MM-630, the original Jira preset brief, FR-001 through FR-008, SCN-001 through SCN-006, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-008, commands run, and remaining risks

## Dependencies And Execution Order

1. Phase 1 setup tasks T001-T003 must complete first.
2. Phase 2 foundational tasks T004-T006 prepare reusable fixtures and confirm storage boundaries.
3. Unit tests T007-T015 and integration tests T016-T019 are written before production implementation.
4. Red-first confirmation T020-T023 must run before implementation tasks T024-T037.
5. Conditional fallback tasks T024-T028 are executed only if verification tests expose gaps in implemented_unverified rows.
6. Production implementation tasks T029-T037 complete partial and missing behavior.
7. Story validation T038-T041 proves the single story before final polish.
8. Final verification T042-T046 closes the feature.

## Parallel Examples

- T007, T010, T012, T014, and T016 can be drafted in parallel because they target different test files.
- T024, T025, and T030 all touch `api_service/services/task_templates/catalog.py`; do not run them in parallel.
- T032 and T033 can run in parallel after red-first confirmation because they touch frontend and backend route files separately, but both must be reconciled before T038.
- T043 and T044 should run after focused validation, not in parallel with implementation edits.

## Implementation Strategy

Start with red-first tests that expose the remaining partial behavior: recursive composition metadata, authored preset bindings, snapshot reconstruction, and no-live-catalog execution boundary. Preserve implemented_unverified rows through verification tests first, then execute the conditional fallback implementation tasks only when tests show gaps. Keep metadata compact and contract-shaped, avoid embedding large template content in workflow history, and preserve manual-only behavior without fabricated preset provenance.
