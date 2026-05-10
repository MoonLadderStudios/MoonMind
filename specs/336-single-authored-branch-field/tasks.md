# Tasks: Single Authored Branch Field

**Input**: Design documents from `specs/336-single-authored-branch-field/`
**Prerequisites**: `specs/336-single-authored-branch-field/spec.md`, `specs/336-single-authored-branch-field/plan.md`, `specs/336-single-authored-branch-field/research.md`, `specs/336-single-authored-branch-field/data-model.md`, `specs/336-single-authored-branch-field/contracts/single-authored-branch-contract.md`, `specs/336-single-authored-branch-field/quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks cover exactly one story: `Normalize Authored Branch Input`.

**Source Traceability**: Original Jira issue `MM-668` and the preset brief are preserved in `spec.md`. Tasks cover FR-001 through FR-011, acceptance scenarios 1-4, edge cases, SC-001 through SC-005, and source mappings DESIGN-REQ-009 and DESIGN-REQ-010.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Focused frontend tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Focused Python unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py tests/unit/agents/codex_worker/test_worker.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and has no dependency on incomplete tasks.
- Every task names exact file paths and relevant traceability IDs.

## Phase 1: Setup

**Purpose**: Confirm the task-generation inputs and current tooling surfaces before story work starts.

- [ ] T001 Confirm `specs/336-single-authored-branch-field/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/single-authored-branch-contract.md`, and `quickstart.md` are present and preserve `MM-668`, DESIGN-REQ-009, and DESIGN-REQ-010.
- [ ] T002 Confirm the current branch-name prerequisite-script limitation is recorded in `specs/336-single-authored-branch-field/plan.md` and does not change the active feature directory from `.specify/feature.json`.
- [ ] T003 [P] Confirm focused frontend test routing works through `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` before editing frontend files.
- [ ] T004 [P] Confirm focused Python unit routing works through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py tests/unit/agents/codex_worker/test_worker.py` before editing backend/runtime files.

## Phase 2: Foundational

**Purpose**: Establish the branch-field contract inventory that blocks safe implementation.

- [ ] T005 Map every active `startingBranch` and `targetBranch` production reference in `frontend/src/lib/temporalTaskEditing.ts`, `frontend/src/entrypoints/task-create.tsx`, `moonmind/workflows/tasks/task_contract.py`, `api_service/api/routers/executions.py`, and `moonmind/agents/codex_worker/worker.py` to either authored input, legacy metadata, or runtime-owned generated metadata for FR-004, FR-007, FR-008, FR-010, DESIGN-REQ-009, and DESIGN-REQ-010.
- [ ] T006 Map existing test coverage for `startingBranch` and `targetBranch` in `frontend/src/entrypoints/task-create.test.tsx`, `tests/unit/workflows/tasks/test_task_contract.py`, `tests/unit/api/routers/test_executions.py`, `tests/unit/agents/codex_worker/test_worker.py`, `tests/integration/api/test_task_contract_normalization.py`, and `tests/integration/temporal/test_task_shaped_submission_normalization.py` before adding new tests.
- [ ] T007 Update `specs/336-single-authored-branch-field/contracts/single-authored-branch-contract.md` only if T005 reveals a contract term that is ambiguous between authored `targetBranch` and runtime-owned head/working branch metadata for FR-008 and DESIGN-REQ-010.

**Checkpoint**: Branch-field surfaces are classified; story test and implementation work can begin.

## Phase 3: Story - Normalize Authored Branch Input

**Summary**: As an operator and platform owner, I want authored task submissions to expose one branch value while legacy two-branch snapshots are reconstructed safely so that new work has a clear branch contract and old work remains auditable.

**Independent Test**: Create a new branch-publish submission, reconstruct legacy `startingBranch`/`targetBranch` snapshots, and confirm the submitted or reconstructed task contract exposes one active branch, preserves publish mode, retains only historical target-branch metadata, shows warnings for unreconstructable legacy snapshots, and never lets `targetBranch` drive active submission or runtime preparation.

**Traceability**: FR-001 through FR-011; acceptance scenarios 1-4; edge cases for missing branch, target-only legacy, equal legacy branches, unknown branch-shaped fields, and editing after warning; SC-001 through SC-005; DESIGN-REQ-009; DESIGN-REQ-010.

**Unit Test Plan**:

- Frontend unit/UI tests cover authored create payloads, edit/rerun patch output, target-only legacy reconstruction, two-branch branch-publish warnings, publish-mode preservation, and warning visibility.
- Python unit tests cover canonical task contract rejection/stripping of active `targetBranch`, API route validation, and runtime worker preparation no longer reading authored `git.targetBranch` as active input.

**Integration Test Plan**:

- API integration tests cover task-shaped submission normalization/rejection and persisted snapshot shape.
- Temporal integration tests cover task-shaped submission payloads and runtime preparation boundaries where feasible without external credentials.

### Unit Tests (write first)

- [ ] T008 [P] Add failing frontend test for target-only legacy reconstruction in `frontend/src/entrypoints/task-create.test.tsx` proving `targetBranch` is shown only as warning/audit context and does not prefill active `branch` for FR-007, FR-008, SC-004, and DESIGN-REQ-010.
- [ ] T009 [P] Add failing frontend test for two-branch branch-publish reconstruction in `frontend/src/entrypoints/task-create.test.tsx` proving the warning appears and subsequent submitted payload omits `startingBranch` and `targetBranch` for FR-009, FR-010, FR-011, SC-005, and DESIGN-REQ-010.
- [ ] T010 [P] Add failing frontend regression test in `frontend/src/entrypoints/task-create.test.tsx` proving new authored submissions preserve `publishMode`/`task.publish.mode` and still submit only `task.git.branch` for FR-001, FR-002, FR-003, SC-001, SC-002, and DESIGN-REQ-009.
- [ ] T011 [P] Add failing task-contract unit tests in `tests/unit/workflows/tasks/test_task_contract.py` replacing legacy `targetBranch` normalization expectations with active-authored `targetBranch` rejection or historical-only stripping for FR-004, FR-010, SC-001, and DESIGN-REQ-009.
- [ ] T012 [P] Add failing API route unit tests in `tests/unit/api/routers/test_executions.py` proving top-level, task-level, and `task.git.targetBranch` aliases cannot create active authored branch intent for FR-004, FR-005, FR-010, and DESIGN-REQ-009.
- [ ] T013 [P] Add failing worker unit tests in `tests/unit/agents/codex_worker/test_worker.py` proving `moonmind/agents/codex_worker/worker.py` ignores or rejects authored `task.git.targetBranch` as active branch input while preserving generated runtime branch metadata for FR-008, SC-001, and DESIGN-REQ-010.
- [ ] T014 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T008-T010 fail for the expected branch-contract reasons before changing `frontend/src/lib/temporalTaskEditing.ts` or `frontend/src/entrypoints/task-create.tsx`.
- [ ] T015 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py tests/unit/agents/codex_worker/test_worker.py` and confirm T011-T013 fail for the expected branch-contract reasons before changing backend/runtime code.

### Integration Tests (write first)

- [ ] T016 [P] Add failing integration coverage in `tests/integration/api/test_task_contract_normalization.py` proving task-shaped submissions reject active `targetBranch` aliases and persist canonical `task.git.branch` only for FR-004, FR-010, SC-001, and DESIGN-REQ-009.
- [ ] T017 [P] Add failing integration coverage in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving original task input snapshots and Temporal submission payloads omit active `targetBranch` for new authored submissions for FR-004, FR-008, SC-001, and DESIGN-REQ-009.
- [ ] T018 [P] Add failing integration or boundary coverage in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving legacy reconstruction evidence preserves warning/metadata without submitting `targetBranch` as active branch for FR-007, FR-009, FR-011, SC-004, SC-005, and DESIGN-REQ-010.
- [ ] T019 Run focused integration checks for `tests/integration/api/test_task_contract_normalization.py` and `tests/integration/temporal/test_task_shaped_submission_normalization.py` through the available repo test runner and confirm T016-T018 fail for the expected branch-contract reasons before implementation.

### Conditional Verification-Only Work

- [ ] T020 If T010 passes before implementation, record existing evidence for FR-001, FR-002, FR-003, SC-002, and DESIGN-REQ-009 in `specs/336-single-authored-branch-field/tasks.md`; otherwise keep the implementation tasks below active.
- [ ] T021 If T009 and T018 pass before implementation, record existing evidence for FR-009, FR-011, and SC-005 in `specs/336-single-authored-branch-field/tasks.md`; otherwise keep the warning persistence implementation tasks below active.
- [ ] T022 If T012 passes before implementation, record existing evidence for FR-005 in `specs/336-single-authored-branch-field/tasks.md`; otherwise keep the branch-required validation implementation tasks below active.

### Implementation

- [ ] T023 Update `frontend/src/lib/temporalTaskEditing.ts` so target-only legacy snapshots do not set active `branch` from `targetBranch`, and instead return warning/audit metadata for FR-007, FR-008, SC-004, and DESIGN-REQ-010.
- [ ] T024 Update `frontend/src/lib/temporalTaskEditing.ts` so two-branch branch-publish snapshots preserve a reconstruction warning and ensure reconstructed draft output submits only `branch` for FR-009, FR-010, FR-011, SC-005, and DESIGN-REQ-010.
- [ ] T025 Update `frontend/src/entrypoints/task-create.tsx` so edit/rerun submission blocks or clearly warns when legacy target-only data leaves no active `branch` for a branch-required publish mode, without deriving active branch intent from legacy fields, for FR-005, FR-007, FR-008, and SC-004.
- [ ] T026 Update `moonmind/workflows/tasks/task_contract.py` so new authored task contracts no longer normalize active `targetBranch` into `branch`, while preserving allowed historical metadata only through explicit legacy reconstruction paths, for FR-004, FR-010, SC-001, and DESIGN-REQ-009.
- [ ] T027 Update `api_service/api/routers/executions.py` only as needed to keep task-shaped submission validation aligned with the single authored branch contract and field-specific errors for FR-004, FR-005, FR-010, and DESIGN-REQ-009.
- [ ] T028 Update `moonmind/agents/codex_worker/worker.py` so runtime preparation reads active authored branch intent only from `task.git.branch`, does not read authored `task.git.targetBranch`, and keeps generated working/head branch metadata separate for FR-008, SC-001, and DESIGN-REQ-010.
- [ ] T029 Update `moonmind/schemas/temporal_models.py` and `moonmind/schemas/temporal_activity_models.py` only if implementation changes require typed payload fields to distinguish legacy branch metadata from active authored branch input for FR-007, FR-008, and DESIGN-REQ-010.
- [ ] T030 Update generated frontend/API type fixtures only if backend contract changes require them, including `frontend/src/generated/openapi.ts`, for FR-004, FR-010, and DESIGN-REQ-009.

### Story Validation

- [ ] T031 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and fix failures until FR-001 through FR-003, FR-006, FR-007, FR-009, FR-011, SC-002 through SC-005, DESIGN-REQ-009, and DESIGN-REQ-010 pass in frontend coverage.
- [ ] T032 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/tasks/test_task_contract.py tests/unit/api/routers/test_executions.py tests/unit/agents/codex_worker/test_worker.py` and fix failures until FR-004, FR-005, FR-008, FR-010, SC-001, DESIGN-REQ-009, and DESIGN-REQ-010 pass in backend/runtime unit coverage.
- [ ] T033 Run the focused integration checks for `tests/integration/api/test_task_contract_normalization.py` and `tests/integration/temporal/test_task_shaped_submission_normalization.py` when integration dependencies are available, and record any environment blocker in `specs/336-single-authored-branch-field/tasks.md` for FR-004, FR-008, FR-010, SC-001, SC-004, SC-005, DESIGN-REQ-009, and DESIGN-REQ-010.
- [ ] T034 Run the end-to-end story checklist from `specs/336-single-authored-branch-field/quickstart.md` and confirm the one-story independent test passes for MM-668.

**Checkpoint**: The single story is implemented, covered by red-first unit and integration tests, and independently validated.

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T035 [P] Update `docs/Tasks/TaskPublishing.md` only if implementation reveals canonical desired-state wording that is stale for DESIGN-REQ-009 or DESIGN-REQ-010.
- [ ] T036 [P] Re-run `rg -n "targetBranch|startingBranch|git\\.branch|publishMode" frontend/src api_service moonmind tests specs/336-single-authored-branch-field` and confirm remaining legacy branch references are classified as historical metadata, runtime-owned generated metadata, tests, or source-design traceability for FR-004, FR-007, FR-008, FR-010, DESIGN-REQ-009, and DESIGN-REQ-010.
- [ ] T037 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and fix failures until the required unit suite passes for MM-668.
- [ ] T038 Run `./tools/test_integration.sh` when Docker/integration dependencies are available, or record the exact blocker in `specs/336-single-authored-branch-field/tasks.md` for final verification.
- [ ] T039 Run `/moonspec-verify` for `specs/336-single-authored-branch-field/spec.md` and preserve the final verification report with coverage for MM-668, FR-001 through FR-011, SC-001 through SC-005, DESIGN-REQ-009, and DESIGN-REQ-010.

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish And Verification (Phase 4)**: Depends on story implementation and story validation passing.

### Within The Story

- T008-T013 must be written before implementation.
- T014-T015 must confirm unit tests fail for expected reasons before T023-T030.
- T016-T018 must be written before implementation.
- T019 must confirm integration tests fail or record the exact environment blocker before T023-T030.
- T020-T022 are verification-only checks for implemented_unverified rows; skip related fallback implementation only when the new verification tests already pass.
- T023-T030 implement production changes after red-first confirmation.
- T031-T034 validate the story after implementation.
- T039 runs only after unit/integration evidence is available or blockers are recorded.

## Parallel Opportunities

- T003 and T004 can run in parallel.
- T008, T011, T012, and T013 can run in parallel because they touch different test files.
- T016, T017, and T018 can run in parallel if each edits a distinct test section without merge conflicts.
- T023 and T026 can run in parallel only if coordinated with T025/T027 because frontend and backend/runtime files are disjoint.
- T035 and T036 can run in parallel after story validation.

## Parallel Example: Story Phase

```bash
Task: "Add failing frontend reconstruction tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "Add failing task contract tests in tests/unit/workflows/tasks/test_task_contract.py"
Task: "Add failing API route tests in tests/unit/api/routers/test_executions.py"
Task: "Add failing worker tests in tests/unit/agents/codex_worker/test_worker.py"
```

## Implementation Strategy

1. Preserve already-verified direct Create-page behavior for FR-001, FR-002, FR-003, and SC-003.
2. Write focused tests for partial and implemented_unverified rows before production edits.
3. Confirm the tests fail for the intended reason so legacy `targetBranch` behavior is proven before cleanup.
4. Remove active authored `targetBranch` semantics from reconstruction, task contract, API validation, and runtime preparation.
5. Preserve safe `startingBranch` normalization and runtime-owned generated branch metadata.
6. Run focused frontend, backend unit, and integration validation.
7. Run full unit/integration verification and final `/moonspec-verify`.

## Requirement Status Coverage

- Already verified rows preserved through final validation: FR-001, FR-002, FR-003, FR-006, SC-003.
- Partial rows requiring code and tests: FR-004, FR-007, FR-008, FR-010, DESIGN-REQ-009, DESIGN-REQ-010, SC-001, SC-004.
- Implemented-unverified rows requiring verification tests plus conditional fallback work: FR-005, FR-009, FR-011, SC-002, SC-005.
- No rows are intentionally out of scope for this single story.
