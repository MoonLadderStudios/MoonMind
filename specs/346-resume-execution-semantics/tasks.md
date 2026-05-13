# Tasks: Resume Execution Semantics

**Input**: Design documents from `/work/agent_jobs/mm:6d4f0168-95fa-48a3-9cb3-f3508f959fd5/repo/specs/346-resume-execution-semantics/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/resume-execution.md](./contracts/resume-execution.md), [quickstart.md](./quickstart.md)

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: One story only: Failed-Step Resume Execution.

**Source Traceability**: Original Jira issue `MM-647` and the preserved Jira preset brief are in `spec.md` `**Input**`. Tasks cover FR-001 through FR-014, SCN-001 through SCN-008, SC-001 through SC-008, DESIGN-REQ-001 through DESIGN-REQ-006, and source coverage IDs DESIGN-REQ-018 and DESIGN-REQ-024.

**Requirement Status Summary**: 5 missing, 14 partial, 17 implemented_unverified, 4 implemented_verified from `plan.md`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py`
- Integration tests: `./tools/test_unit.sh tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/integration/temporal/test_backend_resume_eligibility.py`
- Full integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when touching different files and not depending on incomplete work.
- Every task uses `- [ ] T### [P?] Description with file path`.

## Phase 1: Setup

**Purpose**: Confirm the task environment and current artifact set before test-first work.

- [ ] T001 Confirm `specs/346-resume-execution-semantics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/resume-execution.md`, and `quickstart.md` are current and preserve `MM-647`, DESIGN-REQ-018, and DESIGN-REQ-024.
- [ ] T002 Confirm existing focused Resume test targets and fixtures in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/api/routers/test_executions.py`, `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, and `tests/integration/temporal/test_backend_resume_eligibility.py`

---

## Phase 2: Foundational

**Purpose**: Establish shared test fixtures and contract helpers that block the story tests.

**CRITICAL**: No production implementation starts until Phase 3 unit and integration tests are written and red-first confirmation has run.

- [ ] T003 Add reusable Resume checkpoint, `resumeSource`, preserved-step, and workspace-restoration fixtures for MM-647 in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-001, FR-002, FR-003, FR-006, FR-012, SCN-001, SCN-002, SCN-007, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, and DESIGN-REQ-006.
- [ ] T004 [P] Add reusable resumed-run integration fixtures for source run, checkpoint payload, preserved output refs, failed step, and downstream step ordering in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-004, FR-005, FR-008, FR-009, FR-010, FR-011, SCN-003, SCN-004, SCN-005, SCN-006, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-005.
- [ ] T005 [P] Add service-level fixture coverage for accepted Resume refs and invalid checkpoint cases only where current helpers are insufficient in `tests/unit/workflows/temporal/test_temporal_service.py` covering FR-001, FR-002, FR-012, SC-001, and SC-006.

**Checkpoint**: Shared fixtures are ready for red-first unit and integration tests.

---

## Phase 3: Story - Failed-Step Resume Execution

**Summary**: As an execution-plane engineer, I want Resume to restore a validated failed-step checkpoint, preserve completed prior work, and retry the failed step as the first new execution action so that users can continue a failed task without editing inputs or losing trusted progress.

**Independent Test**: Start representative Resume executions from valid and invalid failed-step checkpoints, then verify valid resumes validate source identity and plan identity, restore workspace state, preserve prior steps without re-executing them, inject preserved outputs, retry the failed step first, continue downstream after success, and fail explicitly before execution when restoration is incomplete or inconsistent.

**Traceability**: FR-001 through FR-014; SCN-001 through SCN-008; SC-001 through SC-008; DESIGN-REQ-001 through DESIGN-REQ-006; original coverage IDs DESIGN-REQ-018 and DESIGN-REQ-024.

**Test Plan**:

- Unit: resume-source validation, no-fallback failure behavior, preserved provenance, preserved-output refs, workspace restoration contract, fresh resumed-run evidence, edited-input guard preservation.
- Integration: real or representative `MoonMind.Run` Resume initialization, preserved-step import, pre-failed-step workspace restoration, failed-step first ordering, downstream continuation, invalid restoration no-fallback behavior, projection of preserved prior steps.

### Unit Tests (write first)

- [ ] T006 Add failing unit tests for `MoonMind.Run` resume-source validation before step execution in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-001, FR-002, FR-003, FR-012, SCN-001, SCN-007, SC-001, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, and DESIGN-REQ-006.
- [ ] T007 Add failing unit tests for workspace restoration requirement before failed-step execution in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-006, SCN-002, SC-002, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T008 Add failing unit tests for preserved output injection into failed and downstream step input contracts in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-008, SCN-004, SC-004, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T009 Add failing unit tests for fresh resumed-run ledger rows, artifacts, and checkpoints on retried and later steps in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-011, SCN-006, SC-005, DESIGN-REQ-002, and DESIGN-REQ-003.
- [ ] T010 [P] Add verification unit tests preserving edited-input rejection and source ID pinning in `tests/unit/api/routers/test_executions.py` covering FR-013, FR-014, SCN-008, SC-007, SC-008, DESIGN-REQ-004, and DESIGN-REQ-006.
- [ ] T011 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py` and confirm T006-T009 fail for missing/partial MM-647 execution semantics while T010 preserves existing verified behavior.

### Integration Tests (write first)

- [ ] T012 Add failing integration test for valid Resume initialization validating source workflow ID, source run ID, snapshot, failed step, and plan identity before execution in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-001, FR-002, FR-003, SCN-001, SC-001, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-006.
- [ ] T013 Add failing integration test proving workspace or branch checkpoint restoration happens before the failed step starts in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-006, SCN-002, SC-002, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T014 Add failing integration test proving preserved prior steps are not re-executed and retain `preservedFrom` provenance in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-004, FR-005, FR-007, SCN-003, SC-003, DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-005.
- [ ] T015 Add failing integration test proving preserved outputs are available to the retried failed step and downstream steps with continuous-run semantics in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-008, SCN-004, SC-004, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T016 Add failing integration test proving the failed step is the first newly executed step and downstream steps continue after success in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-009, FR-010, SCN-005, SC-005, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T017 Add failing integration test proving invalid restoration fails explicitly before failed-step execution and never falls back to full rerun in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-012, SCN-007, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, and DESIGN-REQ-003.
- [ ] T018 Add or extend integration verification for accepted Resume refs and route/service invalid checkpoint behavior in `tests/integration/temporal/test_backend_resume_eligibility.py` covering FR-001, FR-002, FR-012, FR-013, SCN-001, SCN-007, SCN-008, SC-001, SC-006, and SC-007.
- [ ] T019 Run `./tools/test_unit.sh tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/integration/temporal/test_backend_resume_eligibility.py` and confirm T012-T018 fail for the intended missing/partial MM-647 behavior or pass only for already verified rows.

### Implementation

- [ ] T020 Implement compact resume-source validation and explicit pre-execution failure handling in `moonmind/workflows/temporal/workflows/run.py` covering FR-001, FR-002, FR-003, FR-012, SCN-001, SCN-007, SC-001, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, and DESIGN-REQ-006.
- [ ] T021 Add or refine Resume source models and validation helpers in `moonmind/schemas/temporal_models.py` only as needed by T020 and T025, covering FR-001, FR-002, FR-003, FR-012, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-006.
- [ ] T022 Implement workspace or branch checkpoint restoration boundary before failed-step execution in `moonmind/workflows/temporal/workflows/run.py` covering FR-006, SCN-002, SC-002, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T023 Add any required activity/service boundary for workspace restoration in `moonmind/workflows/temporal/service.py` or an existing Temporal activity module only if T022 cannot use an existing boundary, covering FR-006 and SCN-002.
- [ ] T024 Implement preserved output injection from preserved ledger rows into failed and downstream step input composition in `moonmind/workflows/temporal/workflows/run.py` covering FR-008, SCN-004, SC-004, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T025 Ensure preserved prior steps initialize with source provenance, zero new attempt, visible preserved status, and no re-execution in `moonmind/workflows/temporal/step_ledger.py` and `moonmind/workflows/temporal/workflows/run.py` covering FR-004, FR-005, FR-007, SCN-003, SC-003, DESIGN-REQ-001, DESIGN-REQ-003, and DESIGN-REQ-005.
- [ ] T026 Ensure failed-step first ordering and downstream continuation use the restored/preserved state in `moonmind/workflows/temporal/workflows/run.py` covering FR-009, FR-010, SCN-005, SC-005, DESIGN-REQ-001, and DESIGN-REQ-002.
- [ ] T027 Ensure retried failed and later steps produce fresh resumed-run ledger rows, artifacts, and checkpoints instead of copied source evidence in `moonmind/workflows/temporal/workflows/run.py` and `moonmind/workflows/temporal/step_ledger.py` covering FR-011, SCN-006, SC-005, DESIGN-REQ-002, and DESIGN-REQ-003.
- [ ] T028 Preserve and adjust, only if required by test failures, failed-step Resume submission validation in `api_service/api/routers/executions.py` covering FR-013, SCN-008, SC-007, and DESIGN-REQ-004.
- [ ] T029 Preserve and adjust, only if required by test failures, `TemporalExecutionService.create_failed_step_resume_execution()` accepted Resume payload construction in `moonmind/workflows/temporal/service.py` covering FR-001, FR-002, FR-003, FR-012, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, and DESIGN-REQ-006.
- [ ] T030 Preserve MM-647 traceability comments or test names where useful in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` covering FR-014 and SC-008.

### Story Validation

- [ ] T031 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py` and fix failures in `moonmind/workflows/temporal/workflows/run.py`, `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/step_ledger.py`, `moonmind/workflows/temporal/service.py`, or `api_service/api/routers/executions.py` until FR-001 through FR-014 pass focused unit validation.
- [ ] T032 Run `./tools/test_unit.sh tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/integration/temporal/test_backend_resume_eligibility.py` and fix failures in `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/service.py`, `moonmind/workflows/temporal/step_ledger.py`, or `api_service/api/routers/executions.py` until SCN-001 through SCN-008 pass focused integration validation.

**Checkpoint**: The single MM-647 story is functionally complete, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T033 [P] Review `specs/346-resume-execution-semantics/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/resume-execution.md`, `quickstart.md`, and `tasks.md` for MM-647 traceability and update only if implementation reality changed.
- [ ] T034 [P] Run focused quickstart unit command from `specs/346-resume-execution-semantics/quickstart.md` and record the outcome in implementation notes or final verification evidence.
- [ ] T035 Run focused quickstart integration command from `specs/346-resume-execution-semantics/quickstart.md` and record the outcome in implementation notes or final verification evidence.
- [ ] T036 Run full required unit suite `./tools/test_unit.sh` and fix any MM-647 regressions.
- [ ] T037 Run full required hermetic integration suite `./tools/test_integration.sh` and fix any MM-647 regressions or document exact environment blockers.
- [ ] T038 Run `/speckit.verify` against `specs/346-resume-execution-semantics/spec.md`, `plan.md`, `tasks.md`, source design mappings, preserved MM-647 Jira preset brief, and test evidence after implementation and tests pass.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 and blocks story tests.
- Phase 3 depends on Phase 2.
- Phase 4 depends on story implementation and focused tests passing.

### Story Order

- T006-T010 unit tests must be written before T011.
- T012-T018 integration tests must be written before T019.
- T011 and T019 red-first confirmation must run before T020-T030 production work.
- T020-T023 establish validation and restoration before T024-T027 step execution semantics.
- T028-T029 are conditional fallback tasks for already implemented or implemented_unverified route/service behavior and should run only if verification tests fail.
- T031-T032 validate the complete story before polish.

### Parallel Opportunities

- T004 and T005 can run in parallel with T003 after T001-T002.
- T010 can be authored in parallel with the T006-T009 unit-test sequence because it touches a different file.
- T018 can be authored in parallel with the T012-T017 integration-test sequence because it touches a different file.
- T033 and T034 can run in parallel after story validation.

## Parallel Example

```bash
Task: "Add failing unit tests for workspace restoration in tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py"
Task: "Add failing integration test for invalid restoration no-fallback in tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py"
Task: "Add route/service verification for accepted Resume refs in tests/integration/temporal/test_backend_resume_eligibility.py"
```

## Implementation Strategy

1. Confirm the MM-647 artifact set and existing Resume test fixtures.
2. Add all red-first unit tests for validation, restoration, preserved output injection, fresh evidence, and no-edit guard preservation.
3. Add all red-first integration tests for real resumed-run initialization, workspace restoration, preserved-step behavior, failed-step ordering, downstream continuation, and no-fallback failures.
4. Run focused unit and integration commands and record expected failures before production changes.
5. Implement missing and partial code paths in `MoonMind.Run`, schemas, service, step ledger, and route boundaries as needed.
6. Run focused tests until green.
7. Run full required unit and hermetic integration suites.
8. Run final `/speckit.verify`.

## Coverage Summary

- Code-and-test work: FR-002, FR-003, FR-006, FR-008, FR-011, FR-012 and related missing/partial SCN/SC/DESIGN rows.
- Verification-first with conditional fallback: FR-001, FR-004, FR-005, FR-007, FR-009, FR-010 and related implemented_unverified rows.
- Already verified and preserved: FR-013, FR-014, SCN-008, SC-007, SC-008, DESIGN-REQ-004.
- Final verification preserves original request traceability for `MM-647`, DESIGN-REQ-018, and DESIGN-REQ-024.
