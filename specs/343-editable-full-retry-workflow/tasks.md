# Tasks: Editable Full Retry Workflow

**Input**: Design documents from `specs/343-editable-full-retry-workflow/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/editable-full-retry.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason when they cover partial or missing behavior, then implement the production code until they pass.

**Organization**: Tasks cover exactly one independently testable story: Editable Full Retry From Snapshot.

**Source Traceability**: Preserves MM-644 and maps FR-001 through FR-012, acceptance scenarios 1-7, SC-001 through SC-005, and DESIGN-REQ-001 through DESIGN-REQ-006.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Focused UI tests: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx`
- Focused Python unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and do not depend on each other.
- All task rows use `- [ ] T###` format and include concrete file paths.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the current feature artifacts and existing test surfaces before authoring tests.

- [ ] T001 Confirm `specs/343-editable-full-retry-workflow/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/editable-full-retry.md`, and `quickstart.md` are present and preserve MM-644 traceability.
- [ ] T002 Confirm existing focused test targets in `frontend/src/entrypoints/task-create.test.tsx`, `frontend/src/entrypoints/task-detail.test.tsx`, `tests/unit/api/routers/test_executions.py`, `tests/unit/workflows/tasks/test_task_contract.py`, and `tests/integration/temporal/` match the paths in `specs/343-editable-full-retry-workflow/quickstart.md`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish shared fixtures and contract anchors needed by the story tests.

**CRITICAL**: No story implementation work begins until this phase is complete.

- [ ] T003 [P] Add or extend reusable edit-for-rerun execution fixtures in `frontend/src/entrypoints/task-create.test.tsx` with authoritative snapshot, source run ID, editable fields, and `canEditForRerun` coverage for FR-001, FR-002, SCN-001, and DESIGN-REQ-001.
- [ ] T004 [P] Add or extend Task Detail action fixture builders in `frontend/src/entrypoints/task-detail.test.tsx` for failed `MoonMind.Run` executions with `canEditForRerun`, `canRerun`, disabled reasons, and missing snapshot cases for FR-001, FR-011, SCN-007, and SC-004.
- [ ] T005 [P] Add or extend Python API/service fixture helpers in `tests/unit/api/routers/test_executions.py` for task input snapshot descriptors, artifact authorization/read failures, and action capability assertions covering FR-001 and FR-011.
- [ ] T006 [P] Add integration fixture scaffolding in `tests/integration/temporal/test_editable_full_retry_workflow.py` for failed source executions with task snapshot refs, Resume-shaped progress metadata, and source artifact/checkpoint refs covering FR-005 through FR-009.

**Checkpoint**: Shared fixtures are ready; story test authoring can begin.

---

## Phase 3: Story - Editable Full Retry From Snapshot

**Summary**: As a Mission Control user recovering from a failed execution, I want Edit task to open an editable retry from the original task snapshot so I can change the authored task and start a new full execution without mutating the failed run or importing partial progress.

**Independent Test**: Start from a failed execution with an authoritative task snapshot, choose Edit task, confirm the form is hydrated from the original snapshot, change representative authoring fields, submit the edited retry, and verify that the new execution starts from the beginning with its own snapshot while the failed execution evidence remains unchanged.

**Traceability**: FR-001 through FR-012; SCN-001 through SCN-007; SC-001 through SC-005; DESIGN-REQ-001 through DESIGN-REQ-006.

**Unit Test Plan**:

- Python unit: action capability eligibility, disabled reasons, recovery provenance, exact-vs-edited full retry distinction, Resume carryover stripping.
- UI unit: Task Detail edit link, edit-for-rerun route resolution, snapshot hydration, editable form submission, normal validation, blocked snapshot states.

**Integration Test Plan**:

- Hermetic Temporal/API boundary: changed edited full retry creates a new execution, writes its own snapshot, preserves source immutability, starts from the beginning, strips Resume/progress refs, and records edited-full-retry provenance.

### Unit Tests (write first)

- [ ] T007 [P] Add API unit tests for `canEditForRerun` eligibility and disabled reasons in `tests/unit/api/routers/test_executions.py` covering FR-001, FR-011, SCN-007, SC-004, and DESIGN-REQ-001.
- [ ] T008 [P] Add task contract unit tests for `edited_full_retry` provenance, required source workflow/run IDs, and rejection of paired Resume refs in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-010, FR-009, and SC-003.
- [ ] T009 [P] Add Task Detail UI tests for failed execution Edit task link, disabled snapshot reason behavior, and no local inference from status in `frontend/src/entrypoints/task-detail.test.tsx` covering FR-001, FR-011, SCN-001, and SCN-007.
- [ ] T010 [P] Add Create page UI tests for `rerunExecutionId&mode=edit` route resolution and authoritative snapshot hydration in `frontend/src/entrypoints/task-create.test.tsx` covering FR-002, SCN-001, SC-001, and DESIGN-REQ-001.
- [ ] T011 [P] Add Create page UI tests proving edit-for-rerun permits representative authoring edits and applies normal validation before submission in `frontend/src/entrypoints/task-create.test.tsx` covering FR-003, FR-004, SCN-002, and DESIGN-REQ-002.
- [ ] T012 [P] Add Create page UI tests proving edited full retry submission sends changed `RequestRerun` payload with edited-full-retry provenance and exact rerun remains mutation-free in `frontend/src/entrypoints/task-create.test.tsx` covering FR-005, FR-010, SCN-003, and SC-003.
- [ ] T013 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx` to confirm T009-T012 fail for missing or insufficient MM-644 behavior before implementation.
- [ ] T014 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py` to confirm T007-T008 fail for missing or insufficient MM-644 behavior before implementation.

### Integration Tests (write first)

- [ ] T015 [P] Add hermetic integration test for changed edited full retry creating a distinct new execution in `tests/integration/temporal/test_editable_full_retry_workflow.py` covering FR-005, FR-007, SCN-003, and DESIGN-REQ-003.
- [ ] T016 [P] Add hermetic integration test for edited full retry writing its own authoritative task input snapshot with edited content and source lineage in `tests/integration/temporal/test_editable_full_retry_workflow.py` covering FR-006, SCN-004, SC-002, and DESIGN-REQ-004.
- [ ] T017 [P] Add hermetic integration test proving failed source execution snapshot, artifact refs, checkpoint/progress refs, and terminal state remain unchanged after edited full retry in `tests/integration/temporal/test_editable_full_retry_workflow.py` covering FR-008, SCN-005, and DESIGN-REQ-005.
- [ ] T018 [P] Add hermetic integration test proving edited full retry strips `resumeSource`, `resumeCheckpointRef`, `preservedSteps`, `completedSteps`, task `resume`, and stale task `recovery` from new execution parameters in `tests/integration/temporal/test_editable_full_retry_workflow.py` covering FR-009, SCN-006, SC-003, and DESIGN-REQ-006.
- [ ] T019 Run `./tools/test_integration.sh` to confirm T015-T018 fail for missing or insufficient MM-644 behavior before implementation.

### Red-First Confirmation

- [ ] T020 Record the expected red-first failures from T013-T019 in `specs/343-editable-full-retry-workflow/tasks.md` or implementation notes before modifying production files, confirming failures map to FR-001 through FR-011.

### Conditional Fallback Implementation

- [ ] T021 If T007 or T009 fails, update action capability and disabled-reason handling in `api_service/api/routers/executions.py` and `frontend/src/entrypoints/task-detail.tsx` so Edit task is offered only for readable authoritative snapshots and unavailable states have operator-readable reasons for FR-001 and FR-011.
- [ ] T022 If T010 or T011 fails, update edit-for-rerun route hydration, mode copy, editable authoring state, and validation handling in `frontend/src/lib/temporalTaskEditing.ts` and `frontend/src/entrypoints/task-create.tsx` for FR-002, FR-003, FR-004, SCN-001, and SCN-002.
- [ ] T023 If T008 or T012 fails, update edited-full-retry provenance modeling in `moonmind/workflows/tasks/task_contract.py`, `moonmind/schemas/temporal_models.py`, `frontend/src/lib/temporalTaskEditing.ts`, and `frontend/src/entrypoints/task-create.tsx` so changed edited retry carries `edited_full_retry` with pinned source workflow/run IDs for FR-010.
- [ ] T024 If T015 or T018 fails, update full-rerun parameter normalization in `moonmind/workflows/temporal/service.py` so edited full retry starts from the beginning and strips prior Resume/progress metadata while preserving newly authored edited-full-retry provenance for FR-007 and FR-009.
- [ ] T025 If T016 fails, update snapshot persistence and source lineage handling in `api_service/api/routers/executions.py` so the new edited full retry execution gets its own authoritative task input snapshot reflecting edited input for FR-006.
- [ ] T026 If T017 fails, update fresh rerun creation and source record handling in `moonmind/workflows/temporal/service.py` and `api_service/api/routers/executions.py` so source execution evidence remains immutable for FR-005 and FR-008.
- [ ] T027 If frontend generated API types are affected by schema changes from T023, regenerate and update `frontend/src/generated/openapi.ts` for FR-010.

### Story Validation

- [ ] T028 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx` and verify UI coverage for FR-001 through FR-004, FR-010, FR-011, SCN-001, SCN-002, and SCN-007 passes.
- [ ] T029 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py` and verify API/contract coverage for FR-001, FR-009, FR-010, FR-011, SC-003, and SC-004 passes.
- [ ] T030 Run `./tools/test_integration.sh` and verify integration coverage for FR-005 through FR-009, SCN-003 through SCN-006, SC-002, SC-003, and DESIGN-REQ-003 through DESIGN-REQ-006 passes.
- [ ] T031 Confirm `specs/343-editable-full-retry-workflow/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/editable-full-retry.md`, `quickstart.md`, and `tasks.md` preserve MM-644 and the original Jira preset brief for FR-012 and SC-005.

**Checkpoint**: The single story is fully covered by unit and integration tests and can be validated independently.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding scope.

- [ ] T032 [P] Refactor duplicated recovery test fixtures in `frontend/src/entrypoints/task-create.test.tsx`, `frontend/src/entrypoints/task-detail.test.tsx`, and `tests/integration/temporal/test_editable_full_retry_workflow.py` without changing behavior.
- [ ] T033 [P] Review security and secret hygiene for new logs, errors, artifacts, and test fixtures in `api_service/api/routers/executions.py`, `moonmind/workflows/temporal/service.py`, and `frontend/src/entrypoints/task-create.tsx`.
- [ ] T034 Run `./tools/test_unit.sh` for full unit verification after focused tests pass.
- [ ] T035 Run quickstart validation from `specs/343-editable-full-retry-workflow/quickstart.md` and record any deviations in `specs/343-editable-full-retry-workflow/verification.md` if created by the verify step.
- [ ] T036 Run `/speckit.verify` after implementation and tests pass to validate MM-644 against the original Jira preset brief and source design mappings.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story tests.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on story validation and passing focused tests.

### Within The Story

- T007-T012 unit/UI tests are written before production implementation.
- T015-T018 integration tests are written before production implementation.
- T013, T014, T019, and T020 confirm red-first behavior before T021-T027 production changes.
- T021-T027 are conditional fallback implementation tasks for partial or implemented-unverified rows; skip a fallback task only when its verification tests pass without production changes and traceability remains documented.
- T028-T031 validate the completed story before polish.
- T036 final `/speckit.verify` runs only after implementation and tests pass.

### Parallel Opportunities

- T003-T006 can run in parallel after T001-T002.
- T007-T012 can run in parallel after foundational fixtures are ready because they touch different test surfaces.
- T015-T018 are in the same integration file and should be coordinated by one worker or serialized to avoid conflicts.
- T021-T027 must be coordinated because API schema, service normalization, and frontend payloads interact.
- T032 and T033 can run in parallel after story validation.

## Parallel Example: Story Test Authoring

```bash
# Safe parallel work after Phase 2:
Task: "Add API action capability tests in tests/unit/api/routers/test_executions.py"
Task: "Add Task Detail UI action tests in frontend/src/entrypoints/task-detail.test.tsx"
Task: "Add Create page edit-for-rerun tests in frontend/src/entrypoints/task-create.test.tsx"
```

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete setup and fixture tasks T001-T006.
2. Write all unit/UI tests T007-T012 and integration tests T015-T018 before production changes.
3. Run T013, T014, and T019 to confirm red-first failures for partial/missing behavior or to document verification-only passes.
4. Apply conditional fallback implementation tasks T021-T027 only for failing verification tests.
5. Run focused story validation T028-T031.
6. Complete polish and full verification T032-T036.

### Requirement Status Handling

- **Partial rows**: FR-001, FR-006, FR-010, FR-011, SCN-003, SCN-004, SCN-007, SC-002, SC-004, DESIGN-REQ-004 receive tests plus fallback implementation tasks.
- **Implemented-unverified rows**: FR-002, FR-003, FR-004, FR-005, FR-007, FR-008, FR-009, FR-012, SCN-001, SCN-002, SCN-005, SCN-006, SC-001, SC-003, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-006 receive verification tests and conditional fallback implementation.
- **Implemented-verified rows**: none.
- **Missing rows**: none.

## Notes

- This task list covers one story only: Editable Full Retry From Snapshot.
- Do not create `plan.md`, additional specs, implementation docs, PRs, Jira transitions, or downstream verification artifacts except where T035-T036 explicitly call for final validation after implementation.
- Preserve MM-644 in implementation notes, test names or comments where useful, verification output, commit text, and pull request metadata.
- Keep exact rerun and failed-step Resume behavior out of scope except where tests ensure edited full retry stays distinct and does not import Resume progress.
