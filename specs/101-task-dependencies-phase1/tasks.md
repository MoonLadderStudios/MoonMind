# Tasks: Task Dependencies Phase 1 — Backend Foundation

**Input**: Design documents from `specs/101-task-dependencies-phase1/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup

**Purpose**: No project initialization needed — this is an existing monorepo. Skip to foundational.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The enum value must exist before any status mapping or constant can reference it.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T001 Add `WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` to `MoonMindWorkflowState` enum in `api_service/db/models.py` (DOC-REQ-002, DOC-REQ-007)
- [x] T002 Generate Alembic migration to add `waiting_on_dependencies` to PostgreSQL `moonmindworkflowstate` native enum type in `api_service/migrations/versions/` (DOC-REQ-003)

**Checkpoint**: Enum value exists in Python and PostgreSQL. All downstream tasks can reference it.

---

## Phase 3: User Story 1 - Workflow reports waiting state (Priority: P1) 🎯 MVP

**Goal**: The workflow constant, projection sync, and dashboard status map all recognize `waiting_on_dependencies` so the system can surface it end-to-end.

**Independent Test**: Verify `STATE_WAITING_ON_DEPENDENCIES` constant exists, projection sync accepts the value, and dashboard mapper returns `"waiting"`.

### Implementation for User Story 1

- [x] T003 [P] [US1] Add `STATE_WAITING_ON_DEPENDENCIES = "waiting_on_dependencies"` constant to `moonmind/workflows/temporal/workflows/run.py` after `STATE_INITIALIZING` (DOC-REQ-004)
- [x] T004 [P] [US1] Add `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting"` to `_DASHBOARD_STATUS_BY_STATE` in `api_service/api/routers/executions.py` (DOC-REQ-006)
- [x] T005 [P] [US1] Register `(MoonMindWorkflowState.WAITING_ON_DEPENDENCIES, None)` in the projection sync mapping in `api_service/core/sync.py` (DOC-REQ-005)

### Validation for User Story 1

- [x] T006 [US1] Run `./tools/test_unit.sh` and verify all existing tests pass (DOC-REQ-002 through DOC-REQ-007)
- [x] T007 [US1] Add unit test to verify `MoonMindWorkflowState.WAITING_ON_DEPENDENCIES.value == "waiting_on_dependencies"` in tests (DOC-REQ-002)
- [x] T008 [US1] Add unit test to verify `_DASHBOARD_STATUS_BY_STATE[MoonMindWorkflowState.WAITING_ON_DEPENDENCIES] == "waiting"` in tests (DOC-REQ-006)

**Checkpoint**: US1 complete — workflow state is recognized across all API and dashboard layers.

---

## Phase 4: User Story 2 - State persists in database (Priority: P1)

**Goal**: The Alembic migration successfully alters the PostgreSQL enum type, and the new value can be stored/retrieved.

**Independent Test**: Run `alembic upgrade head` on a test database and insert a row with the new state.

### Implementation for User Story 2

- [x] T009 [US2] Verify Alembic migration file uses `ALTER TYPE moonmindworkflowstate ADD VALUE IF NOT EXISTS 'waiting_on_dependencies'` in upgrade and no-op in downgrade in `api_service/migrations/versions/<new>.py` (DOC-REQ-003)

### Validation for User Story 2

- [x] T010 [US2] Run `./tools/test_unit.sh` to confirm no migration-related test regressions (DOC-REQ-003)

**Checkpoint**: US2 complete — database accepts the new state value.

---

## Phase 5: User Story 3 - Compatibility layer recognizes new state (Priority: P2)

**Goal**: The compatibility status mapping translates `WAITING_ON_DEPENDENCIES` to a dashboard-friendly status for legacy API consumers.

**Independent Test**: Call the compatibility status mapping function with the new state.

### Implementation for User Story 3

- [x] T011 [P] [US3] Add `db_models.MoonMindWorkflowState.WAITING_ON_DEPENDENCIES: "waiting"` to `_TEMPORAL_STATUS_MAP` in `moonmind/workflows/tasks/compatibility.py` (DOC-REQ-006)
- [x] T012 [P] [US3] Add `"waiting": (db_models.MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,)` to the reverse status mapping in `moonmind/workflows/tasks/compatibility.py` (DOC-REQ-006)

### Validation for User Story 3

- [x] T013 [US3] Add unit test to verify compatibility status mapping returns `"waiting"` for `WAITING_ON_DEPENDENCIES` in tests (DOC-REQ-006)
- [x] T014 [US3] Run `./tools/test_unit.sh` and verify all tests pass (DOC-REQ-006)

**Checkpoint**: US3 complete — all status mapping layers are consistent.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification and documentation.

- [x] T015 [P] Update `docs/Tasks/TaskDependencies.md` to reflect Phase 1 implementation status
- [x] T016 Run full `./tools/test_unit.sh` as final regression gate

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 2)**: No dependencies — start immediately
- **US1 (Phase 3)**: Depends on Phase 2 (enum must exist before constants/maps reference it)
- **US2 (Phase 4)**: Depends on Phase 2 (migration depends on enum definition)
- **US3 (Phase 5)**: Depends on Phase 2 (compatibility map imports the enum)
- **Polish (Phase 6)**: Depends on all user stories complete

### Parallel Opportunities

- T003, T004, T005 can all run in parallel (different files, no dependencies on each other)
- T011, T012 can run in parallel (different lines in same file, but both `compatibility.py`)
- US1, US2, US3 are independent after Phase 2 — can run in any order

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (T001, T002)
2. Complete Phase 3: User Story 1 (T003–T008)
3. **STOP and VALIDATE**: Test independently — `./tools/test_unit.sh`
4. System can now recognize `waiting_on_dependencies` state across API + dashboard

### Incremental Delivery

1. Phase 2 → Foundation ready
2. US1 → Enum + constant + dashboard mapping are live (MVP!)
3. US2 → Database migration verified
4. US3 → Compatibility layer complete
5. Polish → Final regression + docs update

---

## Notes

- All tasks reference specific DOC-REQ IDs for full traceability
- [P] tasks = different files, no dependencies
- Commit after each phase checkpoint
- Total tasks: 16
- Tasks per story: US1=6, US2=2, US3=4, Foundational=2, Polish=2
