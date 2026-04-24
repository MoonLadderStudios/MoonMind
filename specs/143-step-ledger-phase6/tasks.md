# Tasks: Step Ledger Phase 6

**Input**: Design documents from `/specs/143-step-ledger-phase6/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing router/contract/browser tests before implementing the corresponding behavior.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create the Phase 6 feature package and traceability artifacts under `specs/143-step-ledger-phase6/`.

---

## Phase 2: User Story 1 - Execution detail follows the workflow's latest run during projection lag (Priority: P1)

**Goal**: Backend detail reads and task-detail latest-run state remain internally consistent through Continue-As-New lag.

### Tests for User Story 1

- [X] T002 [P] [US1] Write failing router tests in `tests/unit/api/routers/test_executions.py` covering latest-run `runId` reconciliation from the bounded progress query.
- [X] T003 [P] [US1] Write failing public-contract coverage in `tests/contract/test_temporal_execution_api.py` proving execution detail follows the queried latest run without changing the public `progress` shape.
- [X] T004 [P] [US1] Write failing workflow-query coverage in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` for latest-run metadata carried on the bounded progress query payload.

### Implementation for User Story 1

- [X] T005 [US1] Update `moonmind/workflows/temporal/workflows/run.py` so the bounded progress query carries internal latest-run metadata.
- [X] T006 [US1] Update `api_service/api/routers/executions.py` so execution detail adopts the queried latest `runId` while returning the same public `progress` contract.

---

## Phase 3: User Story 2 - Latest-run semantics remain truthful through degraded reads and repair (Priority: P1)

**Goal**: Secondary task-detail evidence and repair/degraded reads stay latest-run-only.

### Tests for User Story 2

- [X] T007 [P] [US2] Write failing browser coverage in `frontend/src/entrypoints/task-detail.test.tsx` proving generic artifact reads switch to the latest run exposed by the step ledger.

### Implementation for User Story 2

- [X] T008 [US2] Update `frontend/src/entrypoints/task-detail.tsx` so execution-wide artifact reads use the latest resolved run id rather than a stale detail snapshot.

---

## Phase 4: User Story 3 - Step-ledger rollout trackers are retired (Priority: P2)

**Goal**: Remove completed step-ledger rollout backlog bullets from tmp trackers.

### Implementation for User Story 3

- [X] T009 [US3] Retire completed step-ledger rollout bullets from the relevant `docs/Temporal/StepLedgerAndProgressModel.md*.md` tracker files.

---

## Phase 5: Validation

- [ ] T010 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T011 Run `pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q`
- [X] T012 Run `pytest tests/unit/api/routers/test_executions.py -q`
- [X] T013 Run `pytest tests/contract/test_temporal_execution_api.py -q`
- [ ] T014 Run `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`
- [ ] T015 Run `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/api/routers/test_executions.py tests/contract/test_temporal_execution_api.py`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **User Story 1 (Phase 2)**: Depends on Setup.
- **User Story 2 (Phase 3)**: Depends on the latest-run identity work from User Story 1.
- **User Story 3 (Phase 4)**: Depends on verification that the hardening work is implemented.
- **Validation (Phase 5)**: Depends on all implementation work.

### Parallel Opportunities

- T002, T003, and T004 can be written in parallel before backend implementation.
- T007 can be written in parallel with the backend work once the latest-run contract is fixed.

## Implementation Strategy

### MVP First

1. Add failing backend tests for queried latest-run reconciliation.
2. Reconcile execution detail `runId` from workflow query truth without changing the public `progress` contract.
3. Align the task-detail generic artifact panel to the latest run.
4. Retire completed tmp trackers and run final verification.
