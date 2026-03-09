# Tasks: Temporal Migration Task 5.13 (Local Dev Bring-up & E2E Test)

**Input**: Design documents from `/specs/071-temporal-migration-5-13/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Ensure scripts directory exists at scripts/

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

*(No specific foundational tasks block User Story 1 in this feature)*

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Local Development Bring-up (Priority: P1)

**Goal**: Developers need a seamless way to start the local Temporal environment and MoonMind workers so they can test workflow changes without manual setup steps.

**Independent Test**: Can be fully tested by running a single docker-compose command and verifying all services (Temporal, Postgres, workers) are healthy.

### Implementation for User Story 1

- [X] T002 [US1] Update docker-compose.yaml to include Temporal server, Postgres, MoonMind API, and worker fleets for DOC-REQ-001
- [X] T003 [US1] Validate local environment start with docker compose up in docker-compose.yaml to verify DOC-REQ-001

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Automated End-to-End Task Validation (Priority: P1)

**Goal**: The system requires an automated E2E test to prove that tasks are properly orchestrated by Temporal, from creation to final artifact generation, including UI status updates.

**Independent Test**: Can be tested by running the E2E test script against a running local environment.

### Implementation for User Story 2

- [X] T004 [P] [US2] Implement E2E test script in scripts/temporal_e2e_test.py to submit and monitor tasks for DOC-REQ-002
- [X] T005 [US2] Execute pytest on scripts/temporal_e2e_test.py and tests/ to verify task orchestration for DOC-REQ-002

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Environment Rollback and State Cleaning (Priority: P2)

**Goal**: Developers and CI systems need to reset the Temporal state between test runs or roll back changes safely.

**Independent Test**: Run a test, clean the state, and verify the next test run starts from a blank slate.

### Implementation for User Story 3

- [X] T006 [P] [US3] Implement cleanup script in scripts/temporal_clean_state.sh to reset environment for DOC-REQ-003
- [X] T007 [US3] Run scripts/temporal_clean_state.sh and verify environment is clean for DOC-REQ-003

**Checkpoint**: All user stories should now be independently functional

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T008 [P] Update README.md with local dev bring-up instructions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: No dependencies on other stories
- **User Story 2 (P1)**: Depends on US1 (needs running environment)
- **User Story 3 (P2)**: Depends on US1 (needs running environment)

### Parallel Opportunities

- US2 and US3 implementation tasks can be written in parallel (T004 and T006).

## Parallel Example: User Story 2 & 3

```bash
# Launch implementation of E2E test and cleanup script together:
Task: "Implement E2E test script in scripts/temporal_e2e_test.py to submit and monitor tasks for DOC-REQ-002"
Task: "Implement cleanup script in scripts/temporal_clean_state.sh to reset environment for DOC-REQ-003"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 3: User Story 1
2. **STOP and VALIDATE**: Test User Story 1 independently

### Incremental Delivery

1. Add User Story 1 → Test independently
2. Add User Story 2 → Test independently
3. Add User Story 3 → Test independently