# Tasks: Step Ledger Phase 5

**Input**: Design documents from `/specs/142-step-ledger-phase5/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing workflow-boundary/UI tests before implementing the corresponding behavior.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create or extend the Phase 5 workflow-boundary test target in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py`.
- [X] T002 Create or extend the Phase 5 UI test target in `frontend/src/entrypoints/task-detail.test.tsx`.

---

## Phase 2: User Story 1 - Approval policy becomes the first real `checks[]` producer (Priority: P1)

**Goal**: `MoonMind.Run` owns live review/check state in the step ledger.

### Tests for User Story 1

- [X] T003 [P] [US1] Write failing workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covering `reviewing` transitions, pending approval-policy checks, PASS verdicts, and `INCONCLUSIVE` acceptance.
- [X] T004 [P] [US1] Extend `tests/unit/workflows/temporal/test_step_ledger.py` with failing helper/model assertions for structured approval-policy check mutation and bounded artifact refs.

### Implementation for User Story 1

- [X] T005 [US1] Update `moonmind/workflows/temporal/step_ledger.py` with deterministic helpers for upserting structured step checks.
- [X] T006 [US1] Update `moonmind/workflows/temporal/workflows/run.py` to invoke `step.review` for eligible completed steps, mark rows `reviewing`, persist review evidence artifacts, and finalize approval-policy check rows.

---

## Phase 3: User Story 2 - Failed reviews retry with feedback on the same logical step (Priority: P1)

**Goal**: Approval-policy failures retry the same logical step while keeping the row authoritative.

### Tests for User Story 2

- [X] T007 [P] [US2] Write failing workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covering FAIL→retry→PASS, retry count accumulation, feedback injection, and bounded artifact-backed review evidence.

### Implementation for User Story 2

- [X] T008 [US2] Update `moonmind/workflows/temporal/workflows/run.py` to inject review feedback into reruns, increment retry counts on the approval-policy check row, and keep full feedback out of workflow state.

---

## Phase 4: User Story 3 - Mission Control exposes review verdicts inside Checks (Priority: P2)

**Goal**: The existing Steps UI clearly shows review verdicts, retry counts, and evidence refs.

### Tests for User Story 3

- [X] T009 [P] [US3] Write failing UI tests in `frontend/src/entrypoints/task-detail.test.tsx` covering verdict badges, retry-count copy, and review artifact refs in the expanded Checks section.

### Implementation for User Story 3

- [X] T010 [US3] Update `frontend/src/entrypoints/task-detail.tsx` and `frontend/src/styles/mission-control.css` so the Checks section renders retry counts and review artifact refs with compact Mission Control styling.

---

## Phase 5: Validation

- [X] T011 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T012 Run `pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q`
- [ ] T013 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [ ] T014 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/workflows/temporal/workflows/test_run_step_ledger.py`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **User Story 1 (Phase 2)**: Depends on Setup.
- **User Story 2 (Phase 3)**: Depends on the approval-policy check production from User Story 1.
- **User Story 3 (Phase 4)**: Depends on the finalized `checks[]` shape from User Story 1/2.
- **Validation (Phase 5)**: Depends on all implementation work.

### Parallel Opportunities

- T003 and T004 can be written in parallel before implementation begins.
- T009 can be written in parallel with backend work once the final check-row contract is agreed.

## Implementation Strategy

### MVP First

1. Add failing workflow tests for live review/check state.
2. Implement workflow-owned approval-policy checks and review artifacts.
3. Add retry-with-feedback coverage.
4. Extend the existing Checks UI with retry counts and review evidence refs.
5. Run final verification.
