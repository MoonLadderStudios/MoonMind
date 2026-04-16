# Tasks: Parent-Owned Merge Automation

**Input**: Design documents from `/specs/186-parent-owned-merge-automation/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-350 story: parent-owned merge automation must be started and awaited by the original `MoonMind.Run`.

**Source Traceability**: Covers FR-001 through FR-014, acceptance scenarios 1-6, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-009, DESIGN-REQ-028, and DESIGN-REQ-029 from [spec.md](./spec.md).

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Phase 1: Setup

**Purpose**: Confirm existing workflow surfaces and create focused test locations.

- [X] T001 Review existing merge-gate parent startup and publish context helpers in `moonmind/workflows/temporal/workflows/run.py` for FR-001 through FR-003 and DESIGN-REQ-001 through DESIGN-REQ-003
- [X] T002 Review existing merge-gate workflow outcome shape and resolver behavior in `moonmind/workflows/temporal/workflows/merge_gate.py` for reusable logic only, while preserving the source-required `MoonMind.MergeAutomation` workflow type for FR-004, FR-005, and FR-012
- [X] T003 [P] Review existing parent merge-gate tests in `tests/unit/workflows/temporal/test_run_merge_gate_start.py` for reusable fixtures covering FR-002, FR-008, and DESIGN-REQ-007
- [X] T004 [P] Review existing workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_integration.py` and `tests/unit/workflows/temporal/workflows/test_merge_gate_temporal.py` for child workflow stubbing patterns covering FR-014

## Phase 2: Foundational

**Purpose**: Establish compact contracts and fixtures before story implementation.

- [X] T005 Add failing unit tests for parent-owned publish context validation and missing required PR fields in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` covering FR-010, SC-001, DESIGN-REQ-007
- [X] T006 Add failing unit tests for deterministic child workflow id and duplicate child prevention in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` covering FR-002, FR-008, SC-005, DESIGN-REQ-001
- [X] T007 Add failing unit tests for child outcome classification in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` covering FR-004, FR-005, SC-003, SC-004, DESIGN-REQ-003
- [X] T008 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` to confirm T005-T007 fail for the expected missing parent-owned semantics

**Checkpoint**: Foundational tests prove the missing compact contract, duplicate-prevention, and child-result behavior.

## Phase 3: Story - Await Parent-Owned Merge Automation

**Summary**: As a MoonMind operator using pull-request publishing, I want the original task run to own and await merge automation as child workflow work so downstream dependencies complete only after publish and automated merge resolution finish.

**Independent Test**: Run or simulate a PR-publishing `MoonMind.Run` with merge automation enabled and a stub publish result. Verify the parent persists publish context, starts one parent-owned child workflow, remains `awaiting_external` while the child is active, completes only after successful child outcome, and remains the dependency target for downstream tasks.

**Traceability**: FR-001 through FR-014; acceptance scenarios 1-6; SC-001 through SC-006; DESIGN-REQ-001 through DESIGN-REQ-009, DESIGN-REQ-028, DESIGN-REQ-029.

**Test Plan**:

- Unit: publish context field validation, effective config parsing, deterministic child id, duplicate prevention, child outcome classification, disabled automation guardrail.
- Integration/workflow-boundary: real parent workflow invocation shape, child start and await, parent `awaiting_external`, success outcome, non-success outcome, dependency target preservation.

### Unit Tests (write first)

- [X] T009 [P] Add failing unit test for effective merge automation config preserving top-level `publishMode` in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` covering FR-001, FR-009, FR-013, DESIGN-REQ-006
- [X] T010 [P] Add failing unit test for parent compact metadata including child workflow id and waiting state in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` covering FR-003, FR-011, DESIGN-REQ-008, DESIGN-REQ-029
- [X] T011 [P] Add failing unit test for resolver child template retaining publish mode `none` and parent-owned workflow type `MoonMind.MergeAutomation` in `tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` covering FR-012, DESIGN-REQ-009
- [X] T012 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py` to confirm T009-T011 fail for the expected missing behavior

### Workflow-Boundary Tests (write first)

- [X] T013 [P] Add failing workflow-boundary test for enabled merge automation starting and awaiting one child in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering acceptance scenario 1, FR-002, FR-003, SC-001, SC-002
- [X] T014 [P] Add failing workflow-boundary test for successful child outcome allowing parent success in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering acceptance scenario 3, FR-004, FR-006, SC-003
- [X] T015 [P] Add failing workflow-boundary test for blocked or failed child outcome preventing parent success in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering acceptance scenario 4, FR-005, SC-004
- [ ] T016 [P] Add failing workflow-boundary test for duplicate replay or retry preserving one child workflow id in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering acceptance scenario 5, FR-008, SC-005
- [X] T017 [P] Add failing workflow-boundary test for disabled merge automation preserving existing PR publish behavior in `tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` covering acceptance scenario 6, FR-013
- [X] T018 Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` to confirm T013-T017 fail for the expected missing parent-await behavior

### Implementation

- [X] T019 Add or update compact parent-owned merge automation helper models in `moonmind/workflows/temporal/workflows/run.py` or the established schema home covering FR-001, FR-009, FR-010, DESIGN-REQ-006, DESIGN-REQ-007
- [X] T020 Update publish context construction in `moonmind/workflows/temporal/workflows/run.py` to persist required PR identity fields and compact artifact refs before child start covering FR-010, DESIGN-REQ-007
- [X] T021 Replace detached start-only merge automation behavior with parent-owned child start and await behavior in `moonmind/workflows/temporal/workflows/run.py` covering FR-002, FR-003, FR-004, FR-005, DESIGN-REQ-001 through DESIGN-REQ-003
- [X] T022 Add deterministic child workflow identity recording and duplicate prevention in `moonmind/workflows/temporal/workflows/run.py` covering FR-008, SC-005
- [X] T023 Update parent waiting metadata, finish summary, and operator-visible compact state in `moonmind/workflows/temporal/workflows/run.py` covering FR-011, DESIGN-REQ-008, DESIGN-REQ-029
- [X] T024 Add or adapt the source-required `MoonMind.MergeAutomation` child workflow in `moonmind/workflows/temporal/workflows/merge_automation.py` using reusable readiness/resolver logic from `moonmind/workflows/temporal/workflows/merge_gate.py` where appropriate covering FR-004, FR-005, FR-012, DESIGN-REQ-009
- [X] T025 Register `MoonMind.MergeAutomation` without a hidden compatibility alias in `moonmind/workflows/temporal/worker_entrypoint.py` and `moonmind/workflows/temporal/workers.py` covering FR-002, FR-014, DESIGN-REQ-001
- [X] T026 Ensure disabled merge automation and non-PR publish modes retain current behavior in `moonmind/workflows/temporal/workflows/run.py` covering FR-013
- [X] T027 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py` and fix failures until the story tests pass

**Checkpoint**: The parent-owned merge automation story is functionally implemented and independently covered.

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T028 [P] Update quick reference or operator-facing documentation only if implementation changes visible merge automation semantics in `docs/Tasks/TaskPublishing.md` covering FR-011 and DESIGN-REQ-029
- [X] T029 [P] Add any missing regression coverage for existing detached merge-gate behavior conflicts in `tests/unit/workflows/temporal/test_run_merge_gate_start.py` if code paths remain shared
- [X] T030 Run `./tools/test_unit.sh` for full unit verification
- [X] T031 Run `./tools/test_integration.sh` for hermetic integration verification, or document the exact environment blocker if Docker is unavailable
- [X] T032 Run `/speckit.verify` for `specs/186-parent-owned-merge-automation/spec.md` and record the result in `specs/186-parent-owned-merge-automation/verification.md`

## Dependencies And Execution Order

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 context and blocks implementation.
- Phase 3 tests must be written and confirmed failing before T019-T025 implementation tasks.
- T019-T026 may be implemented only after T008, T012, and T018 confirm red-first failures.
- Phase 4 depends on focused story tests passing.

## Parallel Opportunities

- T003 and T004 can run in parallel.
- T009 through T011 can be authored in parallel because they cover separate helper behaviors in the same new test file but must be reconciled before T012.
- T013 through T017 can be authored in parallel if coordinated in the same workflow-boundary test file.
- T028 and T029 can run in parallel after the implementation is complete.

## Implementation Strategy

1. Confirm existing MM-341 merge-gate behavior and locate reusable readiness/resolver code.
2. Write focused unit tests for compact contracts and child outcome classification.
3. Write workflow-boundary tests for parent-owned start, await, success, failure, duplicate, and disabled paths.
4. Confirm all new tests fail for missing MM-350 semantics.
5. Update parent workflow behavior and compact state handling.
6. Reuse or adapt existing merge-gate child behavior without adding a top-level dependency target.
7. Run focused tests, full unit tests, integration tests where available, and final `/speckit.verify`.
