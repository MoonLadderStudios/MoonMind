# Tasks: Temporal UI Actions and Submission Flags

**Input**: Design documents from `/specs/071-temporal-ui-flags/`
**Prerequisites**: plan.md, spec.md

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Verify project structure and testing environment

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [ ] T002 Update `moonmind/config/settings.py` to add `TEMPORAL_DASHBOARD_ACTIONS_ENABLED` setting with default `True` (DOC-REQ-001, DOC-REQ-005)
- [ ] T003 Update `moonmind/config/settings.py` to add `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` setting with default `True` (DOC-REQ-002, DOC-REQ-005)
- [ ] T004 [P] Add unit test for settings in `tests/unit/config/test_settings.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005)

## Phase 3: User Story 1 - Enable Temporal UI Task Actions (Priority: P1)

**Goal**: As a Mission Control operator, I want to use UI buttons to manage Temporal workflows (Pause, Resume, Approve, etc.) so that I can directly interact with real-time execution state without legacy database limitations.

**Independent Test**: Can be tested by navigating to a task detail page for a running workflow and verifying the appropriate action buttons are visible and functional based on its state.

### Tests for User Story 1

- [ ] T005 [P] [US1] Add test for UI action visibility based on workflow state in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-003, DOC-REQ-005)
- [ ] T006 [P] [US1] Add test for UI action execution endpoints routing to Temporal in `tests/unit/api/routers/test_executions.py` (DOC-REQ-003, DOC-REQ-005)

### Implementation for User Story 1

- [ ] T007 [US1] Modify `api_service/api/routers/task_dashboard_view_model.py` to conditionally display UI action buttons based on `TEMPORAL_DASHBOARD_ACTIONS_ENABLED` and workflow state (DOC-REQ-003)
- [ ] T008 [US1] Update `api_service/api/routers/executions.py` endpoints to route UI actions to Temporal signals/updates when `TEMPORAL_DASHBOARD_ACTIONS_ENABLED` is true (DOC-REQ-003)

## Phase 4: User Story 2 - Enable Temporal Task Submission (Priority: P1)

**Goal**: As a Mission Control operator, I want to submit new tasks directly to Temporal so that execution begins immediately and correctly registers in the workflow history.

**Independent Test**: Can be tested by filling out the `/tasks/new` form and submitting it to verify immediate execution and redirection.

### Tests for User Story 2

- [ ] T009 [P] [US2] Add test for direct Temporal task submission in `tests/unit/api/routers/test_executions.py` (DOC-REQ-004, DOC-REQ-005)
- [ ] T010 [P] [US2] Add test for task creation idempotency in `tests/unit/api/routers/test_executions.py` (DOC-REQ-004, DOC-REQ-005)

### Implementation for User Story 2

- [ ] T011 [US2] Implement logic in `moonmind/workflows/tasks/routing.py` to route new task submissions directly to Temporal if `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` is true (DOC-REQ-004)
- [ ] T012 [US2] Update `/api/executions` POST endpoint in `api_service/api/routers/executions.py` to use direct Temporal creation and enforce idempotency (DOC-REQ-004)

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T013 Run full test suite using `pytest` to validate all changes
- [ ] T014 Run validation of runtime scope with `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete
