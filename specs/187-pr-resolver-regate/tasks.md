# Tasks: PR Resolver Child Re-Gating

**Input**: Design documents from `/specs/187-pr-resolver-regate/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around the single MM-352 story: resolver attempts must run as child MoonMind.Run executions and drive merge automation through explicit dispositions.

**Source Traceability**: Covers FR-001 through FR-010, acceptance scenarios 1-5, edge cases, SC-001 through SC-005, and DESIGN-REQ-005, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-029 from [spec.md](./spec.md).

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Phase 1: Setup

**Purpose**: Confirm existing workflow surfaces and MM-352 traceability before modifying tests or code.

- [X] T001 Review the active MM-352 orchestration input in `docs/tmp/jira-orchestration-inputs/MM-352-moonspec-orchestration-input.md` for FR-010 and DESIGN-REQ-029
- [X] T002 Review resolver child request construction in `moonmind/workflows/temporal/workflows/merge_gate.py` for FR-001 through FR-003 and DESIGN-REQ-014, DESIGN-REQ-016
- [X] T003 Review disposition handling and re-gating loop behavior in `moonmind/workflows/temporal/workflows/merge_automation.py` for FR-004 through FR-009 and DESIGN-REQ-019 through DESIGN-REQ-022
- [X] T004 [P] Review focused tests in `tests/unit/workflows/temporal/test_merge_gate_workflow.py` for resolver child request coverage
- [X] T005 [P] Review focused workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` for re-gating and disposition coverage

## Phase 2: Foundational

**Purpose**: Establish the exact missing test coverage before production changes.

- [X] T006 Add failing unit assertions for resolver child `task.tool.version = 1.0` in `tests/unit/workflows/temporal/test_merge_gate_workflow.py` covering FR-003, SC-001, DESIGN-REQ-016
- [X] T007 Add failing workflow-boundary tests for merged and already_merged dispositions in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-005, SC-002, DESIGN-REQ-020
- [X] T008 Add failing workflow-boundary tests for manual_review and failed dispositions producing non-success outcomes in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-007, SC-002, DESIGN-REQ-020
- [X] T009 Add failing workflow-boundary tests for missing and unsupported dispositions producing deterministic non-success outcomes in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-009, SC-004
- [X] T010 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` and confirm T006-T009 fail for the expected missing MM-352 contract

**Checkpoint**: Foundational tests prove the remaining resolver child and disposition contract gaps.

## Phase 3: Story - Run Resolver Children And Re-Gate

**Summary**: As a maintainer relying on merge automation, I want each resolver attempt to run as a child MoonMind.Run with an explicit disposition so that MoonMind can merge, re-enter the gate after resolver pushes, or stop for manual review without inferring outcomes from logs.

**Independent Test**: Run a workflow-boundary test where the merge gate opens, stub resolver child MoonMind.Run results return each allowed merge automation disposition, and MoonMind.MergeAutomation either succeeds, re-enters awaiting external readiness with an incremented cycle, or returns a non-success outcome with blockers.

**Traceability**: FR-001 through FR-010; acceptance scenarios 1-5; edge cases; SC-001 through SC-005; DESIGN-REQ-005, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-029.

**Test Plan**:

- Unit: resolver child request shape, publishMode none, pr-resolver tool identity and version.
- Integration/workflow-boundary: child workflow launch, merged/already_merged success, reenter_gate loop, manual_review/failed non-success, missing/unsupported disposition guardrails, head-SHA freshness.

### Unit Tests

- [X] T011 [P] Extend resolver child request test in `tests/unit/workflows/temporal/test_merge_gate_workflow.py` to assert top-level publishMode none and exact pr-resolver skill version for FR-003 and SC-001
- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_merge_gate_workflow.py` to confirm resolver request tests pass after implementation

### Workflow-Boundary Tests

- [X] T013 [P] Extend re-gating workflow-boundary test in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` to assert child payload tool version and head-SHA-scoped child IDs covering FR-001, FR-003, FR-006, SC-003
- [X] T014 [P] Add workflow-boundary test matrix for allowed terminal dispositions in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-005, FR-007, SC-002
- [X] T015 [P] Add workflow-boundary test matrix for invalid disposition guardrails in `tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` covering FR-009, SC-004
- [X] T016 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py` to confirm workflow-boundary tests pass after implementation

### Implementation

- [X] T017 Add resolver child `task.tool.version = 1.0` to `moonmind/workflows/temporal/workflows/merge_gate.py` covering FR-003 and DESIGN-REQ-016
- [X] T018 Update `moonmind/workflows/temporal/workflows/merge_automation.py` to classify dispositions explicitly before falling back to child status covering FR-004 through FR-007 and DESIGN-REQ-019 through DESIGN-REQ-021
- [X] T019 Update `moonmind/workflows/temporal/workflows/merge_automation.py` to return deterministic non-success summaries for missing and unsupported dispositions covering FR-009 and SC-004
- [X] T020 Verify `reenter_gate` refreshes tracked head SHA and returns to waiting without reusing stale readiness in `moonmind/workflows/temporal/workflows/merge_automation.py` covering FR-006, FR-008, SC-003, DESIGN-REQ-021, DESIGN-REQ-022
- [X] T021 Run the focused unit command and fix failures until the story passes end-to-end

**Checkpoint**: The MM-352 story is implemented, covered by focused unit and workflow-boundary tests, and preserves MM-352 traceability.

## Phase 4: Polish And Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T022 [P] Review `docs/Tasks/PrMergeAutomation.md` and leave canonical docs unchanged unless implementation behavior contradicts the desired-state contract
- [X] T023 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for full unit verification
- [X] T024 Run `./tools/test_integration.sh` for hermetic integration verification, or document the exact environment blocker if Docker is unavailable
- [X] T025 Run `/moonspec-verify` for `specs/187-pr-resolver-regate/spec.md` and record the result in `specs/187-pr-resolver-regate/verification.md`

## Dependencies And Execution Order

- Phase 1 has no dependencies.
- Phase 2 depends on Phase 1 context and blocks implementation.
- Phase 3 tests must be written and confirmed failing before T017-T020 implementation tasks.
- Phase 4 depends on focused story tests passing.

## Parallel Opportunities

- T004 and T005 can run in parallel.
- T011, T013, T014, and T015 can be authored in parallel if test-file edits are coordinated.
- T022 can run in parallel with final verification commands.

## Implementation Strategy

1. Confirm the existing resolver child and merge automation workflow surfaces.
2. Add red-first tests for the exact MM-352 gaps.
3. Patch resolver child request shape and disposition classification.
4. Run focused validation, then full unit validation.
5. Record MoonSpec verification evidence against MM-352.
