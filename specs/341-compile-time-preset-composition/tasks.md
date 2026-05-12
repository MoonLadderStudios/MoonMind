# Tasks: Compile-Time Preset Composition With Provenance Preservation

**Input**: `specs/341-compile-time-preset-composition/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-composition-contract.md`, `quickstart.md`
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-composition-contract.md`, and `quickstart.md` exist for one story.
**Unit Test Command**: `./tools/test_unit.sh`
**Integration Test Command**: `./tools/test_integration.sh`

## Source Traceability

MM-642 and the canonical Jira preset brief are preserved in `spec.md`. This task list covers exactly one story: Compile-Time Preset Composition. Traceability spans FR-001 through FR-008, acceptance scenarios 1-6, edge cases, SC-001 through SC-007, DESIGN-REQ-010, and DESIGN-REQ-011.

Requirement status from `plan.md`: FR-001 through FR-007, DESIGN-REQ-010, and DESIGN-REQ-011 are implemented_verified by existing production code and focused tests. FR-008 is implemented_unverified until this artifact set and final verification preserve MM-642 end to end. No production code changes are planned unless verification exposes drift.

## Phase 1: Setup

- [X] T001 Confirm active feature locator points to `specs/341-compile-time-preset-composition` in `.specify/feature.json`
- [X] T002 Confirm `specs/341-compile-time-preset-composition/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-composition-contract.md`, and `quickstart.md` exist before implementation
- [X] T003 Review current task preset catalog, task contract, Create page, execution router, worker runtime, and focused test fixtures in `api_service/services/task_templates/catalog.py`, `moonmind/workflows/tasks/task_contract.py`, `frontend/src/entrypoints/task-create.tsx`, `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/worker_runtime.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py`

## Phase 2: Foundational

- [X] T004 Confirm no database migration or new persistent table is required because existing snapshots and task payload metadata carry compact composition provenance (FR-004, FR-006, DESIGN-REQ-011)
- [X] T005 Confirm existing unit and integration test files already target catalog validation, task contract validation, API normalization, Create page payload preservation, worker runtime preparation, and task-shaped submission boundaries (FR-001 through FR-007)
- [X] T006 Confirm the related implemented recursive-preset work cannot be reused as the MM-642 source artifact because it preserves Jira key MM-630 rather than MM-642 (FR-008)

## Phase 3: Story - Compile-Time Preset Composition

**Story Summary**: Control-plane preset composition resolves recursive presets before execution submission and preserves final order plus provenance for workers, audit, rerun, and reconstruction.

**Independent Test**: Submit a task draft containing manual steps plus recursive preset-derived steps, then verify before execution starts that the submitted task has deterministic flattened steps, preserved `authoredPresets`, preserved `steps[].source`, and a worker-facing payload that does not require the live preset catalog.

**Traceability IDs**: FR-001 through FR-008; acceptance scenarios 1-6; SC-001 through SC-007; DESIGN-REQ-010; DESIGN-REQ-011.

**Unit Test Plan**: Verify catalog expansion and validation, task contract provenance validation, worker runtime resolved payload handling, API task-shaped normalization, manual-only regression, and Create page submission provenance.

**Integration Test Plan**: Verify task-shaped submission preserves final flattened order, compact provenance, no unresolved worker payload, reconstruction metadata, and manual-only behavior.

### Verification Tests First

- [X] T007 [P] Verify catalog unit coverage for recursive composition metadata, authored presets, validation failures, deterministic order, and stable source provenance in `tests/unit/api/test_task_step_templates_service.py` (FR-001, FR-002, FR-003, FR-004, DESIGN-REQ-010, DESIGN-REQ-011)
- [X] T008 [P] Verify task contract unit coverage for recursive `authoredPresets`, `steps[].source`, detached provenance, and rejection of unresolved preset include work in `tests/unit/workflows/tasks/test_task_contract.py` (FR-004, FR-005, DESIGN-REQ-011)
- [X] T009 [P] Verify worker runtime unit coverage for resolved template expansion metadata and worker-facing task payload behavior in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` (FR-005, DESIGN-REQ-010)
- [X] T010 [P] Verify API route unit coverage for preserving recursive `authoredPresets`, `appliedStepTemplates` composition, final order, runtime, publish, Jira provenance, attachments, and manual-only regression in `tests/unit/api/routers/test_executions.py` (FR-003, FR-004, FR-006, FR-007)
- [X] T011 [P] Verify Create page unit coverage for recursive preset expansion submission and non-mutating auto-expansion in `frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004)
- [X] T012 [P] Verify hermetic integration coverage for task-shaped submission preserving flattened order, compiled provenance, no unresolved worker payload, catalog-independent reconstruction metadata, and manual-only regression in `tests/integration/temporal/test_task_shaped_submission_normalization.py` (FR-001 through FR-007)

### Red-First Confirmation Status

- [X] T013 Record that new red-first test authoring is not needed for MM-642 because the same behavior was already driven by prior recursive-preset tests and the current plan is verification-first against existing implementation evidence (FR-001 through FR-007)
- [X] T014 If any verification test fails, reclassify the affected requirement from implemented_verified to partial in `specs/341-compile-time-preset-composition/plan.md` before editing production code (FR-001 through FR-007)

### Conditional Fallback Implementation

- [X] T015 If T007 exposes catalog validation or composition drift, update `api_service/services/task_templates/catalog.py` and rerun catalog tests (FR-001, FR-002, FR-003, DESIGN-REQ-010)
- [X] T016 If T008 exposes task contract provenance drift, update `moonmind/workflows/tasks/task_contract.py` and rerun task contract tests (FR-004, FR-005, DESIGN-REQ-011)
- [X] T017 If T009 exposes worker live-catalog dependency drift, update `moonmind/workflows/temporal/worker_runtime.py` and rerun worker runtime tests (FR-005)
- [X] T018 If T010 or T012 exposes snapshot or normalization drift, update `api_service/api/routers/executions.py` and rerun API/integration tests (FR-004, FR-006, FR-007)
- [X] T019 If T011 exposes Create page submission drift, update `frontend/src/entrypoints/task-create.tsx` and rerun focused UI tests (FR-003, FR-004)

### Story Validation

- [X] T020 Run focused backend unit tests with `python -m pytest tests/unit/api/test_task_step_templates_service.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/api/routers/test_executions.py -q` (FR-001 through FR-007)
- [X] T021 Run focused Create page validation with `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` (FR-003, FR-004)
- [X] T022 Run focused hermetic integration validation with `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci` (FR-001 through FR-007, SC-001 through SC-006)
- [X] T023 Verify the task preset composition contract remains satisfied by comparing implementation behavior against `specs/341-compile-time-preset-composition/contracts/task-preset-composition-contract.md` (FR-001 through FR-007)
- [X] T024 Verify `MM-642`, DESIGN-REQ-010, DESIGN-REQ-011, and the canonical Jira preset brief remain preserved in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-preset-composition-contract.md`, `quickstart.md`, and `tasks.md` (FR-008, SC-007)

## Final Phase: Polish And Verification

- [X] T025 Run the full required unit suite with `./tools/test_unit.sh` (FR-001 through FR-008)
- [X] T026 Run the required hermetic integration suite with `./tools/test_integration.sh` or record the exact environmental blocker if Docker is unavailable: Docker returned `403 Forbidden` after the buildx warning in this managed environment (SC-001 through SC-006)
- [X] T027 Confirm no provider verification or credentialed tests are required for MM-642 and document skipped provider checks in `specs/341-compile-time-preset-composition/verification.md`
- [X] T028 Run `/moonspec-verify` after implementation and tests pass or are blocked with exact reasons, ensuring verification covers MM-642, the preserved Jira preset brief, FR-001 through FR-008, SC-001 through SC-007, DESIGN-REQ-010, DESIGN-REQ-011, commands run, and remaining risks

## Dependencies And Execution Order

1. Phase 1 setup tasks T001-T003 must complete first.
2. Phase 2 foundational tasks T004-T006 confirm existing boundaries and evidence.
3. Verification tasks T007-T012 are evaluated before any production edits.
4. Conditional fallback tasks T015-T019 are only executed if verification exposes drift.
5. Story validation tasks T020-T024 prove MM-642 against current code and artifacts.
6. Final verification tasks T025-T028 close the feature.

## Parallel Examples

- T007, T008, T009, T010, T011, and T012 can be evaluated in parallel because they inspect different test files or boundaries.
- T015 through T019 must not be run unless the corresponding verification test fails.
- T020, T021, and T022 should run after artifact generation and before final verification.

## Implementation Strategy

This MM-642 run is verification-first because the same compile-time preset composition behavior is already present in the repository from prior recursive-preset implementation. Preserve the new Jira source trace in this artifact set, rerun focused and required tests, edit production code only if current evidence fails, and finish with MoonSpec verification against the `MM-642` brief.
