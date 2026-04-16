# Tasks: Remove Legacy Merge Automation Workflow

**Input**: Design documents from `specs/193-remove-legacy-merge-automation/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Source Traceability**: MM-364 Jira preset brief is preserved in `spec.md` and `docs/tmp/jira-orchestration-inputs/MM-364-moonspec-orchestration-input.md`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py`
- Integration tests: workflow-boundary coverage in the same focused pytest command; compose-backed integration is not required because no external service contract changes
- Final verification: `/speckit.verify`

## Phase 1: Setup

- [X] T001 Verify active feature artifacts and checklist for MM-364 in `specs/193-remove-legacy-merge-automation/spec.md` and `specs/193-remove-legacy-merge-automation/checklists/requirements.md`

## Phase 2: Foundational

- [X] T002 Inspect active workflow registration and helper imports in `moonmind/workflows/temporal/worker_entrypoint.py`, `moonmind/workflows/temporal/workflows/merge_automation.py`, and `moonmind/workflows/temporal/workflows/merge_gate.py`

## Phase 3: Story - Unambiguous Merge Automation Runtime Path

**Summary**: Remove the duplicate legacy merge automation workflow and dead resolver-run activity path while preserving active child `MoonMind.Run` resolver behavior.

**Independent Test**: Grep proves one active workflow class and no legacy activity path; focused tests prove helper behavior and active workflow resolver launch still work.

**Traceability**: FR-001 through FR-007, SC-001 through SC-005, MM-364

**Test Plan**:

- Unit: helper behavior, workflow class uniqueness, activity catalog/runtime absence
- Integration: active workflow boundary launches child `MoonMind.Run` with `pr-resolver` and `publishMode=none`

### Unit Tests (write first)

- [X] T003 Add failing unit assertions that `merge_gate.py` no longer exposes `MoonMindMergeAutomationWorkflow` and live activity bindings no longer include `merge_automation.create_resolver_run` in `tests/unit/workflows/temporal/test_merge_gate_workflow.py` covering FR-001, FR-002, FR-004, SC-001, and SC-002
- [X] T004 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py` and confirm T003 fails for the expected legacy workflow/activity presence

### Workflow-Boundary Tests (write first)

- [X] T005 Update active workflow tests in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` to assert ready-state resolver launch uses child `MoonMind.Run` with `pr-resolver` and `publishMode=none`, and never calls `merge_automation.create_resolver_run`, covering FR-003 and scenario 3
- [X] T006 Run focused workflow tests and confirm T005 remains meaningful while T003 fails before production cleanup

### Implementation

- [X] T007 Remove the legacy `MoonMindMergeAutomationWorkflow` class and its workflow-only imports/constants from `moonmind/workflows/temporal/workflows/merge_gate.py` while preserving helpers used by `merge_automation.py` covering FR-001 and FR-002
- [X] T008 Remove `merge_automation.create_resolver_run` from `moonmind/workflows/temporal/activity_catalog.py` covering FR-004
- [X] T009 Remove `merge_automation.create_resolver_run` dispatch and handler code from `moonmind/workflows/temporal/activity_runtime.py` covering FR-004
- [X] T010 Remove or replace legacy workflow tests in `tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py` so tests validate the active workflow path instead of the deleted class covering FR-005
- [X] T011 Review `docs/Tasks/PrMergeAutomation.md` and update it only if it implies both workflow paths are active covering FR-006
- [X] T012 Run focused tests and fix failures: `./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py`
- [X] T013 Capture grep evidence that only one `MoonMindMergeAutomationWorkflow` class exists and no live `merge_automation.create_resolver_run` references remain covering SC-001 and SC-002

## Phase 4: Polish And Verification

- [X] T014 Run `./tools/test_unit.sh` for final unit verification covering SC-004
- [X] T015 Preserve MM-364 evidence in `specs/193-remove-legacy-merge-automation/verification.md` covering FR-007 and SC-005
- [X] T016 Run `/speckit.verify` by auditing `spec.md`, `plan.md`, `tasks.md`, code, tests, and grep evidence; record the verdict in `specs/193-remove-legacy-merge-automation/verification.md`

## Dependencies & Execution Order

- Phase 1 and Phase 2 must complete before story test edits.
- T003-T006 must complete before T007-T011 production cleanup.
- T012-T013 must complete before final verification.
- T014-T016 complete the story.

## Implementation Strategy

Use focused TDD: first make tests assert the desired cleanup state, confirm they fail on current legacy code, then remove the legacy workflow/activity path and run the focused tests until they pass. Finish with full unit verification and a MoonSpec verification record.
