# Tasks: Task Dependencies Phase 1 — Submit Contract And Validation

**Input**: Design documents from `specs/117-task-dep-phase1/`
**Prerequisites**: plan.md (required), spec.md (required), research.md

**Organization**: Tasks are grouped by user story. Implementation code is pre-existing; tasks focus on the missing test and plan-status update.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2)

---

## Phase 1: Setup

**Purpose**: No project initialization needed — this is an existing codebase with a missing unit test.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Verify all existing implementations satisfy FRs before adding the missing test.

- [ ] T001 [US1] Audit `api_service/api/routers/executions.py` L758-773 to confirm FR-001 through FR-005 are implemented (read `payload.task.dependsOn`, coerce, trim, deduplicate, 10-item limit)
- [ ] T002 [US3] Audit `moonmind/workflows/temporal/service.py` `_validate_dependencies` (L219-265) to confirm FR-006, FR-007, FR-008 (missing target, non-Run type, self-dependency), FR-009, FR-010 (cycle detection) are implemented
- [ ] T003 [US2] Audit existing test files to confirm all FRs except FR-008 self-dependency have test coverage

**Checkpoint**: Audit complete. All implementations verified. Only FR-008 self-dependency test is missing.

---

## Phase 3: User Story 3 - Fix missing self-dependency test (Priority: P1) 🎯 MVP

**Goal**: Add the missing unit test for FR-008 (self-dependency rejection) to `tests/unit/workflows/temporal/test_temporal_service.py`.

**Independent Test**: Run `./tools/test_unit.sh` and confirm the new test passes.

### Implementation for User Story 3

- [ ] T004 [US3] Add `test_create_execution_rejects_self_dependency` to `tests/unit/workflows/temporal/test_temporal_service.py`:
  - Create an execution to obtain a `workflow_id`
  - Attempt to call `service.create_execution` with `initial_parameters={"task": {"dependsOn": [<the_new_workflow_id>]}}`  
  - Expect `TemporalExecutionValidationError` with message matching `"Workflow cannot depend on itself: <workflow_id>"`
  - Note: Since `_validate_dependencies` is called with the newly-generated UUID workflow ID, we need to mock it to test self-dependency. Alternatively, test `_validate_dependencies` directly.
  - Preferred approach: Call `service._validate_dependencies(depends_on=["mm:some-id"], new_workflow_id="mm:some-id")` directly and assert the expected error.

### Validation for User Story 3

- [ ] T005 [US3] Run `./tools/test_unit.sh` and verify `test_create_execution_rejects_self_dependency` passes

**Checkpoint**: US3 complete — FR-008 self-dependency test added and passing.

---

## Phase 4: User Story 2 / Plan Tracking Update (Priority: P2)

**Goal**: Update `docs/Tasks/TaskDependencies.md` to mark Phase 1 as complete.

### Implementation

- [ ] T006 [US2] Update `docs/Tasks/TaskDependencies.md` to mark Phase 1 as complete

### Validation

- [ ] T007 [US2] Confirm `docs/Tasks/TaskDependencies.md` Phase 1 entry shows completed status and Phases 2–5 remain open

**Checkpoint**: Plan doc updated.

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Final regression gate.

- [ ] T008 Run `./tools/test_unit.sh` to confirm all tests pass with zero regressions (FR-013, SC-006)

---

## Dependencies & Execution Order

- **T001–T003**: Independent audits, can run in parallel
- **T004**: Depends on T001–T003 (audit first)
- **T005**: Depends on T004
- **T006–T007**: Independent of T004–T005
- **T008**: Final gate, depends on all

---

## Implementation Strategy

1. Phase 2: Audit implementations (T001–T003)
2. Phase 3: Add self-dependency test (T004–T005)
3. Phase 4: Update plan tracker (T006–T007)
4. Phase 5: Regression gate (T008)

---

## Notes

- Total tasks: 8
- Tasks per story: US1=1, US2=2, US3=3, Polish=1, Audit=2
- Implementation code is pre-existing; the only code change is adding one test function
