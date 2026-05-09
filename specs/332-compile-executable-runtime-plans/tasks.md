# Tasks: Compile Executable Steps into Runtime Plans

**Input**: `specs/332-compile-executable-runtime-plans/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-step-plan-contract.md`, `quickstart.md`  
**Prerequisites**: `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-step-plan-contract.md`, and `quickstart.md` exist for exactly one story.  
**Unit Test Command**: `./tools/test_unit.sh`  
**Integration Test Command**: `./tools/test_integration.sh`

## Source Traceability

Target Jira issue `MM-573`, source issue `manual-mm-569-mm-574`, and the original Jira preset brief are preserved in `spec.md`. This task list covers exactly one story: Runtime Executes Flattened Steps. Traceability spans FR-001 through FR-007, acceptance scenarios 1 through 6, edge cases, SC-001 through SC-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, and `contracts/runtime-step-plan-contract.md`.

Requirement status from `plan.md`: implemented_unverified rows require verification tests first plus conditional fallback implementation if verification fails; implemented_verified rows keep traceability and final validation without new implementation by default; missing traceability requires final verification evidence. No requirement is classified as partial.

## Phase 1: Setup

- [X] T001 Confirm active feature directory is `specs/332-compile-executable-runtime-plans` in `.specify/feature.json` and that `spec.md` contains exactly one `## User Story` section. (FR-007, SC-005)
- [X] T002 Confirm `specs/332-compile-executable-runtime-plans/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-step-plan-contract.md`, and `quickstart.md` exist before implementation. (FR-007, SC-005)
- [X] T003 Review existing runtime plan, task contract, proposal promotion, API normalization, and integration evidence in `moonmind/workflows/temporal/worker_runtime.py`, `moonmind/workflows/tasks/task_contract.py`, `moonmind/workflows/task_proposals/service.py`, `api_service/api/routers/executions.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py`. (FR-001, FR-003, FR-005, DESIGN-REQ-006, DESIGN-REQ-012, DESIGN-REQ-021, DESIGN-REQ-022)

## Phase 2: Foundational

- [X] T004 Identify reusable runtime planner fixtures in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` for explicit Tool and Skill step payloads without changing production behavior. (FR-001, FR-002, FR-003, SC-001)
- [X] T005 Identify reusable task proposal fixtures in `tests/unit/workflows/task_proposals/test_service.py` for reviewed flattened payload promotion and unresolved Preset rejection. (FR-005, FR-006, SC-003, SC-004)
- [X] T006 Identify reusable task-shaped submission fixture coverage in `tests/integration/temporal/test_task_shaped_submission_normalization.py` for flattened steps and preset provenance. (FR-001, FR-004, SC-002, DESIGN-REQ-020)
- [X] T007 Confirm no database migration, new persistent table, or provider credential setup is required by reviewing `plan.md`, `data-model.md`, and `contracts/runtime-step-plan-contract.md`. (Constitution II, IV, XII)

## Phase 3: Story - Runtime Executes Flattened Steps

**Story Summary**: As an operator, I want submitted and promoted tasks to execute from flattened Tool and Skill steps so runtime correctness does not depend on unresolved presets or live catalog re-expansion.

**Independent Test**: Submit or promote a task that originated from a preset, inspect the durable execution payload and runtime plan, and verify that only Tool and Skill steps are executable, each maps to the expected runtime materialization, preset provenance remains available, and no live preset catalog lookup is needed to execute the task.

**Traceability IDs**: FR-001 through FR-007; acceptance scenarios 1 through 6; SC-001 through SC-005; DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022.

**Unit Test Plan**: Add or confirm runtime planner tests for explicit Tool and Skill steps, task contract tests for executable-only step validation, and proposal promotion tests for reviewed flat payload preservation without live preset re-expansion.

**Integration Test Plan**: Confirm existing hermetic integration coverage for task-shaped submission preserves flattened executable steps and compact preset provenance; add coverage only if API/execution-boundary behavior changes.

### Unit Tests First

- [X] T008 [P] Add a verification unit test for explicit Skill step runtime planner mapping in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`. (FR-003, SCN-003, SC-001, DESIGN-REQ-012)
- [X] T009 [P] Add a verification unit test that one multi-step executable payload containing both Tool and Skill steps compiles into ordered runtime plan nodes in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`. (FR-001, FR-002, FR-003, SCN-001, SCN-002, SCN-003, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-012)
- [X] T010 [P] Add a verification unit test that runtime planner inputs preserve preset-derived source metadata without requiring catalog access in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`. (FR-004, SCN-004, SC-002, DESIGN-REQ-020)
- [X] T011 [P] Add a proposal promotion unit test proving a reviewed flattened task payload is promoted as stored even when a same-named preset catalog definition would differ or be unavailable in `tests/unit/workflows/task_proposals/test_service.py`. (FR-005, SCN-005, SC-003, DESIGN-REQ-021, DESIGN-REQ-022)
- [X] T012 [P] Add or confirm task contract unit coverage that `type: preset`, unresolved include work, and conflicting executable step payloads fail explicitly in `tests/unit/workflows/tasks/test_task_contract.py`. (FR-006, SCN-006, SC-004, DESIGN-REQ-018)
- [X] T013 [P] Add or confirm proposal promotion unit coverage that unresolved Preset steps and preset-derived steps without flat executable type are rejected in `tests/unit/workflows/task_proposals/test_service.py`. (FR-006, SCN-006, SC-004, DESIGN-REQ-018)

### Integration Tests First

- [X] T014 [P] Add or confirm hermetic integration coverage that task-shaped submission preserves flattened Tool/Skill steps and no `preset` step remains in `tests/integration/temporal/test_task_shaped_submission_normalization.py`. (FR-001, SCN-001, SC-004, SC-001, DESIGN-REQ-006)
- [X] T015 [P] Add or confirm hermetic integration coverage that preset-derived provenance and authored preset metadata remain compact and durable in `tests/integration/temporal/test_task_shaped_submission_normalization.py`. (FR-004, SCN-004, SC-002, DESIGN-REQ-020)
- [X] T016 [P] Add or confirm hermetic integration regression that manual-only task submissions do not gain fabricated preset metadata in `tests/integration/temporal/test_task_shaped_submission_normalization.py`. (Edge case: manual work, SC-002)

### Red-First Confirmation

- [X] T017 Run focused runtime planner tests and confirm the new MM-573 verification tests fail only when expected behavior is missing using `python -m pytest tests/unit/workflows/temporal/test_temporal_worker_runtime.py -q`. (FR-001, FR-002, FR-003, FR-004, SC-001, SC-002)
- [X] T018 Run focused proposal promotion tests and confirm the no-live-reexpansion verification fails only if promotion recomputes from live preset data using `python -m pytest tests/unit/workflows/task_proposals/test_service.py -q`. (FR-005, FR-006, SC-003, SC-004)
- [X] T019 Run focused task contract tests and confirm unresolved Preset/include rejection coverage remains passing or fails for a real gap using `python -m pytest tests/unit/workflows/tasks/test_task_contract.py -q`. (FR-006, SC-004, DESIGN-REQ-018)
- [X] T020 Run focused hermetic integration coverage and confirm task-shaped submission behavior using `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci`. (FR-001, FR-004, SC-001, SC-002)

### Conditional Fallback Implementation For Implemented-Unverified Rows

- [X] T021 Confirm T008 and T009 pass without fallback changes; explicit Skill steps already produce acceptable runtime materialization inputs in `moonmind/workflows/temporal/worker_runtime.py`. (FR-003, DESIGN-REQ-012)
- [X] T022 Confirm T009 and T014 pass without fallback changes; executable payload boundaries do not retain unresolved Preset steps. (FR-001, DESIGN-REQ-006)
- [X] T023 Confirm T010 and T015 pass without fallback changes; preset provenance is preserved without live catalog lookup. (FR-004, DESIGN-REQ-020)
- [X] T024 Confirm T011 passes without fallback changes; promotion uses the reviewed flattened payload without live re-expansion. (FR-005, DESIGN-REQ-021, DESIGN-REQ-022)
- [X] T025 Confirm T012 and T013 pass without fallback changes; unresolved Preset/include work is rejected before execution. (FR-006, DESIGN-REQ-018)

### Production Implementation

- [X] T026 Confirm no runtime planner production changes were necessary after T017 passed in `moonmind/workflows/temporal/worker_runtime.py`. (FR-001, FR-003, SCN-001, SCN-003)
- [X] T027 Confirm no task contract or API normalization production changes were necessary after T019 and T020 passed. (FR-001, FR-006, SCN-006)
- [X] T028 Confirm no proposal promotion production changes were necessary after T018 passed. (FR-005, SCN-005)
- [X] T029 Preserve MM-573 and `manual-mm-569-mm-574` references in implementation notes, test names or comments where useful, and final delivery metadata without adding noisy runtime fields in `specs/332-compile-executable-runtime-plans/tasks.md`. (FR-007, SC-005)

### Story Validation

- [X] T030 Re-run focused unit tests with `python -m pytest tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py tests/unit/workflows/task_proposals/test_service.py -q`. (FR-001 through FR-006, SC-001 through SC-004)
- [X] T031 Re-run focused hermetic integration coverage with `python -m pytest tests/integration/temporal/test_task_shaped_submission_normalization.py -q -m integration_ci` if any API/execution-boundary task changed production behavior. (FR-001, FR-004, SC-001, SC-002)
- [X] T032 Verify `contracts/runtime-step-plan-contract.md` is satisfied by comparing task contract validation, runtime planner output, and proposal promotion behavior against `specs/332-compile-executable-runtime-plans/contracts/runtime-step-plan-contract.md`. (FR-001 through FR-006)
- [X] T033 Verify `MM-573`, `manual-mm-569-mm-574`, and the original Jira preset brief remain preserved in `spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-step-plan-contract.md`, `quickstart.md`, and `tasks.md`. (FR-007, SC-005)

## Final Phase: Polish And Verification

- [X] T034 Refactor any duplicated test fixtures introduced for MM-573 while keeping behavior unchanged in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py`, `tests/unit/workflows/task_proposals/test_service.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py`. (Constitution VI, VIII)
- [X] T035 Run the full required unit suite with `./tools/test_unit.sh`. (FR-001 through FR-007)
- [X] T036 Run the required hermetic integration suite with `./tools/test_integration.sh` if production code or API/execution-boundary behavior changed; otherwise record why focused integration coverage is sufficient in `specs/332-compile-executable-runtime-plans/verification.md`. (FR-001, FR-004, SC-001, SC-002)
- [X] T037 Confirm no provider verification or credentialed tests are required for MM-573 and document any intentionally skipped provider checks in `specs/332-compile-executable-runtime-plans/verification.md`. (Constitution II, III)
- [ ] T038 Run `/moonspec-verify` after implementation and tests pass, and ensure verification covers MM-573, `manual-mm-569-mm-574`, the original Jira preset brief, FR-001 through FR-007, acceptance scenarios 1 through 6, SC-001 through SC-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-012, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, commands run, and remaining risks. (FR-007, SC-005)

## Dependencies And Execution Order

1. Phase 1 setup tasks T001-T003 must complete first.
2. Phase 2 foundational tasks T004-T007 prepare fixture and boundary review.
3. Unit tests T008-T013 and integration tests T014-T016 are written before any production implementation.
4. Red-first confirmation T017-T020 runs before conditional fallback tasks.
5. Conditional fallback tasks T021-T025 are executed only if verification tests expose gaps.
6. Production implementation tasks T026-T029 are executed only for confirmed gaps.
7. Story validation T030-T033 proves the single story before final polish.
8. Final verification T034-T038 closes the feature.

## Parallel Examples

- T008, T011, T012, and T014 can be drafted in parallel because they target different test files or independent coverage areas.
- T021 and T024 can run in parallel only if both are needed because they touch different production files.
- T021 and T026 both touch `moonmind/workflows/temporal/worker_runtime.py`; do not run them in parallel.
- T030 must wait for all required focused tests and any conditional implementation work.

## Implementation Strategy

Start with verification tests for implemented_unverified rows: explicit Skill-step mapping, combined Tool/Skill runtime plan compilation, no-live-reexpansion proposal promotion, and executable payload boundary coverage. Treat implemented_verified rows as traceability and final validation work unless a new verification test reveals drift. Execute fallback implementation tasks only for failing verification tests, keep changes scoped to the runtime planner, task contract/API normalization, or proposal service, and preserve compact preset provenance without adding live catalog dependencies.
