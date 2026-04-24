# Tasks: Create Page Merge Automation

**Input**: Design documents from `specs/193-create-page-merge-automation/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and UI/request-shape integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code.

**Test Commands**:

- Focused UI tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx`
- Backend merge automation parsing tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_run_merge_gate_start.py`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Traceability Inventory

- FR-001, FR-002, SC-001, SC-002: merge automation availability follows publish mode.
- FR-003, FR-007, SC-005: resolver-style skills cannot expose or submit merge automation.
- FR-004, FR-005, SC-003, SC-004: submitted payload includes `mergeAutomation.enabled=true` and preserves PR publish contracts.
- FR-006: merge automation remains PR-publishing task configuration.
- FR-008, SC-006: UI and docs explain `pr-resolver` relationship without direct auto-merge language.
- FR-009: Jira Orchestrate preset behavior remains explicit and unchanged.
- FR-010, SC-007: MM-365 remains visible in artifacts and verification.

## Phase 1: Setup

- [X] T001 Confirm MM-365 source input and single-story traceability in `spec.md` (Input) and `specs/193-create-page-merge-automation/spec.md`.
- [X] T002 Confirm current Create page publish and resolver behavior in `frontend/src/entrypoints/task-create.tsx` and `frontend/src/entrypoints/task-create.test.tsx`.

## Phase 2: Foundational

- [X] T003 Confirm existing backend merge automation parsing coverage in `tests/unit/workflows/temporal/test_run_merge_gate_start.py` accepts the planned top-level `mergeAutomation.enabled=true` request shape.

## Phase 3: Story - Configure Merge Automation During PR Publishing

**Summary**: As a MoonMind operator, I want the Create page to offer merge automation only for PR-publishing tasks so that a submitted implementation run can publish a pull request and route readiness and merge handling through MoonMind's resolver workflow.

**Independent Test**: Create task drafts across publish modes and resolver skill choices. The story passes when the merge automation option is visible and submitted only for ordinary PR-publishing tasks, remains absent for `branch` and `none`, is unavailable for resolver-style tasks, and the submitted payload keeps the existing PR publish fields alongside `mergeAutomation.enabled=true` when selected.

**Traceability**: FR-001 through FR-010, SC-001 through SC-007, MM-365.

### Unit Tests

- [X] T004 Add failing helper/state tests in `frontend/src/entrypoints/task-create.test.tsx` proving merge automation visibility follows `Publish Mode` `pr`, `branch`, and `none` (FR-001, FR-002, SC-001, SC-002).
- [X] T005 Add failing helper/state tests in `frontend/src/entrypoints/task-create.test.tsx` proving resolver-style primary skills hide or disable merge automation and clear stale enabled state (FR-003, FR-007, SC-005).

### Integration Tests

- [X] T006 Add failing UI request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` proving selected merge automation submits `mergeAutomation.enabled=true`, `publishMode=pr`, and `task.publish.mode=pr` (FR-004, FR-005, SC-003, SC-004).
- [X] T007 Add failing UI request-shape tests in `frontend/src/entrypoints/task-create.test.tsx` proving disabled/unavailable states omit merge automation fields, including resolver-style tasks and non-PR publish modes (FR-002, FR-003, FR-007, SC-002, SC-005).
- [X] T008 Add failing UI copy test in `frontend/src/entrypoints/task-create.test.tsx` proving the control text names `pr-resolver` and does not describe direct auto-merge (FR-008, SC-006).
- [X] T009 Run focused UI tests and confirm the new tests fail for missing merge automation Create page behavior.

### Implementation

- [X] T010 Implement local merge automation enabled state, availability derivation, and stale-state clearing in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-002, FR-003, FR-007).
- [X] T011 Implement the Create page merge automation control and operator copy in `frontend/src/entrypoints/task-create.tsx` (FR-001, FR-008, SC-006).
- [X] T012 Implement request payload inclusion and omission in `frontend/src/entrypoints/task-create.tsx` so available selected PR tasks submit `mergeAutomation.enabled=true` while unavailable states omit it (FR-004, FR-005, FR-006, FR-007).
- [X] T013 Update `docs/UI/CreatePage.md` to document visibility, payload behavior, resolver relationship, and unchanged Jira Orchestrate behavior (FR-008, FR-009, SC-006).
- [X] T014 Run focused UI tests and backend merge automation parsing tests, then fix failures in `frontend/src/entrypoints/task-create.tsx`, `frontend/src/entrypoints/task-create.test.tsx`, or docs only as needed.

## Phase 4: Polish And Verification

- [X] T015 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T016 Run `/moonspec-verify` and record the result in `specs/193-create-page-merge-automation/verification.md` (FR-010, SC-007).

## Dependencies & Execution Order

- T001-T003 must complete before story tests.
- T004-T008 must be written before T010-T013.
- T009 confirms red-first behavior before implementation.
- T010-T012 must complete before T014.
- T015-T016 run after focused tests pass.

## Parallel Opportunities

- T004 and T005 may be drafted together in the same test file but must be committed as coherent red-first coverage.
- T013 can be drafted while T010-T012 are implemented because it touches `docs/UI/CreatePage.md`.

## Notes

- This task list covers exactly one story: MM-365.
