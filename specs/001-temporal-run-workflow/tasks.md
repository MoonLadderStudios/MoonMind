# Tasks: Temporal Run Workflow

**Input**: Design documents from `/specs/001-temporal-run-workflow/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/requirements-traceability.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Verify Temporal SDK is configured and `moonmind/workflows/temporal/workflows/run.py` is present

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [x] T002 Define `RunWorkflowInput` and `RunWorkflowOutput` data structures in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-005)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Create and Execute Run Workflow (Priority: P1) 🎯 MVP

**Goal**: The "Run" API triggers a real Temporal workflow so that the execution is durable, observable, and fully orchestrated by Temporal.

**Independent Test**: Can be independently tested by triggering a new run via the API and observing a new execution appearing in the Temporal Web UI with the correct phases and terminal status.

### Tests for User Story 1 (OPTIONAL)

- [x] T003 [P] [US1] Write unit test skeleton for Temporal workflow start and execution in `tests/unit/workflows/temporal/workflows/test_run.py` (DOC-REQ-005)

### Implementation for User Story 1

- [x] T013 [US1] Configure Temporal RetryPolicies with exponential backoff for all activity executions in `moonmind/workflows/temporal/workflows/run.py` (FR-006)
- [x] T014 [US1] Offload large payloads to the artifact store and store only references in `moonmind/workflows/temporal/workflows/run.py` (FR-007)
- [x] T004 [US1] Implement Temporal client `start_workflow` capability in `moonmind/workflows/temporal/client.py` to trigger workflow (DOC-REQ-001, DOC-REQ-005)
- [x] T005 [US1] Update `POST /api/executions` to use the Temporal client in `api_service/api/routers/executions.py` (DOC-REQ-001, DOC-REQ-005)
- [x] T006 [US1] Implement `MoonMindRunWorkflow` states (initializing, planning, executing) in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-002, DOC-REQ-005)
- [x] T007 [US1] Implement terminal state closures (Success/Failure) correctly in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-003, DOC-REQ-005)
- [x] T008 [US1] Integrate `workflow.upsert_search_attributes` to reflect runtime metadata in `moonmind/workflows/temporal/workflows/run.py` (DOC-REQ-004, DOC-REQ-005)
- [x] T009 [US1] Write unit tests verifying phase transitions and search attributes in `tests/unit/workflows/temporal/workflows/test_run.py` (DOC-REQ-002, DOC-REQ-004, DOC-REQ-005)
- [x] T010 [US1] Write unit tests validating terminal states in `tests/unit/workflows/temporal/workflows/test_run.py` (DOC-REQ-003, DOC-REQ-005)
- [x] T011 [US1] Write unit tests validating the Run API triggers a Temporal workflow execution in `tests/unit/api/routers/test_executions.py` (DOC-REQ-001, DOC-REQ-005)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T012 Run code formatting, type checking, and test validation on modified python files

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup
- **User Stories (Phase 3+)**: All depend on Foundational
- **Polish (Final Phase)**: Depends on all user stories

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational

### Parallel Opportunities

- Tests (T003) can be written in parallel with T002.

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and 2
2. Complete Phase 3: User Story 1
3. **STOP and VALIDATE**: Test User Story 1 via API and Temporal Web UI
4. Polish and finalize
