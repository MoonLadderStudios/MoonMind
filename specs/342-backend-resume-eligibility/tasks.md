# Tasks: Backend-Computed Resume Eligibility

**Input**: Design documents from `specs/342-backend-resume-eligibility/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/recovery-eligibility.md](contracts/recovery-eligibility.md), [quickstart.md](quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: backend-governed failed-task recovery choices for MM-643.

**Source Traceability**: The original `MM-643` Jira preset brief is preserved in [spec.md](spec.md) `**Input**`. Tasks cover FR-001 through FR-010, acceptance scenarios 1 through 6, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-005, and [contracts/recovery-eligibility.md](contracts/recovery-eligibility.md).

**Test Commands**:

Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_service.py`

UI unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`

Integration tests: `./tools/test_integration.sh`

Final verification: `/moonspec.verify`

## Format: `[ID] [P?] Description`

Each task below uses `- [ ] T###` with optional `[P]` for independent work in different files.

## Phase 1: Setup

**Purpose**: Confirm the MoonSpec planning artifacts and test targets are ready before test-first work begins.

- [X] T001 Confirm `specs/342-backend-resume-eligibility/spec.md`, `specs/342-backend-resume-eligibility/plan.md`, `specs/342-backend-resume-eligibility/research.md`, `specs/342-backend-resume-eligibility/data-model.md`, `specs/342-backend-resume-eligibility/contracts/recovery-eligibility.md`, and `specs/342-backend-resume-eligibility/quickstart.md` exist and describe exactly one MM-643 story.
- [X] T002 Confirm existing recovery-related test targets are runnable entry points in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `frontend/src/entrypoints/task-detail.test.tsx`, and `tests/integration/temporal/`.

---

## Phase 2: Foundational

**Purpose**: Add shared fixtures and contract helpers needed by the red-first tests. No production recovery behavior changes begin until this phase is complete.

- [ ] T003 [P] Add or extend failed-execution fixture builders for action capability and Resume evidence permutations in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-003, FR-007, FR-008, SCN-001, SCN-002, SCN-003, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T004 [P] Add or extend canonical recovery payload fixtures in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-005, FR-006, SCN-005, and DESIGN-REQ-003.
- [ ] T005 [P] Add or extend Task Detail recovery action fixtures in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-002, FR-009, SCN-001, SCN-006, SC-001, and SC-005.

**Checkpoint**: Shared fixtures are ready; story test and implementation work can begin.

---

## Phase 3: Story - Backend-Governed Failed-Task Recovery Choices

**Summary**: As a Mission Control user recovering a failed task, I want Edit task, Rerun, and Resume to appear as separate actions based on backend-computed eligibility so that I can choose the correct recovery intent without the platform inferring Resume from a generic rerun.

**Independent Test**: Evaluate failed task details and recovery submissions across eligible, ineligible, stale, unauthorized, and inconsistent evidence cases, then confirm displayed actions, rejection reasons, and submitted recovery intent records match backend-computed capability state.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005.

**Unit Test Plan**: API unit tests cover backend action matrices, disabled reasons, forbidden Resume payload fields, and no generic rerun-to-Resume inference; task contract tests cover recovery provenance and failed-step Resume reference shapes; Temporal service tests cover accepted Resume metadata and rerun sanitization; UI tests cover backend-driven visibility.

**Integration Test Plan**: Hermetic integration tests cover serialized execution detail action capabilities, Resume submission success and rejection boundaries, and exact rerun/edited retry not carrying Resume intent.

### Unit Tests (write first)

- [ ] T006 [P] Add API unit tests for independent Edit task, Rerun, and Resume capability matrices in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-002, FR-003, SCN-001, SC-001, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T007 [P] Add API unit tests for missing source workflow/run, missing completed-step refs, missing workspace checkpoint, missing plan identity, stale evidence, unauthorized checkpoint, corrupted checkpoint, and inconsistent checkpoint reasons in `tests/unit/api/routers/test_executions.py` covering FR-007, FR-008, SCN-002, SCN-003, SC-002, DESIGN-REQ-002, and DESIGN-REQ-005.
- [X] T008 [P] Add API unit tests that Resume rejects all edited task payload field categories in `tests/unit/api/routers/test_executions.py` covering FR-009, SCN-006, SC-005, DESIGN-REQ-001, and DESIGN-REQ-005.
- [X] T009 [P] Add Temporal service unit tests proving generic rerun and edited full retry do not carry or infer Resume metadata in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-004, SCN-004, SC-004, and DESIGN-REQ-004.
- [ ] T010 [P] Add task contract unit tests for canonical `TaskRecoveryProvenance` and `ResumeFromFailedStepRef` equivalence at the accepted Resume boundary in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-005, FR-006, SCN-005, SC-003, and DESIGN-REQ-003.
- [ ] T011 [P] Add Task Detail UI tests proving Resume visibility and unavailable reason copy come only from backend action fields in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-002, FR-009, SCN-001, SCN-006, SC-001, and SC-005.

### Integration Tests (write first)

- [ ] T012 [P] Add contract or API integration test for `GET /api/executions/{workflow_id}` recovery action serialization in `tests/contract/test_temporal_execution_api.py` covering FR-001, FR-002, FR-003, SCN-001, SC-001, and `contracts/recovery-eligibility.md`.
- [ ] T013 [P] Add hermetic integration test for complete backend Resume evidence enabling `canResumeFromFailedStep` in `tests/integration/temporal/test_backend_resume_eligibility.py` covering FR-007, SCN-001, SC-003, DESIGN-REQ-002, and DESIGN-REQ-005.
- [ ] T014 [P] Add hermetic integration test for invalid Resume evidence rejecting before a resumed execution is created in `tests/integration/temporal/test_backend_resume_eligibility.py` covering FR-008, SCN-003, SC-002, SC-004, DESIGN-REQ-002, and DESIGN-REQ-005.
- [X] T015 [P] Add hermetic integration test proving generic rerun and edited full retry do not populate Resume reference fields in `tests/integration/temporal/test_backend_resume_eligibility.py` covering FR-004, FR-009, SCN-004, SCN-006, SC-004, SC-005, and DESIGN-REQ-004.
- [X] T016 [P] Add integration boundary test for accepted Resume provenance and failed-step reference fields in `tests/integration/temporal/test_backend_resume_eligibility.py` covering FR-005, FR-006, SCN-005, SC-003, and DESIGN-REQ-003.

### Red-First Confirmation

- [ ] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_service.py` and confirm T006 through T010 fail for the expected MM-643 reasons before production changes.
- [ ] T018 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T011 fails for the expected MM-643 reason before production UI changes.
- [ ] T019 Run the narrow integration targets for `tests/contract/test_temporal_execution_api.py`, `tests/integration/temporal/test_backend_resume_eligibility.py`, and `tests/integration/api/test_task_contract_normalization.py` and confirm T012 through T016 fail for the expected MM-643 reasons before production changes.

### Implementation

- [ ] T020 If T006 or T012 fails, update backend action capability serialization in `api_service/api/routers/executions.py` so Edit task, Rerun, and Resume remain independent backend-computed fields for FR-001, FR-002, FR-003, SCN-001, SC-001, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T021 If T007, T013, or T014 fails, update Resume evidence evaluation and bounded disabled reasons in `api_service/api/routers/executions.py` for source workflow/run, failed-step ledger identity, completed-step refs, workspace checkpoint, plan identity, stale evidence, unauthorized evidence, corrupted evidence, and inconsistent evidence covering FR-007, FR-008, SCN-002, SCN-003, SC-002, DESIGN-REQ-002, and DESIGN-REQ-005.
- [ ] T022 If T010 or T016 fails, update canonical recovery provenance and failed-step Resume reference normalization in `moonmind/workflows/tasks/task_contract.py` covering FR-005, FR-006, SCN-005, SC-003, and DESIGN-REQ-003.
- [X] T023 If T010, T014, or T016 fails at the execution boundary, update accepted Resume metadata construction in `moonmind/workflows/temporal/service.py` so the resumed execution carries source workflow/run, failed step, checkpoint, snapshot, plan identity, and recovery intent consistently for FR-005, FR-006, FR-007, SCN-003, SCN-005, DESIGN-REQ-003, and DESIGN-REQ-005.
- [X] T024 If T009 or T015 fails, update full rerun and edited retry sanitization in `moonmind/workflows/temporal/service.py` so generic rerun and edited full retry cannot carry Resume reference fields for FR-004, SCN-004, SC-004, and DESIGN-REQ-004.
- [ ] T025 If T008 fails, update Resume request validation in `api_service/api/routers/executions.py` so all edited task payload field categories are rejected before checkpoint hydration or execution creation for FR-009, SCN-006, SC-005, DESIGN-REQ-001, and DESIGN-REQ-005.
- [ ] T026 If T011 fails, update Task Detail recovery action rendering in `frontend/src/entrypoints/task-detail.tsx` so Resume visibility and unavailable reason copy come only from backend action fields for FR-001, FR-002, FR-009, SCN-001, SCN-006, SC-001, and SC-005.
- [ ] T027 If contract tests reveal stale generated API types, update generated or schema-facing recovery action types in `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/generated/openapi.ts` for `contracts/recovery-eligibility.md`, FR-003, FR-005, and FR-006.

### Story Validation

- [ ] T028 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_service.py` and confirm all MM-643 Python unit tests pass.
- [ ] T029 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm all MM-643 UI tests pass.
- [ ] T030 Run the narrow integration targets for `tests/contract/test_temporal_execution_api.py`, `tests/integration/temporal/test_backend_resume_eligibility.py`, and `tests/integration/api/test_task_contract_normalization.py` and confirm all MM-643 contract/integration tests pass.
- [ ] T031 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and confirm the full unit suite passes for MM-643.
- [ ] T032 Run `./tools/test_integration.sh` and confirm the required hermetic integration suite passes for MM-643.

**Checkpoint**: The single MM-643 story is covered by red-first unit tests, integration tests, implementation work, and independent story validation.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T033 [P] Update `specs/342-backend-resume-eligibility/research.md` if implementation evidence changes any `implemented_unverified` or `partial` requirement status for FR-001 through FR-010.
- [ ] T034 [P] Update `specs/342-backend-resume-eligibility/contracts/recovery-eligibility.md` if the final accepted Resume boundary differs from the planned recovery provenance or `resumeSource` equivalence contract.
- [ ] T035 [P] Update `specs/342-backend-resume-eligibility/quickstart.md` if final validation commands or focused test paths change.
- [ ] T036 Review `api_service/api/routers/executions.py`, `moonmind/workflows/tasks/task_contract.py`, `moonmind/workflows/temporal/service.py`, and `frontend/src/entrypoints/task-detail.tsx` for stale aliases or compatibility shims introduced during MM-643 and remove any internal-only obsolete paths.
- [ ] T037 Confirm `MM-643`, the original Jira preset brief, source coverage IDs DESIGN-REQ-006, DESIGN-REQ-013, and DESIGN-REQ-015, and the final test evidence remain preserved in `specs/342-backend-resume-eligibility/spec.md`, `specs/342-backend-resume-eligibility/plan.md`, `specs/342-backend-resume-eligibility/research.md`, and `specs/342-backend-resume-eligibility/tasks.md`.
- [ ] T038 Run `/moonspec.verify` after implementation and tests pass, using `specs/342-backend-resume-eligibility/spec.md`, `specs/342-backend-resume-eligibility/plan.md`, `specs/342-backend-resume-eligibility/tasks.md`, `specs/342-backend-resume-eligibility/contracts/recovery-eligibility.md`, and the preserved MM-643 Jira preset brief as final verification sources.

---

## Dependencies And Execution Order

### Phase Dependencies

Setup tasks T001-T002 must complete before foundational tasks T003-T005. Foundational tasks T003-T005 must complete before story test tasks T006-T016. Red-first confirmation tasks T017-T019 must complete before implementation tasks T020-T027. Story validation tasks T028-T032 must complete before polish and final verification tasks T033-T038.

### Within The Story

Unit tests T006-T011 and integration tests T012-T016 are written before implementation. Red-first confirmation T017-T019 proves the tests fail for the intended MM-643 gaps. Implementation tasks T020-T027 are conditional fallback work for implemented_unverified rows and required completion work for partial rows. Validation T028-T032 proves the story passes independently.

### Parallel Opportunities

T003, T004, and T005 can run in parallel after setup because they touch different test files. T006 through T011 can run in parallel after foundational fixtures are ready, except tasks sharing `tests/unit/api/routers/test_executions.py` should coordinate edits. T012 through T016 can run in parallel where they touch different integration files. T033 through T035 can run in parallel after validation passes.

---

## Parallel Example: Story Phase

```bash
Task: "T010 Add task contract unit tests in tests/unit/workflows/tasks/test_task_contract.py"
Task: "T011 Add Task Detail UI tests in frontend/src/entrypoints/task-detail.test.tsx"
Task: "T013 Add hermetic integration test in tests/integration/temporal/test_backend_resume_eligibility.py"
```

---

## Implementation Strategy

Complete setup and fixture work first. Write all unit and integration tests before production changes. Confirm red-first failures with T017-T019. For implemented_unverified rows, skip the corresponding fallback implementation task when the new verification tests pass unchanged. For partial rows, implement the smallest contract-aligned change needed to satisfy the failing tests. Finish with focused unit, UI, integration, full unit, hermetic integration, and `/moonspec.verify` evidence.

## Requirement Status Coverage Summary

Code-and-test rows: FR-005, FR-006, FR-007, FR-008, SCN-003, SCN-005, SC-002, SC-003, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005.

Verification-first rows with conditional fallback: FR-001, FR-002, FR-003, FR-004, FR-009, FR-010, SCN-001, SCN-002, SCN-004, SCN-006, SC-001, SC-004, SC-005, SC-006, DESIGN-REQ-001, DESIGN-REQ-004.

Already verified rows: none.

Final validation rows: FR-010, SC-006, T037, and T038 preserve MM-643 traceability and final `/moonspec.verify` work.
