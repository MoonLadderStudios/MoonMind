# Tasks: Temporal Projection Sync

**Input**: Design documents from `/specs/001-temporal-projection-sync/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/requirements-traceability.md

## Format: `[ID] [P?] [Story] Description`

- [X] Tasks use sequential `T###` IDs in dependency order.
- [X] `[P]` marks tasks that are parallelizable (different files, no unmet dependencies).
- [X] `[US#]` labels are used only in user-story phases.
- [X] Every task includes concrete file path(s).

## Prompt B Scope Controls (Step 12/16)

- Runtime production implementation tasks are explicitly present: `T001`, `T002`, `T005`, `T006`, `T008`.
- Runtime validation tasks are explicitly present: `T003`, `T004`, `T007`, `T009`.
- `DOC-REQ-*` implementation + validation coverage is enforced with mappings for `DOC-REQ-001` through `DOC-REQ-004`.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Create core sync module file in `api_service/core/sync.py`

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [X] T002 Implement deterministic Temporal state mapping (DOC-REQ-001 implementation) in `api_service/core/sync.py`

## Phase 3: User Story 1 - Viewing Up-to-Date Execution Detail (Priority: P1) 🎯 MVP

**Goal**: Users viewing the Mission Control UI task detail view need to see the authoritative state of an execution as managed by Temporal.

**Independent Test**: Create an execution in Temporal, query the MoonMind execution detail API, and ensure the API response matches the Temporal server state exactly without duplicates in the local DB.

### Tests for User Story 1

- [X] T003 [P] [US1] Write unit test for mapping Temporal visibility to DB fields (DOC-REQ-001 validation) in `tests/unit/test_sync.py`
- [X] T004 [P] [US1] Write integration test for detail endpoint sync behaviour and rehydration without duplicates (DOC-REQ-002 validation, DOC-REQ-003 validation, DOC-REQ-004 validation) in `tests/integration/test_projection_sync.py`

### Implementation for User Story 1

- [X] T005 [P] [US1] Implement DB upsert logic to rehydrate missing rows without duplicates (DOC-REQ-003 implementation) in `api_service/core/sync.py`
- [X] T006 [US1] Update detail endpoint to call sync logic on read and repopulate DB (DOC-REQ-002 implementation, DOC-REQ-004 implementation) in `api_service/api/routers/executions.py`

## Phase 4: User Story 2 - Viewing Execution Lists (Priority: P1)

**Goal**: Users viewing the execution dashboard need the list view to reflect recent workflow progress reliably.

**Independent Test**: Progress a workflow in Temporal and verify the list API endpoint returns updated statuses.

### Tests for User Story 2

- [X] T007 [P] [US2] Write integration test verifying list endpoint matches Temporal state after progress (DOC-REQ-004 validation) in `tests/integration/test_projection_sync.py`

### Implementation for User Story 2

- [X] T008 [US2] Update list endpoint to consistently match the latest state from the Temporal server (DOC-REQ-004 implementation) in `api_service/api/routers/executions.py`

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T009 Run manual validation steps from `specs/001-temporal-projection-sync/quickstart.md`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### Implementation Strategy

- **MVP First**: Setup -> Foundational -> User Story 1. Deploy independently.
- **Incremental**: Add User Story 2 for list view capabilities.
