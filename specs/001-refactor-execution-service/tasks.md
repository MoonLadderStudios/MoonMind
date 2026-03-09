# Tasks: Refactor Execution Service to Temporal Authority

**Input**: Design documents from `/specs/001-refactor-execution-service/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Prompt B Scope Controls (Step 12/16)

- Runtime implementation tasks are explicitly represented in `T002-T003`, `T006-T008`, and `T011-T014`.
- Runtime validation tasks are explicitly represented in `T004-T005`, `T009-T010`, and `T015-T016`.
- `DOC-REQ-001` through `DOC-REQ-003` implementation + validation coverage is enforced by the per-task tags and the `DOC-REQ Coverage Matrix` in this file, with persistent requirement mapping.
- Deterministic updates across `spec.md`, `plan.md`, and `tasks.md` are required for this remediation step.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Verify project structure and Temporal client dependencies in `api_service` and `moonmind` packages.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T002 Ensure `TemporalExecutionRecord` model in `api_service/db/models.py` acts as a projection/cache.
- [X] T003 Implement `sync_execution_record` utility function to map Temporal visibility/describe output to `TemporalExecutionRecord`.

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Viewing execution lists and details (Priority: P1) 🎯 MVP

**Goal**: Users view their workflow executions on the Mission Control UI and see authoritative data coming directly from Temporal.

**Independent Test**: Create a workflow directly in Temporal and verify it appears correctly in the API list/detail responses without manual local DB inserts.

### Tests for User Story 1

- [X] T004 [P] [US1] Write test for syncing execution details from Temporal in `tests/unit/api/test_execution_service.py` (DOC-REQ-002).
- [X] T005 [P] [US1] Write test for listing executions sourced from Temporal in `tests/unit/api/test_execution_service.py` (DOC-REQ-002).

### Implementation for User Story 1

- [X] T006 [US1] Refactor `describe_execution` in `api_service/services/temporal/service.py` (or `moonmind/workflows/temporal/service.py`) to query Temporal via `TemporalClientAdapter` and update DB cache before returning. (DOC-REQ-001, DOC-REQ-002)
- [X] T007 [US1] Refactor `list_executions` in `api_service/services/temporal/service.py` to fetch visibility from Temporal or use updated projection. (DOC-REQ-001, DOC-REQ-002)
- [X] T008 [US1] Update `api_service/api/routers/executions.py` to handle Temporal-driven responses for list and detail endpoints. (DOC-REQ-002)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Triggering execution actions (Priority: P1)

**Goal**: Users perform actions on an execution (cancel, pause, resume, signal, update) through the UI, routed directly to the Temporal workflow.

**Independent Test**: Issue an API cancel request and verify that the Temporal workflow receives the cancellation signal before the local database state is updated.

### Tests for User Story 2

- [X] T009 [P] [US2] Write test for routing cancel action to Temporal in `tests/unit/api/test_execution_service.py` (DOC-REQ-001).
- [X] T010 [P] [US2] Write test for action validation relying on Temporal in `tests/unit/api/test_execution_service.py` (DOC-REQ-003).

### Implementation for User Story 2

- [X] T011 [US2] Refactor `cancel_execution` in `api_service/services/temporal/service.py` to call `workflow_handle.cancel()` via `TemporalClientAdapter`. (DOC-REQ-001)
- [X] T012 [US2] Refactor `signal_execution` and update methods in `api_service/services/temporal/service.py` to route actions (pause, resume) to `workflow_handle.signal()`. (DOC-REQ-001)
- [X] T013 [US2] Remove local DB pre-validation logic for signals in `api_service/services/temporal/service.py`, relying entirely on Temporal workflow validation. Catch Temporal exceptions and map them to API errors. (DOC-REQ-003)
- [X] T014 [US2] Update `api_service/api/routers/executions.py` endpoints for actions to handle Temporal validation errors correctly. (DOC-REQ-003)

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T015 Run quickstart.md validation to test the endpoints and actions manually via UI/CLI.
- [X] T016 Execute automated tests (`pytest`) and verify requirements coverage (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2)
- **User Story 2 (P1)**: Can start after Foundational (Phase 2) - relies on US1 for verification visibility

### Parallel Opportunities

- Tests within US1 and US2 can be run in parallel.
- US1 and US2 implementation tasks can be parallelized since one deals with reads and the other with writes/signals, though US2 tests better with US1 completed.

---

## Quality Gates

1. Runtime tasks gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime`
2. Runtime diff gate: `./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime`
3. Unit/integration gate: `./tools/test_unit.sh`
4. Traceability gate: each `DOC-REQ-*` has at least one implementation task and one validation task.
5. Prompt B runtime gate: runtime implementation + validation task coverage must remain explicit and deterministic across `spec.md`, `plan.md`, and `tasks.md`.

## Task Summary

- Total tasks: **16**
- Story task count: **US1 = 5**, **US2 = 6**
- Parallelizable tasks (`[P]`): **4**
- Suggested MVP scope: **through Phase 3 (US1)**
- Checklist format validation: **all tasks follow `- [ ] T### [P?] [US?] ...` with explicit path references**

## DOC-REQ Coverage Matrix (Implementation + Validation)

| DOC-REQ | Implementation Task(s) | Validation Task(s) |
| --- | --- | --- |
| DOC-REQ-001 | T006, T007, T011, T012 | T009, T016 |
| DOC-REQ-002 | T002, T003, T006, T007, T008 | T004, T005, T016 |
| DOC-REQ-003 | T013, T014 | T010, T016 |

Coverage rule: do not close implementation until every `DOC-REQ-*` row keeps both implementation and validation coverage.
