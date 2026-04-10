# Tasks: Step Ledger Phase 2

**Input**: Design documents from `/specs/138-step-ledger-phase2/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: TDD is required where feasible. Write failing tests before implementing the corresponding lineage/evidence behavior.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

## Phase 1: Setup

- [X] T001 Create or extend the Phase 2 test targets in `tests/unit/workflows/temporal/test_step_ledger.py`, `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and the relevant `test_agent_run_*` workflow files.

---

## Phase 2: User Story 1 - Parent steps expose child lineage refs (Priority: P1)

**Goal**: Parent step rows capture stable child workflow lineage and task-run observability refs.

### Tests for User Story 1

- [X] T002 [P] [US1] Write failing workflow tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covering child workflow refs and `taskRunId` projection onto the latest step row.
- [X] T003 [P] [US1] Write failing `MoonMind.AgentRun` tests in `tests/unit/workflows/temporal/workflows/test_agent_run_jules_execution.py` and `tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py` covering returned lineage/task-run metadata.

### Implementation for User Story 1

- [X] T004 [US1] Update `moonmind/workflows/temporal/workflows/agent_run.py` to enrich returned result metadata with child workflow lineage and best-available task-run identifiers.
- [X] T005 [US1] Update `moonmind/workflows/temporal/workflows/run.py` to store child workflow refs and task-run refs on parent step rows from compact result metadata only.

---

## Phase 3: User Story 2 - Parent steps expose grouped evidence slots (Priority: P1)

**Goal**: Parent step rows expose canonical grouped evidence refs without artifact hydration.

### Tests for User Story 2

- [X] T006 [P] [US2] Write failing structured-row merge tests in `tests/unit/workflows/temporal/test_step_ledger.py` for refs/artifact slot updates.
- [X] T007 [P] [US2] Write failing workflow tests in `tests/unit/workflows/temporal/workflows/test_run_step_ledger.py` covering deterministic slot grouping from managed-runtime and generic execution results.
- [X] T008 [P] [US2] Write failing managed-run metadata tests in `tests/unit/workflows/temporal/test_agent_runtime_activities.py` covering stdout/stderr/merged/diagnostics/task-run metadata enrichment.

### Implementation for User Story 2

- [X] T009 [US2] Extend `moonmind/workflows/temporal/step_ledger.py` to merge structured refs/artifacts onto existing rows without replacing the frozen schema.
- [X] T010 [US2] Update `moonmind/workflows/temporal/activity_runtime.py` to enrich managed-run fetch results with compact observability refs and task-run metadata.
- [X] T011 [US2] Update `moonmind/workflows/temporal/workflows/run.py` to group compact result metadata into canonical step artifact slots while keeping workflow state bounded.

---

## Phase 4: User Story 3 - Published artifacts carry step-scoped metadata (Priority: P2)

**Goal**: Agent-runtime artifact publication stamps explicit step identity/attempt metadata for later grouping and projection.

### Tests for User Story 3

- [X] T012 [P] [US3] Write failing activity tests in `tests/unit/workflows/temporal/test_agent_runtime_activities.py` asserting summary/result artifact metadata includes `step_id`, `attempt`, and `scope` when step context exists.

### Implementation for User Story 3

- [X] T013 [US3] Carry compact step context from `moonmind/workflows/temporal/workflows/run.py` into child request metadata and stamp that metadata during `agent_runtime.publish_artifacts` in `moonmind/workflows/temporal/activity_runtime.py`.

---

## Phase 5: Validation

- [X] T014 Run `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
- [X] T015 Run targeted Phase 2 tests via `pytest tests/unit/workflows/temporal/test_step_ledger.py tests/unit/workflows/temporal/workflows/test_run_step_ledger.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/workflows/test_agent_run_jules_execution.py tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py -q`
- [X] T016 Run `./tools/test_unit.sh`
