# Tasks: Agent Queue MVP (Milestone 1)

**Input**: Design documents from `/specs/009-agent-queue-mvp/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Initialize feature scaffolding for queue implementation and tests.

- [X] T001 Verify feature branch and artifacts exist in `specs/009-agent-queue-mvp/plan.md` and `specs/009-agent-queue-mvp/spec.md`.
- [X] T002 Create queue package scaffold in `moonmind/workflows/agent_queue/__init__.py`, `moonmind/workflows/agent_queue/models.py`, `moonmind/workflows/agent_queue/repositories.py`, and `moonmind/workflows/agent_queue/service.py`.
- [X] T003 [P] Create schema/contract scaffold in `moonmind/schemas/agent_queue_models.py` and keep endpoint expectations aligned with `specs/009-agent-queue-mvp/contracts/agent-queue.openapi.yaml`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Deliver queue persistence model and shared service/repository plumbing before user stories.

- [X] T004 Implement `AgentJob` ORM model and queue status enum in `moonmind/workflows/agent_queue/models.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).
- [X] T005 Add Alembic migration for `agent_jobs` in `api_service/migrations/versions/202602130001_agent_queue_mvp.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).
- [X] T006 [P] Register queue model imports in `api_service/db/models.py` so metadata and string relationships resolve (DOC-REQ-001).
- [X] T007 Implement Pydantic request/response/status schemas in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-007).
- [X] T008 Implement queue repository CRUD + lifecycle signatures in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-006).
- [X] T009 Implement queue service transition/ownership validation in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-006).
- [X] T010 [P] Expose queue repository factory in `moonmind/workflows/__init__.py` for router dependency wiring (DOC-REQ-006).

**Checkpoint**: Queue model, migration, and service/repository contracts are ready for API and user story work.

---

## Phase 3: User Story 1 - Queue and Inspect Jobs via REST (Priority: P1) 🎯 MVP

**Goal**: Producers can enqueue jobs and inspect queue state.

**Independent Test**: `POST /api/queue/jobs` creates `queued` records and `GET` endpoints return the created job and filtered list.

### Tests for User Story 1

- [X] T011 [P] [US1] Add repository unit tests for create/get/list behavior in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-010).
- [X] T012 [P] [US1] Add API unit tests for create/get/list endpoints in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010).

### Implementation for User Story 1

- [X] T013 [US1] Implement enqueue/get/list service operations in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-006, DOC-REQ-007).
- [X] T014 [US1] Implement `POST /api/queue/jobs`, `GET /api/queue/jobs`, and `GET /api/queue/jobs/{jobId}` in `api_service/api/routers/agent_queue.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009).
- [X] T015 [US1] Register queue router in `api_service/main.py` and ensure API prefix uses `/api/queue` (DOC-REQ-008).

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Worker Job Lifecycle Control (Priority: P1)

**Goal**: Workers can claim jobs, heartbeat leases, and complete/fail jobs safely.

**Independent Test**: Worker can claim a queued job, extend lease, and transition job to terminal status with ownership validation.

### Tests for User Story 2

- [X] T016 [P] [US2] Add unit tests for claim/heartbeat/complete/fail transitions in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-004, DOC-REQ-005, DOC-REQ-006, DOC-REQ-010).
- [X] T017 [P] [US2] Add API unit tests for `claim`, `heartbeat`, `complete`, and `fail` handlers in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-007, DOC-REQ-009, DOC-REQ-010).

### Implementation for User Story 2

- [X] T018 [US2] Implement transactional claim logic with lease updates in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-004, DOC-REQ-006).
- [X] T019 [US2] Implement expired lease reprocessing policy in `moonmind/workflows/agent_queue/repositories.py` and enforce service-level ownership checks in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-005, DOC-REQ-006).
- [X] T020 [US2] Implement lifecycle endpoints for `claim`, `heartbeat`, `complete`, and `fail` in `api_service/api/routers/agent_queue.py` (DOC-REQ-007, DOC-REQ-009).
- [X] T021 [US2] Implement nullable claim response (`job: null`) and payload validation for worker fields in `moonmind/schemas/agent_queue_models.py` and `api_service/api/routers/agent_queue.py` (DOC-REQ-007).

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Correct Concurrent Claim Behavior (Priority: P2)

**Goal**: Claim operations are deterministic and safe under concurrent workers.

**Independent Test**: Concurrent claims never return the same job twice and expired leases are normalized before claim selection.

### Tests for User Story 3

- [X] T022 [P] [US3] Add concurrent claim safety tests using separate DB sessions in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-004, DOC-REQ-010).
- [X] T023 [P] [US3] Add expired lease normalization tests in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-005, DOC-REQ-010).

### Implementation for User Story 3

- [X] T024 [US3] Apply deterministic claim ordering (`priority DESC`, `created_at ASC`) and optional type filtering in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-004, DOC-REQ-007).
- [X] T025 [US3] Add queue performance indexes supporting claim query patterns in `api_service/migrations/versions/202602130001_agent_queue_mvp.py` (DOC-REQ-004).

**Checkpoint**: User Story 3 is independently functional and testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and quality pass across all user stories.

- [X] T026 [P] Ensure package exports and imports are stable in `moonmind/workflows/agent_queue/__init__.py`, `moonmind/workflows/__init__.py`, and `moonmind/schemas/__init__.py`.
- [X] T027 [P] Reconcile implementation with `specs/009-agent-queue-mvp/contracts/requirements-traceability.md` and update any drift.
- [ ] T028 Run full unit suite via `./tools/test_unit.sh` validating `tests/unit/workflows/agent_queue/test_repositories.py` and `tests/unit/api/routers/test_agent_queue.py` coverage (DOC-REQ-010).

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phase 3/4/5 -> Phase 6.
- User stories begin only after foundational tasks T004-T010 complete.
- Phase 6 runs after all targeted user stories are implemented.

### User Story Dependencies

- US1 starts immediately after Phase 2.
- US2 depends on foundational queue model/repository from Phase 2 and can follow US1.
- US3 depends on claim implementation from US2.

### Parallel Opportunities

- T003, T006, and T010 can run in parallel during setup/foundational work.
- T011/T012 can run in parallel with early US1 implementation.
- T016/T017 and T022/T023 are parallelizable test tasks per story.
- T026/T027 can run in parallel during final polish.

---

## Implementation Strategy

### MVP First (US1)

1. Complete Phase 1 and Phase 2.
2. Deliver US1 enqueue/get/list and validate tests.
3. Confirm queue API baseline before lifecycle complexity.

### Incremental Delivery

1. Add US2 lifecycle transitions and ownership safety.
2. Add US3 concurrency protections and verification.
3. Run full unit test suite and close traceability gaps.

### Runtime Scope Commitments

- Production runtime files are modified in `api_service/`, `moonmind/`, and `api_service/migrations/`.
- Validation coverage is delivered through new unit/contract tests and `./tools/test_unit.sh`.
