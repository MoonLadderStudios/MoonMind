# Tasks: Merge Automation Visibility

**Input**: Design documents from `specs/189-merge-automation-visibility/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration/UI boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement production code.

**Test Commands**:

- Unit/UI focused tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py --ui-args frontend/src/entrypoints/task-detail.test.tsx`
- Full unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

- [X] T001 Confirm MM-354 input and source design traceability in `spec.md` (Input) and `specs/189-merge-automation-visibility/spec.md`.
- [X] T002 Confirm existing workflow and UI files that own merge automation visibility: `moonmind/workflows/temporal/workflows/merge_automation.py`, `moonmind/workflows/temporal/workflows/run.py`, and `frontend/src/entrypoints/task-detail.tsx`.

## Phase 2: Foundational

- [X] T003 Confirm existing artifact activity boundary is available for workflow JSON reports in `moonmind/workflows/temporal/activity_catalog.py` and `moonmind/schemas/temporal_activity_models.py` (FR-005, DESIGN-REQ-027).

## Phase 3: Story - Inspect Merge Automation State

**Summary**: As an operator watching Mission Control, I want merge automation state, settings, workflow links, and artifacts exposed on the parent task so that I can diagnose waiting, merged, failed, or canceled PR automation.

**Independent Test**: Focused workflow and UI tests validate summary projection, artifact refs, and Mission Control rendering from one task detail payload.

**Traceability**: FR-001 through FR-010, SC-001 through SC-005, DESIGN-REQ-006, DESIGN-REQ-018, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-029.

### Unit Tests

- [X] T004 Add failing workflow tests in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` proving summary, gate snapshot, and resolver attempt artifact refs are written and returned (FR-005, SC-002, DESIGN-REQ-027).
- [X] T005 Add failing parent summary tests in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` proving `mergeAutomation` run summary projection contains enabled, status, PR, child workflow, resolver ids, cycles, blockers, and artifact refs (FR-006, FR-007, SC-003).

### Integration Tests

- [X] T006 Add failing UI tests in `frontend/src/entrypoints/task-detail.test.tsx` proving Mission Control renders merge automation status, blockers, PR link, head SHA, cycles, child workflow links, and artifact refs without dependency/schedule surfaces (FR-002, FR-003, FR-004, FR-008, SC-001, SC-004).
- [X] T007 Run the focused test command and confirm the new tests fail for missing visibility/artifact behavior.

### Implementation

- [X] T008 Extend `moonmind/schemas/temporal_models.py` and `moonmind/workflows/temporal/workflows/run.py` only as needed to carry compact merge automation artifact/visibility refs without large payloads (FR-006, FR-010).
- [X] T009 Implement durable merge automation artifact writes and returned artifact refs in `moonmind/workflows/temporal/workflows/merge_automation.py` (FR-005, DESIGN-REQ-027).
- [X] T010 Implement parent `mergeAutomation` run summary projection in `moonmind/workflows/temporal/workflows/run.py` (FR-006, FR-007, DESIGN-REQ-018).
- [X] T011 Implement Mission Control merge automation rendering in `frontend/src/entrypoints/task-detail.tsx` (FR-002, FR-003, FR-004, FR-008, DESIGN-REQ-026).
- [X] T012 Run focused workflow/UI tests and fix failures.

## Phase 4: Polish And Verification

- [X] T013 Run full unit suite with `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`.
- [X] T014 Run `/moonspec-verify` and record the result in `specs/189-merge-automation-visibility/verification.md`.

## Dependencies & Execution Order

- T004-T006 must be written before implementation.
- T009-T011 depend on red-first test coverage.
- T013-T014 run after focused tests pass.

## Notes

- This task list covers exactly one story: MM-354 / STORY-005.
