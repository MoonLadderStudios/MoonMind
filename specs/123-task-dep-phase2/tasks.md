# Tasks: Task Dependencies Phase 2 - MoonMind.Run Dependency Gate

**Input**: Design documents from `specs/123-task-dep-phase2/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/requirements-traceability.md`

**Tests**: Test-driven development is required for this feature. Each user story starts with failing workflow-boundary tests before runtime code changes.

**Organization**: Tasks are grouped by user story so each slice can be implemented and validated independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g. `US1`, `US2`, `US3`)

---

## Phase 1: Setup

**Purpose**: Confirm the runtime scope and the concrete files that own the dependency gate.

- [X] T001 Audit `moonmind/workflows/temporal/workflows/run.py`, `tests/unit/workflows/temporal/workflows/test_run_scheduling.py`, and `tests/unit/workflows/temporal/workflows/test_run_signals_updates.py` against `specs/123-task-dep-phase2/spec.md` and `docs/Tasks/TaskDependencies.md`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish failing test coverage before changing workflow runtime code.

- [X] T002 [P] Add shared dependency-gate test helpers in `tests/unit/workflows/temporal/workflows/test_run_scheduling.py` for dependency-aware workflow start paths and planning/execution call ordering
- [X] T003 [P] Add direct unit-test scaffolding in `tests/unit/workflows/temporal/workflows/test_run_signals_updates.py` for dependency metadata, external workflow handle doubles, and dependency failure simulation

**Checkpoint**: Dependency-gate test harness is ready. User-story tests can now be written first and made to fail.

---

## Phase 3: User Story 1 - Dependent run waits before planning (Priority: P1) 🎯 MVP

**Goal**: Dependency-aware runs enter `waiting_on_dependencies`, expose dependency metadata, and only reach planning after prerequisite success.

**Independent Test**: Run the scheduling workflow tests and verify that dependency-aware runs wait before planning while runs without dependencies still go straight to planning.

### Tests for User Story 1

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [X] T004 [P] [US1] Add failing workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_scheduling.py` for patched dependency gating, planning-after-dependencies ordering, and empty-dependency bypass (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-006, DOC-REQ-009)

### Implementation for User Story 1

- [X] T005 [US1] Implement dependency parsing, `waiting_on_dependencies` state transition, `dependency_wait` metadata, memo dependency IDs, successful external-handle waiting, and post-wait pause gating in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-006, DOC-REQ-009)

### Validation for User Story 1

- [X] T006 [US1] Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_scheduling.py` to validate the patched wait path and no-dependency bypass behavior (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-006, DOC-REQ-009)

**Checkpoint**: Dependency-aware runs now block before planning and dependency-free runs preserve the direct planning path.

---

## Phase 4: User Story 2 - Dependency failures fail the dependent run clearly (Priority: P1)

**Goal**: Failed, canceled, terminated, or otherwise unsuccessful prerequisites fail the dependent run with dependency-specific messaging.

**Independent Test**: Run direct dependency-gate tests that simulate failed prerequisite handles and confirm dependency-specific workflow failure.

### Tests for User Story 2

- [X] T007 [P] [US2] Add failing dependency-outcome tests in `tests/unit/workflows/temporal/workflows/test_run_signals_updates.py` for failed/canceled prerequisite handles and dependency-specific failure messaging (DOC-REQ-003, DOC-REQ-007)

### Implementation for User Story 2

- [X] T008 [US2] Implement dependency failure classification and dependency-specific error propagation in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-003, DOC-REQ-007)

### Validation for User Story 2

- [X] T009 [US2] Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_signals_updates.py` to validate degraded dependency outcomes and failure messaging (DOC-REQ-003, DOC-REQ-007)

**Checkpoint**: Unsuccessful prerequisites fail dependents deterministically instead of hanging.

---

## Phase 5: User Story 3 - Dependency waiting remains replay-safe and cancel-safe (Priority: P2)

**Goal**: The dependency gate is replay-safe for in-flight histories and cancel-safe for dependent runs waiting on prerequisites.

**Independent Test**: Run workflow tests that exercise the patched path, legacy unpatched path, and dependent-run cancellation during dependency wait.

### Tests for User Story 3

- [X] T010 [P] [US3] Add failing workflow-boundary tests in `tests/unit/workflows/temporal/workflows/test_run_scheduling.py` for `workflow.patched(\"dependency-gate-v1\")`, cancellation-scope interruption, and legacy unpatched compatibility (DOC-REQ-004, DOC-REQ-005, DOC-REQ-008)

### Implementation for User Story 3

- [X] T011 [US3] Implement the patch-guarded dependency wait branch and cancellation-scope handling in `moonmind/workflows/temporal/workflows/run.py` without mutating prerequisite workflows (DOC-REQ-004, DOC-REQ-005, DOC-REQ-008)

### Validation for User Story 3

- [X] T012 [US3] Run `./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_scheduling.py` to validate patched, unpatched, and cancel-during-wait behavior (DOC-REQ-004, DOC-REQ-005, DOC-REQ-008)

**Checkpoint**: Dependency waiting is replay-safe for existing histories and cancel-safe for dependent runs.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Close the phase tracker and run the full regression gate.

- [X] T013 Update `docs/Tasks/TaskDependencies.md` to mark Phase 2 items complete and leave later phases open
- [X] T014 Run `./tools/test_unit.sh` for full regression coverage and confirm no unrelated run workflow regressions remain (FR-010, FR-011)

---

## Dependencies & Execution Order

- T001 must complete before new test work starts.
- T002 and T003 can run in parallel after T001.
- T004 must complete before T005, and T005 before T006.
- T007 must complete before T008, and T008 before T009.
- T010 must complete before T011, and T011 before T012.
- T013 and T014 depend on all user-story implementation and validation tasks completing.

---

## Implementation Strategy

1. Establish shared harnesses for dependency-gate testing.
2. Implement the success path first (US1) so the runtime behavior exists end-to-end.
3. Add explicit failure propagation (US2).
4. Finish replay/cancel safety and compatibility behavior (US3).
5. Update the phase tracker and run the full unit-test gate.

---

## Notes

- Runtime code changes are limited to `moonmind/workflows/temporal/workflows/run.py`.
- Validation must use `./tools/test_unit.sh`; do not invoke `pytest` directly.
- Every `DOC-REQ-*` is covered by at least one implementation task and one validation task.
