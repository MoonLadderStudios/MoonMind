# Tasks: Report Workflow Rollout and Examples

**Input**: Design documents from `/specs/232-report-workflow-rollout-examples/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around a single user story so the work stays focused, traceable, and independently testable.

**Source Traceability**: MM-464; FR-001 through FR-009; SC-001 through SC-005; DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py`
- Integration tests: `pytest tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py -q`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the feature reuses existing report artifact runtime module and tests.

- [X] T001 Create Moon Spec artifacts for MM-464 in `specs/232-report-workflow-rollout-examples/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new infrastructure is required; existing report artifact helpers are the foundation.

- [X] T002 Confirm existing report artifact helpers and tests in `moonmind/workflows/temporal/report_artifacts.py` and `tests/unit/workflows/temporal/test_artifacts.py`

**Checkpoint**: Foundation ready - story test and implementation work can now begin

---

## Phase 3: Story - Validate Report Workflow Rollout Mappings

**Summary**: As a MoonMind maintainer, I want report-producing workflow families to have executable rollout mappings for report, evidence, runtime, and diagnostic artifacts so migrations can prefer report semantics while generic output fallback remains safe.

**Independent Test**: Validate each supported workflow family mapping in isolation, then verify a new report-producing workflow must include `report.primary`, evidence/runtime/diagnostic artifacts stay distinct where expected, and an execution with only generic `output.primary` is classified as a legacy fallback rather than a canonical report.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022

**Test Plan**:

- Unit: mapping contents, validation failures, fallback classification, projection summary guardrails
- Integration: existing artifact service tests continue to cover generic outputs and report bundle publication boundaries

### Unit Tests (write first) ⚠️

- [X] T003 [P] Add failing unit tests for workflow family mappings in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` covering FR-001, FR-002, DESIGN-REQ-003, DESIGN-REQ-007, and DESIGN-REQ-019
- [X] T004 [P] Add failing unit tests for report-producing validation and generic fallback classification in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` covering FR-003, FR-005, FR-006, DESIGN-REQ-020, SC-002, and SC-003
- [X] T005 [P] Add failing unit tests for rollout phases and projection summary guardrails in `tests/unit/workflows/temporal/test_report_workflow_rollout.py` covering FR-007, FR-008, DESIGN-REQ-021, DESIGN-REQ-022, and SC-004
- [X] T006 Run `pytest tests/unit/workflows/temporal/test_report_workflow_rollout.py -q` to confirm T003-T005 fail before implementation

### Integration Tests (write first) ⚠️

- [X] T007 Reuse existing artifact service tests in `tests/unit/workflows/temporal/test_artifacts.py` as the integration-style boundary for generic outputs and report bundle publication
- [X] T008 Run `pytest tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/workflows/temporal/test_artifacts.py -q --tb=short` to confirm new tests fail only for missing MM-464 helpers

### Implementation

- [X] T009 Implement report workflow mapping data and accessors in `moonmind/workflows/temporal/report_artifacts.py` covering FR-001, FR-002, DESIGN-REQ-003, DESIGN-REQ-007, and DESIGN-REQ-019
- [X] T010 Implement report rollout validation and generic fallback classification in `moonmind/workflows/temporal/report_artifacts.py` covering FR-003, FR-005, FR-006, and DESIGN-REQ-020
- [X] T011 Implement ordered rollout phases and projection summary guardrails in `moonmind/workflows/temporal/report_artifacts.py` covering FR-007, FR-008, DESIGN-REQ-021, and DESIGN-REQ-022
- [X] T012 Run targeted unit and related artifact tests, fix failures, and verify story behavior end-to-end

**Checkpoint**: The story is fully functional, covered by unit and related artifact boundary tests, and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen traceability without adding hidden scope.

- [X] T013 Run traceability check for MM-464 and source design IDs across specs, input brief, runtime code, and tests
- [X] T014 Create `specs/232-report-workflow-rollout-examples/verification.md` with final evidence and verdict
- [X] T015 Run `/speckit.verify` equivalent by checking the final implementation against the original MM-464 request

---

## Dependencies & Execution Order

- Setup and foundational inspection are complete before story implementation.
- Unit tests must fail before production code changes.
- Targeted unit tests and related artifact tests must pass before final verification.

## Implementation Strategy

Implement the smallest runtime contract that satisfies MM-464: immutable mappings, explicit validation/classification, and projection guardrails. Do not alter artifact persistence, Mission Control UI, or workflow publication behavior beyond these helpers.
