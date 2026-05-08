# Tasks: Normalize Task-Shaped Submissions

**Input**: Design documents from `specs/320-normalize-task-shaped-submissions/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/task-shaped-submission-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around one story: Preserve Authored Task Submission Shape.

**Source Traceability**: MM-627, FR-001 through FR-012, acceptance scenarios 1 through 5, edge cases, SC-001 through SC-006, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-025.

**Requirement Status Summary**: `partial` rows require code-and-test work for FR-003 through FR-010, SC-001 through SC-005, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, and DESIGN-REQ-025. `implemented_unverified` rows require verification-first tests plus fallback implementation for FR-001 and DESIGN-REQ-001. `implemented_verified` rows require final traceability validation for FR-002, FR-011, FR-012, and SC-006.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel when tasks touch different files and do not depend on incomplete work.
- Each task includes exact file paths and traceability IDs.
- This task list covers exactly one independently testable story.

## Phase 1: Setup

**Purpose**: Confirm the active feature artifacts and test entry points before story work.

- [ ] T001 Confirm `.specify/feature.json` points to `specs/320-normalize-task-shaped-submissions` and that `specs/320-normalize-task-shaped-submissions/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-shaped-submission-contract.md`, and `quickstart.md` exist for MM-627.
- [ ] T002 Review existing targeted frontend test helpers in `frontend/src/entrypoints/task-create.test.tsx` for attachment upload, step reorder, Jira import provenance, applied templates, and branch payload assertions (FR-001 through FR-012).
- [ ] T003 Review existing backend task-shaped request fixtures in `tests/unit/api/routers/test_executions.py` for attachment policy, dependency normalization, runtime validation, and create execution payload assertions (FR-001 through FR-011).

---

## Phase 2: Foundational

**Purpose**: Identify reusable assertion helpers and fixture boundaries before adding red-first tests.

**CRITICAL**: No production implementation work starts until Phase 3 tests and red-first confirmation tasks are complete.

- [ ] T004 Add or extend frontend assertion helpers in `frontend/src/entrypoints/task-create.test.tsx` for canonical task payload checks covering MM-627, FR-001, FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, and SC-005.
- [ ] T005 Add or extend backend helper fixtures in `tests/unit/api/routers/test_executions.py` for canonical task-shaped submissions with objective attachments, step attachments, runtime, publish mode, dependencies, Jira provenance, branch intent, authored presets, and applied templates (FR-001, FR-003 through FR-010, DESIGN-REQ-008, DESIGN-REQ-011).
- [ ] T006 [P] Add integration test fixture helpers in `tests/integration/temporal/test_task_shaped_submission_normalization.py` for submitting task-shaped payloads and inspecting execution-visible normalized task parameters (acceptance scenarios 1 through 5, SC-001 through SC-005).

**Checkpoint**: Shared test helpers are available and story test authoring can begin.

---

## Phase 3: Story - Preserve Authored Task Submission Shape

**Summary**: As a Mission Control user, I want submitted tasks to preserve objective text, steps, repository/runtime/publish choices, dependencies, Jira provenance, and objective- or step-scoped attachments so execution receives exactly the task I authored.

**Independent Test**: Submit create, edit, and rerun task drafts that include objective text, ordered steps, objective-scoped attachments, step-scoped attachments, repository/runtime/publish choices, dependencies, Jira provenance, branch intent, and preset metadata; verify accepted submissions preserve every authored binding and invalid or ambiguous submissions fail before execution receives altered task data.

**Traceability**: MM-627, FR-001 through FR-012, acceptance scenarios 1 through 5, edge cases, SC-001 through SC-006, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-025.

**Unit Test Plan**: Cover frontend payload shaping and backend normalization/validation for attachment targets, branch semantics, dependencies, runtime/publish values, Jira provenance, preset provenance, binary-as-ref behavior, and explicit failure modes.

**Integration Test Plan**: Cover the execution creation boundary and artifact-backed input snapshot behavior so the normalized task payload received by execution preserves create/edit/rerun semantics and rejects invalid target or branch shapes.

### Unit Tests (write first)

- [ ] T007 [P] Add failing frontend unit tests in `frontend/src/entrypoints/task-create.test.tsx` proving create submissions preserve objective text, step order, objective attachments, step attachments, runtime, publish mode, dependencies, Jira provenance, `task.git.branch`, and applied template metadata without `targetBranch` (FR-001, FR-003, FR-005, FR-006, FR-007, FR-008, SC-001, SC-002, SC-003, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-008, DESIGN-REQ-011).
- [ ] T008 [P] Add failing frontend unit tests in `frontend/src/entrypoints/task-create.test.tsx` proving edit and rerun submissions preserve snapshot-backed objective and step attachment targets while text changes, step reordering, and preset overrides do not silently retarget attachments (FR-003, FR-009, SC-001, SC-005, DESIGN-REQ-003, DESIGN-REQ-025).
- [ ] T009 [P] Add failing frontend unit tests in `frontend/src/entrypoints/task-create.test.tsx` proving invalid or ambiguous repository, runtime, publish, dependency, attachment policy, and target-binding inputs block submission before uploads or `/api/executions` calls (FR-004, FR-010, SC-004, DESIGN-REQ-006, DESIGN-REQ-025).
- [ ] T010 [P] Add failing backend unit tests in `tests/unit/api/routers/test_executions.py` proving canonical task-shaped create requests preserve objective attachments, step attachments, step IDs/order, runtime, publish, dependencies, Jira provenance, authored presets, applied templates, and branch intent (FR-001, FR-003, FR-005, FR-007, FR-008, SC-001, SC-002, DESIGN-REQ-001, DESIGN-REQ-008, DESIGN-REQ-011).
- [ ] T011 [P] Add failing backend unit tests in `tests/unit/api/routers/test_executions.py` proving new task-shaped normalization rejects or removes legacy `targetBranch` and top-level branch aliases from execution-visible task output while preserving `task.git.branch` (FR-006, SC-003, DESIGN-REQ-011, DESIGN-REQ-025).
- [ ] T012 [P] Add failing backend unit tests in `tests/unit/api/routers/test_executions.py` proving invalid repository, runtime, publish, dependency, attachment policy, missing target, unknown target, conflicting attachment declaration, and ambiguous target-binding inputs fail explicitly (FR-004, FR-010, SC-004, DESIGN-REQ-006, DESIGN-REQ-025).
- [ ] T013 [P] Confirm existing binary-ref unit evidence in `frontend/src/entrypoints/task-create.test.tsx` and `tests/unit/api/routers/test_executions.py` still proves binary inputs remain structured attachment refs and are not embedded in task instruction text after normalization (FR-011, DESIGN-REQ-025).

### Integration Tests (write first)

- [ ] T014 [P] Add failing hermetic integration tests in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving execution creation receives canonical normalized task data for a valid create submission with objective and step attachments, runtime, publish, dependencies, Jira provenance, branch intent, and preset metadata (acceptance scenario 1, SC-001, SC-002, DESIGN-REQ-008, DESIGN-REQ-011).
- [ ] T015 [P] Add failing hermetic integration tests in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving edit and rerun flows preserve artifact-backed original task input snapshot attachment targets and reject silent retargeting after text or order changes (acceptance scenarios 2 and 3, FR-003, FR-009, SC-001, SC-005, DESIGN-REQ-003, DESIGN-REQ-025).
- [ ] T016 [P] Add failing hermetic integration tests in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving invalid repository, runtime, publish, dependency, attachment policy, and target-binding submissions fail before execution receives normalized task data (acceptance scenario 5, FR-004, FR-010, SC-004, DESIGN-REQ-006).
- [ ] T017 [P] Add failing hermetic integration tests in `tests/integration/temporal/test_task_shaped_submission_normalization.py` proving new task-shaped submissions expose `task.git.branch` and no `targetBranch` in execution-visible task parameters or task input snapshots (acceptance scenario 4, FR-006, SC-003, DESIGN-REQ-011, DESIGN-REQ-025).

### Red-First Confirmation

- [ ] T018 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and confirm T007-T009 fail for the intended MM-627 reasons before editing `frontend/src/entrypoints/task-create.tsx`.
- [ ] T019 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and confirm T010-T012 fail for the intended MM-627 reasons before editing `api_service/api/routers/executions.py`; record T013 as existing verified evidence.
- [ ] T020 Run the focused integration target through `./tools/test_integration.sh` and confirm T014-T017 fail for the intended MM-627 reasons before editing execution-boundary production code in `api_service/api/routers/executions.py`.

### Conditional Fallback for Implemented-Unverified Rows

- [ ] T021 If T007, T010, T014, or T018-T020 show task-shaped create/edit/rerun intent is not already preserved, update `frontend/src/entrypoints/task-create.tsx` and `api_service/api/routers/executions.py` so FR-001 and DESIGN-REQ-001 pass without changing the one-story scope.

### Implementation

- [ ] T022 Update frontend submission shaping in `frontend/src/entrypoints/task-create.tsx` so create, edit, and rerun task payloads preserve objective/step attachments, Jira provenance, preset metadata, dependencies, runtime, publish mode, step identity/order, and canonical `git.branch` without emitting `targetBranch` (FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-005).
- [ ] T023 Update backend task normalization in `api_service/api/routers/executions.py` to preserve `authoredPresets`, `appliedStepTemplates`, step source/provenance, Jira provenance, dependencies, runtime, publish mode, objective attachments, step attachments, and canonical branch intent in `initial_parameters["task"]` (FR-003, FR-005, FR-007, FR-008, DESIGN-REQ-008, DESIGN-REQ-011).
- [ ] T024 Update backend branch normalization in `api_service/api/routers/executions.py` to remove superseded `targetBranch` output from new task-shaped submissions and fail explicitly for unsupported branch alias shapes when canonical `task.git.branch` is required (FR-006, SC-003, DESIGN-REQ-011, DESIGN-REQ-025).
- [ ] T025 Update backend validation in `api_service/api/routers/executions.py` to fail explicitly for invalid repository, runtime, publish, dependency, attachment policy, missing target, unknown target, conflicting attachment declaration, and ambiguous target-binding input (FR-004, FR-010, SC-004, DESIGN-REQ-006, DESIGN-REQ-025).
- [ ] T026 Update backend attachment target normalization in `api_service/api/routers/executions.py` so step reordering, text changes, preset aliases, and migration-era input cannot silently retarget objective or step attachments (FR-003, FR-009, SC-001, SC-005, DESIGN-REQ-003, DESIGN-REQ-025).
- [ ] T027 Update execution-visible schema handling in `moonmind/schemas/temporal_models.py` only if T010-T017 reveal normalized task fields are dropped or hidden from task detail, edit, rerun, or verification surfaces (FR-003, FR-005, FR-007, FR-008, DESIGN-REQ-008, DESIGN-REQ-011).
- [ ] T028 Update or add integration wiring in `api_service/api/routers/executions.py` so artifact-backed original task input snapshots preserve objective and step attachment refs, preset provenance, Jira provenance, and canonical branch semantics for create, edit, and rerun paths (FR-003, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-005).

### Story Validation

- [ ] T029 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx` and verify frontend MM-627 unit coverage passes for FR-001 through FR-012.
- [ ] T030 Run `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and verify backend MM-627 unit coverage passes for FR-001 through FR-012.
- [ ] T031 Run `./tools/test_integration.sh` and verify hermetic integration coverage passes for acceptance scenarios 1 through 5, SC-001 through SC-005, and DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-025.
- [ ] T032 Confirm `MM-627`, the original Jira preset brief, FR-001 through FR-012, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, and DESIGN-REQ-025 remain preserved in `specs/320-normalize-task-shaped-submissions/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/task-shaped-submission-contract.md`, `quickstart.md`, and `tasks.md`.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T033 [P] Update `specs/320-normalize-task-shaped-submissions/quickstart.md` if implementation changes require adjusted validation commands or evidence paths (FR-012, SC-006).
- [ ] T034 [P] Review `docs/Tasks/TaskArchitecture.md` only for source alignment drift and leave canonical docs unchanged unless implementation exposes a desired-state mismatch (DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-025).
- [ ] T035 Run full unit verification with `./tools/test_unit.sh` and record pass/fail evidence for MM-627 in final verification notes.
- [ ] T036 Run `/moonspec-verify` for `specs/320-normalize-task-shaped-submissions/` after implementation and tests pass, covering MM-627, FR-001 through FR-012, acceptance scenarios 1 through 5, SC-001 through SC-006, and DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-025.

---

## Dependencies And Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1.
- Phase 3 depends on Phase 2.
- Phase 4 depends on story implementation and validation passing.

### Within The Story

- T007-T017 must be written before T018-T020.
- T018-T020 must confirm red-first behavior before T021-T028.
- T021 is conditional on implemented-unverified verification failure.
- T022-T028 are production implementation tasks and start only after red-first confirmation.
- T029-T032 validate the story after implementation.
- T035 and T036 run only after story validation passes.

### Parallel Opportunities

- T002 and T003 can run after T001 in parallel.
- T006 can run in parallel with T004-T005 because it creates integration fixture scaffolding in a different file.
- T007-T017 can be authored in parallel where they touch different test files.
- T033 and T034 can run in parallel after story validation.

## Parallel Example

```bash
# After Phase 2, author frontend, backend, and integration tests in parallel:
Task: "T007 frontend canonical create payload tests in frontend/src/entrypoints/task-create.test.tsx"
Task: "T010 backend canonical task payload tests in tests/unit/api/routers/test_executions.py"
Task: "T014 integration canonical execution payload tests in tests/integration/temporal/test_task_shaped_submission_normalization.py"
```

## Implementation Strategy

1. Confirm active MM-627 artifacts and helper locations.
2. Add red-first unit tests in frontend and backend test files.
3. Add red-first integration tests at the execution boundary.
4. Run focused unit and integration commands and confirm expected failures.
5. Apply conditional fallback work for implemented-unverified rows only if tests expose gaps.
6. Implement partial rows in frontend submission shaping and backend task normalization.
7. Rerun focused unit tests and hermetic integration tests.
8. Preserve traceability across MoonSpec artifacts.
9. Run full unit verification and final `/moonspec-verify`.

## Notes

- Do not create more than one story phase.
- Do not add tasks for future stories or broad Task Architecture refactors.
- Keep binary inputs as structured attachment refs.
- Preserve MM-627 in all implementation notes, verification output, commit text, and pull request metadata.
