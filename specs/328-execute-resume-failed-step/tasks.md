# Tasks: Execute Resume From the Failed Step Only

**Input**: Design documents from `specs/328-execute-resume-failed-step/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/resume-execution.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code until they pass.

**Organization**: Tasks are grouped around one independently testable story: Failed-Step Resume Execution.

**Source Traceability**: MM-634 and the original Jira preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-013, acceptance scenarios 1-7, edge cases, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-005.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Focused UI tests when Task Detail display changes: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because the task touches different files and has no dependency on incomplete work
- Each task includes exact file paths and requirement, scenario, or source IDs when applicable
- This task list covers exactly one story and must not add hidden scope beyond MM-634

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and test targets before writing red-first tests.

- [ ] T001 Confirm `specs/328-execute-resume-failed-step/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/resume-execution.md`, and `quickstart.md` are present and preserve MM-634 traceability. (FR-013, SC-008)
- [ ] T002 Confirm `specs/328-execute-resume-failed-step/spec.md` has exactly one `## User Story` section and no `[NEEDS CLARIFICATION]` markers. (FR-013, SC-008)
- [ ] T003 Identify current focused test targets and fixtures in `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py`, `tests/unit/api/routers/test_executions.py`, and `frontend/src/entrypoints/task-detail.test.tsx`. (FR-001 through FR-012)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Prepare reusable fixture coverage for resumed execution ordering, workspace restoration, preserved provenance, and no-fallback behavior.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 [P] Extend Resume checkpoint fixture builders in `tests/unit/workflows/temporal/test_temporal_service.py` with complete resume workspace evidence, plan identity, preserved outputs, and invalid restoration cases. (FR-002, FR-003, FR-004, FR-007, FR-010)
- [ ] T005 [P] Extend preserved-step ledger fixtures in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` for source logical step provenance, preserved output refs, and skipped re-execution assertions. (FR-005, FR-006, FR-007, FR-011)
- [ ] T006 [P] Extend hermetic integration fixtures in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` for a three-step graph where the first step is preserved, the second step is retried, and the third step runs downstream. (FR-004, FR-008, FR-009, DESIGN-REQ-003)
- [ ] T007 [P] Extend Task Detail UI mock payload helpers in `frontend/src/entrypoints/task-detail.test.tsx` only if preserved provenance shape changes. (FR-012)

**Checkpoint**: Test fixtures are ready; story tests and implementation work can now begin.

---

## Phase 3: Story - Failed-Step Resume Execution

**Summary**: As a user pressing Resume on a failed task, I want completed prior work restored and preserved, the failed step retried first, and later steps continued normally without silent input mutation or full-rerun fallback.

**Independent Test**: Start a Resume attempt from a failed task with complete checkpoint evidence, then verify the resumed run preserves prior completed steps with source provenance, restores state before the failed step, injects preserved outputs, executes the failed step first, continues later steps normally, and fails before execution for invalid restoration evidence.

**Traceability**: FR-001 through FR-013; acceptance scenarios 1-7; SC-001 through SC-008; DESIGN-REQ-001 through DESIGN-REQ-005.

**Unit Test Plan**:

- Temporal service tests for identity validation before creation and no execution creation on invalid restoration.
- Step ledger tests for preserved source workflow/run/logical-step/attempt provenance.
- Step ledger or workflow helper tests proving preserved artifact refs are retained for failed/downstream steps.
- Task Detail tests if preserved provenance display shape changes.

**Integration Test Plan**:

- Hermetic resumed-run ordering test proving preserved prior step is not executed, failed step is first newly executable, and downstream step runs after success.
- Hermetic invalid-restoration test proving no full rerun and no preserved-step re-execution.
- Hermetic workspace restoration boundary test proving workspace/branch/commit evidence is applied before new work.

### Unit Tests (write first)

> Write these tests FIRST. Run them and confirm they fail for the expected reason before production implementation.

- [ ] T008 [P] Add failing service tests in `tests/unit/workflows/temporal/test_temporal_service.py` proving Resume validates source workflow ID, source run ID, snapshot identity, and plan identity before creating a resumed execution. (FR-002, FR-003, SC-002, DESIGN-REQ-002)
- [ ] T009 [P] Add failing service tests in `tests/unit/workflows/temporal/test_temporal_service.py` proving invalid restoration evidence creates no resumed execution and cannot call any full-rerun path. (FR-003, FR-010, SC-007, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T010 [P] Add failing step ledger unit tests in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` requiring `preservedFrom.logicalStepId` in addition to source workflow ID, run ID, and attempt. (FR-006, SC-004, DESIGN-REQ-003)
- [ ] T011 [P] Add failing unit tests in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` proving preserved artifact refs and state checkpoint refs remain on preserved rows for downstream consumption. (FR-005, FR-007, SC-005, DESIGN-REQ-002)
- [ ] T012 [P] Add failing Task Detail UI test in `frontend/src/entrypoints/task-detail.test.tsx` if `preservedFrom.logicalStepId` changes the rendered contract or parsing schema. (FR-012)
- [ ] T013 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and confirm T008-T011 fail for the expected MM-634 reasons before production changes. (FR-001 through FR-012)
- [ ] T014 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and confirm T012 fails only if UI work is needed. (FR-012)

### Integration Tests (write first)

- [ ] T015 [P] Add failing hermetic integration test in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` proving preserved prior steps are not executed and the failed step is the first newly executable step. (FR-005, FR-008, FR-011, SC-006, DESIGN-REQ-002, DESIGN-REQ-003)
- [ ] T016 [P] Add failing hermetic integration test in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` proving workspace, branch, commit, or equivalent `resumeWorkspace` evidence is applied before the failed step starts. (FR-004, SC-003, DESIGN-REQ-003, DESIGN-REQ-005)
- [ ] T017 [P] Add failing hermetic integration test in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` proving invalid restoration fails before execution without full-rerun fallback or preserved-step re-execution. (FR-003, FR-010, FR-011, SC-007, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T018 [P] Add failing hermetic integration test in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` proving downstream steps execute normally after the retried failed step succeeds and produce fresh resumed-run evidence. (FR-007, FR-009, SC-005, DESIGN-REQ-002)
- [ ] T019 Run `./tools/test_integration.sh` or `pytest tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py -q --tb=short` and confirm T015-T018 fail for the expected MM-634 reasons before production changes. (FR-004 through FR-011)

### Red-First Confirmation

- [ ] T020 Record expected failing unit test names and failure reasons in `specs/328-execute-resume-failed-step/tasks.md` before changing production code. (FR-001 through FR-012)
- [ ] T021 Record expected failing integration test names and failure reasons in `specs/328-execute-resume-failed-step/tasks.md` before changing production code. (FR-004 through FR-011)

### Conditional Verification Tasks for Implemented-Unverified Rows

- [ ] T022 If T008 proves current identity validation already satisfies FR-002, preserve the evidence and skip redundant service implementation; otherwise keep T026 in scope. (FR-002)
- [ ] T023 If T009 proves invalid restoration already creates no execution and no full-rerun fallback, preserve the evidence and skip redundant no-fallback implementation; otherwise keep T030 in scope. (FR-003, FR-010)
- [ ] T024 If T012 is unnecessary because the UI schema already renders the existing provenance shape without contract changes, record that no UI implementation is required; otherwise keep T031 in scope. (FR-012)

### Implementation

- [ ] T025 Update preserved-step provenance in `moonmind/workflows/temporal/step_ledger.py` and `moonmind/schemas/temporal_models.py` so preserved rows carry source workflow ID, source run ID, logical step ID, and source attempt. (FR-006, SC-004, DESIGN-REQ-003)
- [ ] T026 Update `TemporalExecutionService.create_failed_step_resume_execution()` in `moonmind/workflows/temporal/service.py` to keep validation before resumed execution creation and cover any missing identity/restoration preconditions exposed by T008-T009. (FR-002, FR-003, FR-010, DESIGN-REQ-001, DESIGN-REQ-002)
- [ ] T027 Implement or expose workspace, branch, commit, or equivalent restoration from `resumeWorkspace` before the failed step in `moonmind/workflows/temporal/workflows/run.py` or the selected runtime restoration boundary. (FR-004, SC-003, DESIGN-REQ-003, DESIGN-REQ-005)
- [ ] T028 Update preserved output propagation in `moonmind/workflows/temporal/step_ledger.py`, `moonmind/workflows/temporal/workflows/run.py`, or the selected context generation boundary so failed and downstream steps receive preserved outputs as continuous-run inputs. (FR-007, SC-005, DESIGN-REQ-002)
- [ ] T029 Update resumed-run step progression in `moonmind/workflows/temporal/workflows/run.py` so preserved prior steps are skipped, the failed step is first newly executed, and downstream steps produce fresh resumed-run ledger rows, artifacts, and checkpoints. (FR-005, FR-008, FR-009, FR-011, DESIGN-REQ-002, DESIGN-REQ-003)
- [ ] T030 Add explicit no-fallback/no-reexecution guardrails in `moonmind/workflows/temporal/service.py` or `moonmind/workflows/temporal/workflows/run.py` for restoration failures that occur after checkpoint validation but before the failed step starts. (FR-003, FR-010, FR-011, DESIGN-REQ-001, DESIGN-REQ-004)
- [ ] T031 Update `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/generated/openapi.ts` only if provenance schema or display behavior changes require UI contract updates. (FR-012)

### Story Validation

- [ ] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and make T008-T011 pass. (FR-001 through FR-012)
- [ ] T033 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` and make T012 pass if UI work was needed. (FR-012)
- [ ] T034 Run `pytest tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py -q --tb=short` and make T015-T018 pass. (FR-004 through FR-011)
- [ ] T035 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification after focused tests pass. (FR-001 through FR-013)
- [ ] T036 Run `./tools/test_integration.sh` for required hermetic integration verification or record the exact Docker/environment blocker. (FR-001 through FR-013)
- [ ] T037 Validate the story against `specs/328-execute-resume-failed-step/quickstart.md`, confirming original input reuse, checkpoint validation, workspace restoration, preserved progress, failed-step-first execution, downstream progression, and no fallback. (FR-001 through FR-013, SC-001 through SC-008)

**Checkpoint**: The story is functionally complete, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding scope beyond MM-634.

- [ ] T038 [P] Review `specs/328-execute-resume-failed-step/data-model.md` and `contracts/resume-execution.md` against the final implementation; update only if behavior changed during implementation. (FR-013, SC-008)
- [ ] T039 [P] Review operator-facing error text and logs in `moonmind/workflows/temporal/service.py` and `moonmind/workflows/temporal/workflows/run.py` to ensure no raw checkpoint payloads, credentials, or large/binary content are emitted. (FR-003, FR-010, DESIGN-REQ-001)
- [ ] T040 [P] Review compatibility-sensitive payload changes in `moonmind/schemas/temporal_models.py`, `moonmind/workflows/temporal/service.py`, and `moonmind/workflows/temporal/workflows/run.py`; document explicit cutover notes in `specs/328-execute-resume-failed-step/research.md` or `plan.md` if in-flight compatibility cannot be preserved. (DESIGN-REQ-001 through DESIGN-REQ-005)
- [ ] T041 Preserve MM-634 and the original Jira preset brief in implementation notes, verification output, commit text, and pull request metadata. (FR-013, SC-008)
- [ ] T042 Run `/moonspec-verify` after implementation and tests pass, validating against `specs/328-execute-resume-failed-step/spec.md`, `plan.md`, `tasks.md`, and the preserved MM-634 Jira preset brief. (FR-001 through FR-013, SC-001 through SC-008, DESIGN-REQ-001 through DESIGN-REQ-005)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; start immediately.
- **Foundational (Phase 2)**: Depends on Phase 1; blocks story tests and implementation.
- **Story (Phase 3)**: Depends on Phase 2; tests must be written and confirmed red before production changes.
- **Polish & Verification (Phase 4)**: Depends on story implementation and tests passing.

### Within The Story

- Unit tests T008-T012 must be written before implementation.
- Integration tests T015-T018 must be written before implementation.
- Red-first confirmations T020-T021 must complete before production changes T025-T031.
- Conditional verification tasks T022-T024 decide which fallback implementation work remains necessary.
- Provenance/schema work T025 should happen before UI/schema display work T031.
- Runtime restoration/output/progression work T027-T030 should happen after service validation gaps are understood by T026.
- Story validation T032-T037 must pass before polish and `/moonspec-verify`.

### Parallel Opportunities

- T004-T007 can run in parallel because they touch different fixture files.
- T008-T012 can run in parallel after foundational fixtures are ready.
- T015-T018 can run in parallel because they cover distinct integration scenarios.
- T038-T040 can run in parallel after story validation because they review different files.

## Parallel Example: Story Test Authoring

```bash
Task: "Add failing service identity/no-fallback tests in tests/unit/workflows/temporal/test_temporal_service.py"
Task: "Add failing preserved provenance tests in tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py"
Task: "Add failing resumed-run ordering integration tests in tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py"
Task: "Add failing Task Detail preserved provenance test in frontend/src/entrypoints/task-detail.test.tsx if UI shape changes"
```

## Implementation Strategy

1. Confirm and preserve existing behavior that already satisfies implemented-unverified rows.
2. Add red-first tests for the missing/partial rows: workspace restoration, logical step provenance, output injection, failed-step-first execution, downstream fresh evidence, and no re-execution.
3. Implement the smallest changes at existing boundaries: checkpoint service validation, step ledger provenance/materialization, `MoonMind.Run` resume initialization/restoration, and Task Detail display only if needed.
4. Run focused tests, then full unit and hermetic integration verification.
5. Run `/moonspec-verify` against the preserved MM-634 brief and all generated artifacts.
