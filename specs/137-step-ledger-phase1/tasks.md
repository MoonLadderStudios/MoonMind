# Tasks: Step Ledger Phase 1

**Input**: Design documents from `/specs/137-step-ledger-phase1/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing contract/workflow tests before implementing the corresponding ledger behavior.

**Organization**: Tasks are grouped by user story so the contract freeze, workflow-owned ledger, and query surfaces can be implemented and validated incrementally.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

**Purpose**: Establish the runtime files and test files used by the phase-0/phase-1 rollout.

- [X] T001 Create the step-ledger helper module scaffold in `moonmind/workflows/temporal/step_ledger.py` for compact workflow-owned ledger state and reducers (DOC-REQ-002, DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-010)
- [X] T002 [P] Create the ledger contract test file `tests/unit/workflows/temporal/test_step_ledger.py` and workflow-boundary test file `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-012, DOC-REQ-013)

---

## Phase 2: Foundational Contract Freeze (Blocking Prerequisites)

**Purpose**: Freeze the v1 progress and step-ledger contract before workflow behavior changes.

**⚠️ CRITICAL**: No workflow implementation work should begin until these contract tests exist and fail first.

- [X] T003 [P] [US1] Write failing contract fixture tests in `tests/unit/workflows/temporal/test_step_ledger.py` for bounded `progress` and representative retrying, child-runtime, and reviewed step rows (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-013)
- [X] T004 [US1] Add canonical `ExecutionProgress`, step-ledger snapshot/row, check, refs, and artifact-slot schema models to `moonmind/schemas/temporal_models.py` (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-013)
- [X] T005 [US1] Implement pure default builders and progress reducer helpers in `moonmind/workflows/temporal/step_ledger.py` to satisfy the frozen contract tests (DOC-REQ-005, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010)
- [X] T006 [P] [US1] Add validation coverage in `tests/unit/workflows/temporal/test_step_ledger.py` proving full step rows, attempts, and checks are not mirrored into Memo/Search Attribute payloads (DOC-REQ-011, DOC-REQ-013)

**Checkpoint**: Contract models and helpers are frozen and validated before workflow orchestration changes.

---

## Phase 3: User Story 2 - Deterministic Workflow-Owned Step State (Priority: P1) 🎯 MVP

**Goal**: `MoonMind.Run` owns compact step truth derived from resolved plan metadata and tracks deterministic step transitions and attempts.

**Independent Test**: Execute the workflow against resolved plans and verify ready/running/waiting/reviewing/terminal transitions plus run-scoped attempts.

### Tests for User Story 2

- [X] T007 [P] [US2] Write failing workflow tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` for plan-resolved initialization plus `pending -> ready -> running -> succeeded` transitions sourced from plan-node metadata (DOC-REQ-001, DOC-REQ-002, DOC-REQ-008, DOC-REQ-010, DOC-REQ-012)
- [X] T008 [P] [US2] Write failing workflow tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` for `awaiting_external`, `reviewing`, `failed`, `skipped`, and `canceled` transitions with bounded error/summary fields (DOC-REQ-002, DOC-REQ-003, DOC-REQ-007, DOC-REQ-008, DOC-REQ-012)

### Implementation for User Story 2

- [X] T009 [US2] Initialize ordered latest-run ledger rows from resolved plan-node metadata in `moonmind/workflows/temporal/workflows/run.py` using `moonmind/workflows/temporal/step_ledger.py` after `parse_plan_definition()` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-006)
- [X] T010 [US2] Update `moonmind/workflows/temporal/workflows/run.py` to apply deterministic step transition helpers for ready/running/waiting/reviewing/terminal states and run-scoped attempt increments (DOC-REQ-002, DOC-REQ-008, DOC-REQ-010, DOC-REQ-012)
- [X] T011 [US2] Keep workflow-owned rows bounded in `moonmind/workflows/temporal/workflows/run.py` by storing only compact summaries, refs, and artifact-slot placeholders instead of logs or diagnostics bodies (DOC-REQ-003, DOC-REQ-007)
- [X] T012 [US2] Restrict Memo/Search Attribute updates in `moonmind/workflows/temporal/workflows/run.py` to compact user-visible summaries and counters while leaving full ledger state in workflow memory only (DOC-REQ-011)

**Checkpoint**: The workflow owns deterministic step state and bounded progress without leaking full rows into visibility surfaces.

---

## Phase 4: User Story 3 - Query the Latest-Run Ledger and Progress (Priority: P2)

**Goal**: Expose query-safe latest-run progress and step-ledger snapshots during execution and after completion.

**Independent Test**: Query the workflow while it is executing and after completion, confirming latest-run scope and stable `workflowId`/`runId` output.

### Tests for User Story 3

- [X] T013 [P] [US3] Write failing query tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covering progress and step-ledger reads while executing and after completion (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-012, DOC-REQ-013)
- [X] T014 [P] [US3] Extend `tests/integration/workflows/temporal/workflows/test_run.py` with latest-run query coverage when the integration harness materially increases confidence (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)

### Implementation for User Story 3

- [X] T015 [US3] Add workflow queries for bounded progress and full latest-run step-ledger state in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006)
- [X] T016 [US3] Ensure the query payloads in `moonmind/workflows/temporal/workflows/run.py` remain readable after terminal completion and preserve latest-run-only semantics (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-010)

**Checkpoint**: Later API phases can consume stable workflow query contracts without changing the payload shape.

---

## Phase 5: Polish & Validation

**Purpose**: Final verification and scope enforcement for the phase-0/phase-1 rollout.

- [X] T017 [P] Add any required serialization regression coverage to `tests/unit/api/routers/test_executions.py` if the execution schema changes surface through existing detail serialization (DOC-REQ-005, DOC-REQ-013)
- [X] T018 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T019 Run targeted ledger tests via `pytest tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q`
- [X] T020 Run `./tools/test_unit.sh`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational Contract Freeze (Phase 2)**: Depends on Setup and blocks workflow implementation.
- **User Story 2 (Phase 3)**: Depends on Phase 2.
- **User Story 3 (Phase 4)**: Depends on Phase 3 state ownership and may overlap with late-cycle integration coverage.
- **Polish (Phase 5)**: Depends on all prior implementation work.

### Parallel Opportunities

- T001 and T002 can run in parallel.
- T003 and T006 can run in parallel once the test files exist.
- T007 and T008 can run in parallel before workflow implementation.
- T013 and T014 can run in parallel before query implementation.

## Implementation Strategy

### MVP First

1. Freeze the contract with failing tests and schema/helper implementations.
2. Implement workflow-owned deterministic ledger state.
3. Add query surfaces for progress and latest-run step state.
4. Run targeted tests, then the full unit suite.

### TDD Notes

- Prefer Red → Green sequencing for T003 before T004/T005, T007/T008 before T009–T012, and T013/T014 before T015/T016.
- If a change is not practical to drive entirely from a failing test first, add the closest boundary-level regression test immediately after the minimal implementation.
