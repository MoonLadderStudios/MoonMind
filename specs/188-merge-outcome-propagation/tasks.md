# Tasks: Merge Outcome Propagation

**Input**: Design documents from `/specs/188-merge-outcome-propagation/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style workflow-boundary tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-353 user story.

**Source Traceability**: MM-353; FR-001 through FR-010; SC-001 through SC-005; DESIGN-REQ-002, DESIGN-REQ-012, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-029.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py tests/unit/workflows/temporal/workflows/test_run_dependency_signals.py`
- Integration tests: `./tools/test_integration.sh` when Docker compose is available
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm active artifacts and current workflow test surfaces.

- [X] T001 Confirm MM-353 is preserved in specs/188-merge-outcome-propagation/spec.md, plan.md, research.md, data-model.md, contracts/merge-outcome-propagation.md, quickstart.md, and docs/tmp/jira-orchestration-inputs/MM-353-moonspec-orchestration-input.md (FR-010, SC-005)
- [X] T002 Confirm focused test targets exist and are runnable or have exact environment blockers recorded: tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py, tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py, tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py, tests/unit/workflows/temporal/workflows/test_run_dependency_signals.py (SC-001, SC-002, SC-003)

## Phase 2: Foundational

**Purpose**: Validate existing contracts before adding story behavior.

- [X] T003 Review existing parent merge automation helpers in moonmind/workflows/temporal/workflows/run.py for success, failure, unknown status, and cancellation mapping gaps (FR-001, FR-003, FR-004, FR-005)
- [X] T004 Review existing merge automation workflow cancellation and resolver child handling in moonmind/workflows/temporal/workflows/merge_automation.py (FR-007, FR-008, DESIGN-REQ-024)
- [X] T005 Review dependency signal tests in tests/unit/workflows/temporal/workflows/test_run_dependency_signals.py to confirm success-only dependency satisfaction remains tied to parent workflow status (FR-002, FR-006)

## Phase 3: Story - Propagate Merge Automation Outcomes

**Summary**: As a downstream task author, I want the parent `MoonMind.Run` terminal state to faithfully reflect merge automation success, failure, expiration, or cancellation so that dependency behavior is deterministic.

**Independent Test**: Run parent workflow-boundary tests with stubbed `MoonMind.MergeAutomation` completions for every allowed terminal status and assert parent terminal state, dependency satisfaction behavior, and cancellation propagation.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-002, DESIGN-REQ-012, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-029

### Unit Tests

- [X] T006 [P] Add failing unit tests in tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py for parent success statuses `merged` and `already_merged`, failure statuses `blocked`, `failed`, `expired`, cancellation status `canceled`, and missing or unsupported status values (FR-001, FR-003, FR-004, FR-005, SC-001, SC-004)
- [X] T007 [P] Add failing dependency signal assertions in tests/unit/workflows/temporal/workflows/test_run_dependency_signals.py if existing coverage does not prove failed and canceled parent outcomes do not satisfy downstream dependencies (FR-002, FR-006, SC-002)
- [X] T008 Run focused unit test command for T006-T007 and record expected red-first failures in this tasks.md (SC-001, SC-002, SC-004)

### Workflow-Boundary Tests

- [X] T009 [P] Add failing workflow-boundary tests in tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py proving child `canceled` maps to parent cancellation semantics rather than ValueError failure, and missing or unsupported child status fails deterministically with an operator-readable reason (FR-004, FR-005, DESIGN-REQ-023)
- [X] T010 [P] Add failing merge automation workflow-boundary tests in tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py proving cancellation while a resolver child is active requests resolver child cancellation and produces truthful canceled status or summary state (FR-008, DESIGN-REQ-024, SC-003)
- [X] T011 Run focused unit test command for T009-T010 and record expected red-first failures in this tasks.md (SC-001, SC-003, SC-004)

### Implementation

- [X] T012 Implement deterministic parent merge automation status classification in moonmind/workflows/temporal/workflows/run.py so `merged` and `already_merged` succeed, `blocked`/`failed`/`expired` fail, `canceled` cancels, and missing or unsupported statuses fail with a bounded operator-readable reason (FR-001, FR-003, FR-004, FR-005, DESIGN-REQ-012, DESIGN-REQ-023)
- [X] T013 Implement or refine parent cancellation propagation in moonmind/workflows/temporal/workflows/run.py so operator cancellation while awaiting merge automation requests child cancellation and records truthful status without treating best-effort cleanup as success (FR-007, FR-009, DESIGN-REQ-024)
- [X] T014 Implement or refine active resolver cancellation propagation in moonmind/workflows/temporal/workflows/merge_automation.py so merge automation cancellation requests cancellation of active resolver child runs and reports canceled state truthfully (FR-008, FR-009, DESIGN-REQ-024)
- [X] T015 Validate dependency behavior remains parent-success-only without redirecting dependency targets; update production code only if tests reveal a regression in moonmind/workflows/temporal/workflows/run.py or moonmind/workflows/temporal/service.py (FR-002, FR-006, DESIGN-REQ-002)
- [X] T016 Run the focused unit test command until all MM-353 tests pass (SC-001, SC-002, SC-003, SC-004)

**Checkpoint**: Parent outcome propagation, cancellation propagation, and dependency satisfaction behavior are covered and passing.

## Phase 4: Polish And Verification

- [X] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for the full unit suite or record exact blocker (SC-001 through SC-005)
- [X] T018 Run `./tools/test_integration.sh` when Docker compose is available or record exact blocker (SC-001 through SC-005)
- [X] T019 Run `/moonspec-verify` equivalent for specs/188-merge-outcome-propagation/spec.md and write the result in specs/188-merge-outcome-propagation/verification.md (FR-010, SC-005)

## Dependencies & Execution Order

- Phase 1 must complete before Phase 2.
- Phase 2 must complete before story implementation.
- T006-T007 and T009-T010 can be authored in parallel because they touch different test files.
- T008 and T011 must confirm red-first failures before T012-T015.
- T012-T015 must complete before T016.
- T017-T019 run after focused tests pass.

## Implementation Strategy

1. Preserve MM-353 traceability across artifacts.
2. Add red tests for all terminal mappings, missing or unsupported status, dependency success-only behavior, and cancellation propagation.
3. Implement the minimal workflow changes needed to make focused tests pass.
4. Run focused tests, full unit tests where feasible, integration tests where available, and final `/moonspec-verify`.
