# Tasks: Temporal Local Dev Bring-up Path & E2E Test

**Input**: Design documents from `/specs/task/20260308/5e3cb9e8-multi/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Verify Temporal compose components in docker-compose.yaml

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

- [x] T002 Update docker-compose.yaml to configure worker fleets with polling command

---

## Phase 3: User Story 1 - Developer runs local environment (Priority: P1)

**Goal**: Developers need to be able to start the entire MoonMind stack with Temporal using simple commands.

**Independent Test**: Can run docker compose commands to start the stack, and verify that the Temporal server, temporal-ui, and all necessary worker fleets start and enter a polling state.

### Implementation for User Story 1

- [x] T003 [P] [US1] Update docker-compose.yaml to auto-start Temporal worker fleets (Implements DOC-REQ-001)
- [x] T004 [P] [US1] Update docs/Temporal/DeveloperGuide.md with `docker compose up` instructions (Implements DOC-REQ-001)

### Validation for User Story 1

- [x] T005 [US1] Add test case in tests/test_local_dev.py to verify container start and worker polling (Validates DOC-REQ-001)

---

## Phase 4: User Story 2 - End-to-end task execution (Priority: P1)

**Goal**: An automated test script must exist that validates the entire lifecycle of a task through Temporal.

**Independent Test**: Running the E2E test script creates a task, waits for workers to execute it, and checks that artifacts and final status are correct.

### Implementation for User Story 2

- [x] T006 [P] [US2] Implement E2E test script in scripts/test_temporal_e2e.py to submit tasks and monitor progress (Implements DOC-REQ-002)
- [x] T007 [US2] Update scripts/test_temporal_e2e.py to verify artifact storage access and UI status endpoints (Implements DOC-REQ-002)

### Validation for User Story 2

- [x] T008 [US2] Execute scripts/test_temporal_e2e.py against local stack and verify logs in tests/test_e2e_runner.py (Validates DOC-REQ-002)

---

## Phase 5: User Story 3 - Environment teardown and clean state (Priority: P2)

**Goal**: Developers need to be able to reset their environment to a clean state easily.

**Independent Test**: Can stop the environment, clean volumes, restart, or simulate a rollback.

### Implementation for User Story 3

- [x] T009 [P] [US3] Add teardown and rollback steps to docs/Temporal/DeveloperGuide.md (Implements DOC-REQ-003)
- [x] T010 [P] [US3] Create teardown script in scripts/teardown_temporal.py (Implements DOC-REQ-003)

### Validation for User Story 3

- [x] T011 [US3] Create tests/test_teardown.py to verify database and volumes are clean after teardown (Validates DOC-REQ-003)

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T012 Run .specify/scripts/bash/validate-implementation-scope.sh --mode runtime
