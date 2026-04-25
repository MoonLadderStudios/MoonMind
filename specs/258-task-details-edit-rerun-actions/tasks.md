# Tasks: Task Details Edit and Rerun Actions

**Input**: `specs/258-task-details-edit-rerun-actions/spec.md`, `specs/258-task-details-edit-rerun-actions/plan.md`, `specs/258-task-details-edit-rerun-actions/research.md`, `specs/258-task-details-edit-rerun-actions/data-model.md`, `specs/258-task-details-edit-rerun-actions/contracts/task-details-actions.md`, `specs/258-task-details-edit-rerun-actions/quickstart.md`
**Feature**: `258-task-details-edit-rerun-actions`
**Story Count**: 1
**Unit Test Command**: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
**Frontend Unit Command**: `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
**Integration Test Command**: `./tools/test_integration.sh` only if a new compose-backed execution-detail fixture is added; otherwise run the frontend component integration coverage in the frontend unit command.
**Final Verify Command**: `/moonspec-verify specs/258-task-details-edit-rerun-actions`

## Source Traceability Summary

| ID | Status From Plan | Task Coverage |
| --- | --- | --- |
| SCN-001 | implemented_verified | T004, T005, T006, T012, T015 |
| SCN-002 | implemented_verified | T004, T005, T006, T012, T015 |
| SCN-003 | implemented_verified | T002, T003, T006, T011, T015 |
| SCN-004 | implemented_verified | T002, T003, T006, T011, T015 |
| REQ-001 | implemented_verified | T002, T003, T011, T014, T015 |
| REQ-002 | implemented_verified | T002, T003, T008, T011, T015 |
| REQ-003 | implemented_verified | T002, T003, T008, T011, T015 |
| REQ-004 | implemented_verified | T004, T005, T009, T012, T015 |
| REQ-005 | implemented_verified | T004, T005, T010, T012, T015 |
| REQ-006 | implemented_verified | T004, T005, T009, T012, T015 |
| REQ-007 | implemented_verified | T004, T005, T009, T012, T015 |

## Phase 1: Setup

- [X] T001 Verify the active feature directory contains one story and required design artifacts in `specs/258-task-details-edit-rerun-actions/spec.md`, `specs/258-task-details-edit-rerun-actions/plan.md`, `specs/258-task-details-edit-rerun-actions/research.md`, `specs/258-task-details-edit-rerun-actions/data-model.md`, `specs/258-task-details-edit-rerun-actions/contracts/task-details-actions.md`, and `specs/258-task-details-edit-rerun-actions/quickstart.md`

## Phase 2: Foundational

- [X] T002 [P] Add or refresh red-first API capability assertions for REQ-001, REQ-002, REQ-003, SCN-003, and SCN-004 in `tests/unit/api/routers/test_executions.py`
- [ ] T003 Confirm the API capability assertions fail before implementation by running `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` for `tests/unit/api/routers/test_executions.py`
- [X] T004 [P] Add or refresh red-first Task Details route/rendering assertions for REQ-004, REQ-005, REQ-006, REQ-007, SCN-001, and SCN-002 in `frontend/src/entrypoints/task-detail.test.tsx`
- [X] T005 [P] Add or refresh red-first create-page route mode assertions for REQ-005 in `frontend/src/entrypoints/task-create.test.tsx`
- [ ] T006 Confirm frontend red-first assertions fail before implementation by running `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx` for `frontend/src/entrypoints/task-detail.test.tsx` and `frontend/src/entrypoints/task-create.test.tsx`

## Phase 3: Story - Show Task Details Recovery Actions

**Story Summary**: Task Details shows **Edit task** and **Rerun** from explicit execution action capabilities for failed and other eligible task statuses.
**Independent Test**: Load Task Details with mocked execution detail records for failed, running, missing-snapshot, and unsupported workflow-type cases and verify visible links and hrefs match the capability contract.
**Traceability IDs**: SCN-001, SCN-002, SCN-003, SCN-004, REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-006, REQ-007

### Unit Test Plan

- API unit tests validate capability serialization and disabled reasons.
- Frontend unit tests validate route helpers, Task Details rendering, and edit-for-rerun mode parsing.

### Integration Test Plan

- Frontend component integration coverage validates Task Details behavior through rendered React entrypoints and mocked execution detail payloads.
- No new hermetic `integration_ci` test is required unless a compose-backed execution-detail fixture is introduced.

- [X] T007 Implement `can_edit_for_rerun` in `moonmind/schemas/temporal_models.py` for REQ-001
- [X] T008 Implement terminal-state `canEditForRerun` capability calculation and disabled reasons in `api_service/api/routers/executions.py` for REQ-001, REQ-002, REQ-003, SCN-003, and SCN-004
- [X] T009 Implement Task Details **Edit task** and **Rerun** additive rendering in `frontend/src/entrypoints/task-detail.tsx` for REQ-004, REQ-006, REQ-007, SCN-001, and SCN-002
- [X] T010 Implement edit-for-rerun route helper and `mode=edit` parsing in `frontend/src/lib/temporalTaskEditing.ts` and `frontend/src/entrypoints/task-create.tsx` for REQ-005
- [X] T011 Regenerate OpenAPI frontend types in `frontend/src/generated/openapi.ts` with `npm run api:types` for REQ-001
- [X] T012 Run story validation with `./tools/test_unit.sh tests/unit/api/routers/test_executions.py` and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
- [X] T013 Run type validation with `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`

## Final Phase: Polish And Verification

- [X] T014 Run `git diff --check` and review `frontend/src/generated/openapi.ts` for only the expected `canEditForRerun` contract addition
- [X] T015 Run `/moonspec-verify specs/258-task-details-edit-rerun-actions` and record the final verdict in `specs/258-task-details-edit-rerun-actions/verification.md`

## Implementation Evidence Notes

- Current verification passed for T012/T013/T014: `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`, `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`, `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`, `npm run api:types`, and `git diff --check`.
- T003 and T006 remain unchecked because fail-first confirmation cannot be honestly reproduced after the production implementation was already committed; rerunning the tests now confirms the expected passing behavior.

## Dependencies And Execution Order

1. T001 must complete before all other tasks.
2. T002 through T006 are red-first test tasks and must complete before implementation tasks.
3. T007 through T011 are implementation tasks and must complete before validation.
4. T012 and T013 validate the story.
5. T014 and T015 are final verification work.

## Parallel Examples

- T002 and T004 can run in parallel because they touch Python API tests and frontend Task Details tests.
- T005 can run in parallel with T002 and T004 because it only touches create-page route mode assertions.
- T007 and T009 can run in parallel after red-first confirmation because they touch backend schema and frontend rendering.

## Implementation Strategy

All plan rows are currently `implemented_verified`, so future re-execution should treat this task list as verification-preserving unless code drifts. If any red-first test task already passes before implementation in a future run, keep the test as regression coverage and skip the matching implementation task only after confirming the current code still satisfies the requirement evidence in `plan.md`.
