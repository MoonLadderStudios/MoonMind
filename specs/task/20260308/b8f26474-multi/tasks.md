# Tasks: Temporal API Consistency

**Input**: Design documents from `/specs/071-temporal-api-consistency/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md

## Phase 1: Setup

**Purpose**: Project initialization and basic structure

- [X] T001 Create testing structure for Temporal execution queries in tests/unit/api/test_executions_temporal.py

## Phase 2: Foundational

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [X] T002 Implement TemporalExecutionRecord mappings for status, rawState, closeStatus, and waitingReason in api_service/core/service.py (DOC-REQ-001, FR-002)

## Phase 3: User Story 1 - Authoritative Task Listing (Priority: P1)

**Goal**: As a Mission Control UI user, I want to see an authoritative list of Temporal tasks so that I can accurately monitor workflow executions with reliable status, state, and waiting reasons.

**Independent Test**: Can be fully tested by starting a Temporal workflow, listing executions with `?source=temporal`, and verifying the returned data precisely matches Temporal visibility/history.

### Tests for User Story 1

- [X] T003 [P] [US1] Write validation tests for /api/executions list endpoint to ensure authoritative data and correct filtering in tests/unit/api/test_executions_temporal.py (DOC-REQ-001, DOC-REQ-003, FR-001, FR-004, FR-005)

### Implementation for User Story 1

- [X] T004 [US1] Implement /api/executions list endpoint to fetch directly from Temporal when source=temporal in api_service/api/routers/executions.py (DOC-REQ-001, FR-001)
- [X] T005 [US1] Implement filtering by workflowType, entry, and state using Temporal Search Attributes in api_service/api/routers/executions.py (DOC-REQ-003, FR-004)

## Phase 4: User Story 2 - Authoritative Task Details (Priority: P1)

**Goal**: As a Mission Control UI user, I want to view accurate details of a specific Temporal task so that I can inspect its complete and current state.

**Independent Test**: Can be fully tested by fetching `/api/executions/{id}` for a workflow with the `mm:` prefix and validating the response payloads against the actual Temporal history.

### Tests for User Story 2

- [X] T006 [P] [US2] Write validation tests for /api/executions/{id} and mm: prefix mapping in tests/unit/api/test_executions_temporal.py (DOC-REQ-001, DOC-REQ-002, FR-001, FR-003, FR-005)

### Implementation for User Story 2

- [X] T007 [US2] Implement mm: workflow ID mapping logic in api_service/api/routers/executions.py (DOC-REQ-002, FR-003)
- [X] T008 [US2] Implement /api/executions/{id} detail endpoint to return authoritative state including waitingReason in api_service/api/routers/executions.py (DOC-REQ-001, FR-001, FR-002)

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T009 Polish error handling for Temporal server unavailability in api_service/api/routers/executions.py

## Dependencies & Execution Order

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Phase 1
- **US1 & US2**: Both depend on Phase 2, can run in parallel.
- **Polish**: Depends on all User Stories completion.
