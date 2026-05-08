# Tasks: Gate Resume on Durable Checkpoint Evidence

**Input**: Design documents from `specs/327-gate-resume-checkpoint-evidence/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/resume-evidence.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around one independently testable story: Evidence-Gated Resume Eligibility.

**Source Traceability**: MM-633 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-013, acceptance scenarios 1-7, edge cases, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-004.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Focused UI tests when Task Detail display changes: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work
- Each task includes exact file paths and requirement, scenario, or source IDs when applicable
- This task list covers exactly one story and must not add hidden scope beyond MM-633

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active artifact set and test targets before writing red-first tests.

- [X] T001 Confirm `specs/327-gate-resume-checkpoint-evidence/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/resume-evidence.md`, and `quickstart.md` are present and all preserve MM-633 traceability. (FR-013, SC-007)
- [X] T002 Confirm the one-story scope by checking `specs/327-gate-resume-checkpoint-evidence/spec.md` has exactly one `## User Story` section and no `[NEEDS CLARIFICATION]` markers. (FR-013, SC-007)
- [X] T003 Identify current focused test targets and fixtures in `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/schemas/test_temporal_payload_policy.py`, `frontend/src/entrypoints/task-detail.test.tsx`, and `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`. (FR-001 through FR-012)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare reusable test fixtures and evidence helpers before story tests and implementation.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T004 [P] Add or extend Resume checkpoint fixture builders in `tests/unit/workflows/temporal/test_temporal_service.py` for complete evidence, missing workspace checkpoint, missing plan identity, missing preserved refs, source mismatch, stale plan, corrupted payload, and inline large content cases. (FR-003, FR-004, FR-005, FR-006, FR-007, FR-009, FR-011)
- [X] T005 [P] Add or extend API execution record/checkpoint artifact fixtures in `tests/unit/api/routers/test_executions.py` for complete evidence and every bounded disabled reason from `specs/327-gate-resume-checkpoint-evidence/contracts/resume-evidence.md`. (FR-001, FR-002, FR-004, FR-005, FR-006, FR-007, FR-011)
- [X] T006 [P] Add or extend integration step-ledger fixture helpers in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` for completed prior steps with and without recoverable output refs and workspace checkpoint refs. (FR-004, FR-005, FR-010, DESIGN-REQ-002, DESIGN-REQ-003)
- [ ] T007 [P] Add or extend Task Detail UI mock payload helpers in `frontend/src/entrypoints/task-detail.test.tsx` for backend-provided `actions.canResumeFromFailedStep`, `disabledReasons.canResumeFromFailedStep`, and `resume.disabledReason`. (FR-001, SCN-002)

**Checkpoint**: Test fixtures are ready; story tests and implementation work can now begin.

---

## Phase 3: Story - Evidence-Gated Resume Eligibility

**Summary**: As an operator recovering from a failed task, I want Resume offered only when MoonMind can prove the failed step and completed prior work are recoverable from durable, pinned evidence.

**Independent Test**: Create failed task recovery cases with complete, missing, stale, unauthorized, corrupted, and inconsistent resume evidence; verify that only complete evidence enables Resume, invalid evidence fails before execution, and large or binary checkpoint content remains behind refs.

**Traceability**: FR-001 through FR-013; acceptance scenarios 1-7; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-004.

**Unit Test Plan**:

- API route/action capability tests for complete evidence, missing evidence, bounded disabled reasons, and no UI inference.
- Temporal service/model tests for source identity, failed-step identity, preserved refs, workspace checkpoint, plan identity, compact refs, and no fallback execution.
- Payload policy/schema tests for checkpoint-specific compact refs and inline large/binary rejection.
- UI tests verifying Task Detail follows backend action and disabled-reason fields.

**Integration Test Plan**:

- Hermetic workflow/step-ledger test for valid evidence materializing preserved steps.
- Hermetic invalid-evidence test proving Resume blocks before creating a follow-up execution or full rerun fallback.
- Hermetic idempotent checkpoint write test when checkpoint recording is implemented at a workflow/service boundary.

### Unit Tests (write first) ⚠️

> Write these tests FIRST. Run them and confirm they fail for the expected reason before production implementation.

- [X] T008 [P] Add failing API unit tests for backend-only Resume eligibility and disabled-reason matrix in `tests/unit/api/routers/test_executions.py`. Cover complete evidence, missing snapshot, missing checkpoint, missing failed-step identity, missing completed refs, missing workspace checkpoint, missing plan identity, and stale/inconsistent evidence. (FR-001, FR-002, FR-004, FR-005, FR-006, FR-007, FR-011, SCN-001, SCN-002, SC-001, SC-002, SC-003, DESIGN-REQ-001)
- [X] T009 [P] Add failing service/model unit tests for `ResumeCheckpointModel` and `TemporalExecutionService.create_failed_step_resume_execution()` in `tests/unit/workflows/temporal/test_temporal_service.py`. Cover required workspace checkpoint, required plan identity, source workflow/run mismatch, snapshot mismatch, failed-step identity validation, preserved-step refs, and no `create_execution()` call on invalid evidence. (FR-003, FR-004, FR-005, FR-006, FR-007, FR-010, FR-011, FR-012, DESIGN-REQ-002, DESIGN-REQ-004)
- [X] T010 [P] Add failing checkpoint payload policy tests in `tests/schemas/test_temporal_payload_policy.py` or an adjacent schema test to accept compact checkpoint refs and reject inline large or binary checkpoint payload bodies. (FR-009, SCN-005, SC-006, DESIGN-REQ-003)
- [ ] T011 [P] Add failing Task Detail UI tests in `frontend/src/entrypoints/task-detail.test.tsx` verifying Resume button visibility and unavailable reason text come only from backend `actions.canResumeFromFailedStep`, `disabledReasons`, and `resume.disabledReason`. (FR-001, SCN-002, SC-001)
- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/schemas/test_temporal_payload_policy.py` and confirm T008-T010 fail for the expected evidence-gating reasons before production changes. (FR-001 through FR-012)
- [ ] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T011 fails for the expected backend-eligibility display reason before production changes. (FR-001, SCN-002)

### Integration Tests (write first) ⚠️

- [X] T014 [P] Add failing hermetic integration test for complete Resume evidence in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, proving complete snapshot, pinned source IDs, failed-step identity, completed-step refs, workspace checkpoint, and plan identity allow preservation and unblock the failed step. (FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, SCN-001, SCN-004, SC-002, DESIGN-REQ-001, DESIGN-REQ-002)
- [X] T015 [P] Add failing hermetic integration test for invalid Resume evidence in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, proving missing or inconsistent evidence blocks before creating a resumed execution and never silently falls back to full rerun. (FR-011, FR-012, SCN-003, SC-003, SC-004, DESIGN-REQ-004)
- [ ] T016 [P] Add failing hermetic integration test for idempotent checkpoint writes in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` or a new adjacent `integration_ci` test file if the checkpoint write boundary belongs outside the step-ledger helper. (FR-008, SCN-006, SC-005, DESIGN-REQ-003)
- [ ] T017 Run `./tools/test_integration.sh` and confirm T014-T016 fail for the expected missing evidence-gating and checkpoint-idempotency reasons before production changes. (FR-001 through FR-012)

### Red-First Confirmation

- [X] T018 Record the expected failing unit test names and failure reasons in `specs/327-gate-resume-checkpoint-evidence/tasks.md` notes or implementation handoff comments before changing production code. (FR-001 through FR-012)
- [X] T019 Record the expected failing integration test names and failure reasons in `specs/327-gate-resume-checkpoint-evidence/tasks.md` notes or implementation handoff comments before changing production code. (FR-001 through FR-012)

### Fallback Verification Tasks for Implemented-Unverified Rows

- [X] T020 If T008/T009 show current snapshot or source identity checks already satisfy FR-002 or FR-003, mark only the relevant validation subtask complete and skip redundant implementation for those checks; otherwise keep T022-T026 in scope. (FR-002, FR-003)
- [X] T021 If T009/T015 prove invalid Resume already creates no execution and no full rerun fallback, preserve that evidence and skip redundant fallback work for FR-012; otherwise keep T030 in scope. (FR-012)

### Implementation

- [X] T022 Update Resume checkpoint schema validation in `moonmind/schemas/temporal_models.py` to require plan identity and workspace/branch/commit checkpoint evidence, preserve compact refs, and keep task mutation fields forbidden. (FR-006, FR-007, FR-009, DESIGN-REQ-002, DESIGN-REQ-003)
- [X] T023 Update preserved-step evidence modeling in `moonmind/schemas/temporal_models.py` to represent required state checkpoint evidence for preserved completed steps without embedding large or binary content. (FR-005, FR-009, FR-010, DESIGN-REQ-003)
- [X] T024 Add or update compact checkpoint payload validation in `moonmind/schemas/temporal_payload_policy.py` or the chosen checkpoint validation boundary so inline large/binary Resume checkpoint content is rejected while artifact refs pass. (FR-009, SC-006)
- [X] T025 Implement backend Resume eligibility evaluation in `api_service/api/routers/executions.py` or a shared service helper so `canResumeFromFailedStep` and `resume.disabledReason` require the full evidence bundle before the action is exposed. (FR-001, FR-002, FR-004, FR-005, FR-006, FR-007, FR-011, SCN-001, SCN-002)
- [X] T026 Update checkpoint hydration and submission failure mapping in `api_service/api/routers/executions.py` so missing, stale, unauthorized, corrupted, inconsistent, workspace-missing, plan-missing, and completed-ref-missing evidence return bounded operator-readable reasons. (FR-011, SCN-003, SC-003)
- [X] T027 Update `TemporalExecutionService.create_failed_step_resume_execution()` in `moonmind/workflows/temporal/service.py` to validate the complete evidence bundle against the source execution before calling `create_execution()`. (FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-010, FR-011, FR-012, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004)
- [X] T028 Add source ledger and preserved-step completeness checks in `moonmind/workflows/temporal/step_ledger.py` or the service boundary selected by T027 so completed prior steps without recoverable refs or state evidence are not eligible for preservation. (FR-004, FR-005, FR-010, SCN-004)
- [ ] T029 Implement idempotent Resume checkpoint write/create behavior at the workflow or service boundary in `moonmind/workflows/temporal/workflows/run.py`, `moonmind/workflows/temporal/service.py`, or a new focused helper under `moonmind/workflows/temporal/`, using deterministic source workflow/run and failed-step identity. (FR-008, SCN-006, SC-005, DESIGN-REQ-003)
- [X] T030 Add explicit guardrails in `moonmind/workflows/temporal/service.py` ensuring invalid Resume evidence never creates a resumed execution and never calls any full-rerun path. (FR-012, SC-004, DESIGN-REQ-004)
- [ ] T031 Update generated or handwritten frontend/API types only if response schemas or bounded disabled reasons change, including `frontend/src/generated/openapi.ts` when the repository's OpenAPI generation flow requires it. (FR-001, FR-011)
- [ ] T032 Update Task Detail display behavior in `frontend/src/entrypoints/task-detail.tsx` only if T011 proves the UI is inferring eligibility or not showing backend disabled reasons correctly. (FR-001, SCN-002)

### Story Validation

- [X] T033 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/schemas/test_temporal_payload_policy.py` and make T008-T010 pass. (FR-001 through FR-012)
- [ ] T034 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and make T011 pass if UI work was needed. (FR-001, SCN-002)
- [X] T035 Run `./tools/test_integration.sh` and make T014-T016 pass or record the exact blocker if the managed environment cannot run Docker-backed integration tests. (FR-001 through FR-012)
- [ ] T036 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification after focused tests pass. (FR-001 through FR-013)
- [ ] T037 Validate the story against `specs/327-gate-resume-checkpoint-evidence/quickstart.md`, confirming complete evidence enables Resume and invalid evidence blocks before execution without full-rerun fallback. (FR-001 through FR-013, SC-001 through SC-007)

**Checkpoint**: The story is functionally complete, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding scope beyond MM-633.

- [ ] T038 [P] Review `specs/327-gate-resume-checkpoint-evidence/data-model.md` and `contracts/resume-evidence.md` against the final implementation; update only if behavior changed during implementation. (FR-013, SC-007)
- [ ] T039 [P] Review operator-facing error text and logs in `api_service/api/routers/executions.py` and `moonmind/workflows/temporal/service.py` to ensure no raw checkpoint payloads, credentials, or large/binary content are emitted. (FR-009, FR-011, DESIGN-REQ-003)
- [ ] T040 [P] Review compatibility-sensitive payload changes in `moonmind/schemas/temporal_models.py` and `moonmind/workflows/temporal/service.py`; document any required explicit cutover notes in `specs/327-gate-resume-checkpoint-evidence/research.md` or `plan.md` if in-flight compatibility cannot be preserved. (DESIGN-REQ-001 through DESIGN-REQ-004)
- [ ] T041 Preserve MM-633 and the original Jira preset brief in implementation notes, verification output, commit text, and pull request metadata. (FR-013, SC-007)
- [ ] T042 Run `/moonspec-verify` after implementation and tests pass, validating against `specs/327-gate-resume-checkpoint-evidence/spec.md`, `plan.md`, `tasks.md`, and the preserved MM-633 Jira preset brief. (FR-001 through FR-013, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-004)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story tests and implementation.
- **Story (Phase 3)**: Depends on Phase 2; tests must be written and confirmed red before production changes.
- **Polish & Verification (Phase 4)**: Depends on story implementation and tests passing.

### Within The Story

- Unit tests T008-T011 must be written before implementation.
- Red-first confirmations T012-T019 must complete before production changes T022-T032.
- Integration tests T014-T016 must be written before implementation and confirmed red by T017.
- Schema/model changes T022-T024 should happen before service/API changes T025-T030.
- Backend service/API changes T025-T030 should happen before frontend type/display changes T031-T032.
- Story validation T033-T037 must pass before polish and `/moonspec-verify`.

### Parallel Opportunities

- T004-T007 can run in parallel because they touch different test fixture files.
- T008-T011 can run in parallel after foundational fixtures are ready because they touch different test files.
- T014-T016 can run in parallel because they cover distinct integration scenarios.
- T038-T040 can run in parallel after story validation because they review different files.

## Parallel Example: Story Test Authoring

```bash
Task: "Add failing API eligibility matrix tests in tests/unit/api/routers/test_executions.py"
Task: "Add failing service/model evidence tests in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "Add failing checkpoint payload policy tests in tests/schemas/test_temporal_payload_policy.py"
Task: "Add failing integration invalid-evidence test in tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py"
```

## Implementation Strategy

1. Complete setup and fixture tasks T001-T007.
2. Write unit tests T008-T011 and integration tests T014-T016 before production changes.
3. Run T012, T013, T017 and confirm expected red-first failures.
4. Apply fallback decisions T020-T021 for implemented-unverified rows based on verification evidence.
5. Implement schema, payload, eligibility, service, ledger, idempotency, API, and UI changes T022-T032.
6. Run focused unit, UI, and integration validation T033-T035.
7. Run full unit validation and quickstart story validation T036-T037.
8. Complete polish and final `/moonspec-verify` tasks T038-T042.

## Coverage Matrix

| Requirement / Scenario | Test Tasks | Implementation / Validation Tasks |
| --- | --- | --- |
| FR-001, SCN-001, SC-001, DESIGN-REQ-001 | T008, T011, T014 | T025, T032, T033-T037 |
| FR-002 | T008, T009, T014 | T020, T025, T027, T033-T037 |
| FR-003 | T009, T014 | T020, T027, T033-T037 |
| FR-004, SCN-004 | T008, T009, T014 | T025, T027, T028, T033-T037 |
| FR-005, FR-010 | T008, T009, T014 | T023, T027, T028, T033-T037 |
| FR-006 | T008, T009, T014 | T022, T025, T027, T033-T037 |
| FR-007, SCN-007 | T008, T009, T014 | T022, T025, T027, T033-T037 |
| FR-008, SCN-006, SC-005 | T016, T017 | T029, T035, T037 |
| FR-009, SCN-005, SC-006, DESIGN-REQ-003 | T010 | T022-T024, T039 |
| FR-011, SCN-002, SCN-003, SC-003 | T008, T009, T011, T015 | T025-T027, T032, T033-T037 |
| FR-012, SC-004, DESIGN-REQ-004 | T009, T015 | T021, T030, T035, T037 |
| FR-013, SC-007 | T001, T002 | T038, T041, T042 |

## Notes

- Tasks marked `[P]` are safe to run in parallel because they touch separate files and do not depend on incomplete task output.
- This task list intentionally includes conditional fallback implementation tasks for implemented-unverified rows; skip only when the new verification tests prove existing behavior already satisfies the requirement.
- Do not create commits, pull requests, Jira transitions, or implementation changes during task generation.

## Implementation Notes

- Red-first unit confirmation before production changes:
  - `test_describe_execution_requires_complete_resume_evidence[...]` failed because `actions.canResumeFromFailedStep` was still true with incomplete evidence.
  - `test_resume_checkpoint_model_requires_plan_identity`, `test_resume_checkpoint_model_requires_workspace_checkpoint`, and `test_resume_checkpoint_model_requires_preserved_step_state_checkpoint` failed because checkpoint evidence fields were optional.
  - `test_resume_checkpoint_rejects_large_inline_workspace_content` failed because checkpoint workspace metadata accepted large inline content.
- Red-first integration confirmation before production changes:
  - `test_failed_step_resume_preserves_prior_steps_and_unblocks_failed_step` failed because preserved ledger rows did not carry `stateCheckpointRef`.
  - `test_failed_step_resume_rejects_preserved_step_without_state_checkpoint` failed because missing state checkpoint evidence did not block preservation.
- Post-implementation verification:
  - `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/schemas/test_temporal_payload_policy.py` passed before the final additional focused guardrail tests.
  - `pytest tests/unit/api/routers/test_executions.py -q --tb=short -k "resume"` passed: 8 passed.
  - `pytest tests/unit/workflows/temporal/test_temporal_service.py -q --tb=short -k "resume_checkpoint or failed_step_resume"` passed: 10 passed.
  - `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/schemas/test_temporal_payload_policy.py` passed: 7 Python tests passed; the runner also executed the frontend unit suite, 20 files passed with 324 passed and 225 skipped.
  - `pytest tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py -q --tb=short` passed: 2 passed.
  - `./tools/test_integration.sh` was blocked in this managed environment by Docker/daemon access: `403 Forbidden` from administrative rules after compose attempted to build `repo-pytest`.
