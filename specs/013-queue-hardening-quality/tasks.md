# Tasks: Agent Queue Hardening and Quality (Milestone 5)

**Input**: Design documents from `/specs/013-queue-hardening-quality/`  
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare Milestone 5 scaffolding for schema/runtime hardening work.

- [X] T001 Verify branch `013-queue-hardening-quality` and feature artifacts exist in `specs/013-queue-hardening-quality/`.
- [X] T002 Create/confirm test package scaffolding for hardening coverage in `tests/unit/workflows/agent_queue/` and `tests/unit/api/routers/`.
- [X] T003 [P] Add initial worker hardening test scaffolding in `tests/unit/agents/codex_worker/test_worker.py` for event/auth updates.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared schema/runtime primitives required by all user stories.

- [X] T004 Extend queue ORM models for `dead_letter`, `next_attempt_at`, `agent_job_events`, and `agent_worker_tokens` in `moonmind/workflows/agent_queue/models.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-008).
- [X] T005 Add Alembic migration for hardening schema updates in `api_service/migrations/versions/202602140001_agent_queue_hardening.py` (DOC-REQ-002, DOC-REQ-005, DOC-REQ-008).
- [X] T006 Update queue API schemas for worker capabilities, retry scheduling, and event payloads in `moonmind/schemas/agent_queue_models.py` (DOC-REQ-004, DOC-REQ-006, DOC-REQ-007, DOC-REQ-008).
- [X] T007 [P] Update queue package exports and wiring for new models/services in `moonmind/workflows/agent_queue/__init__.py` and `moonmind/workflows/__init__.py` (DOC-REQ-002, DOC-REQ-005).
- [X] T008 Add worker auth context/dependency primitives in `api_service/api/routers/agent_queue.py` (DOC-REQ-001, DOC-REQ-002).

**Checkpoint**: Hardening schema primitives and shared types are in place.

---

## Phase 3: User Story 1 - Enforce Worker Identity and Policy (Priority: P1) 🎯 MVP

**Goal**: Worker endpoints enforce OIDC/token identity with repository/job-type/capability policy checks.

**Independent Test**: Claim/mutation endpoints only accept authorized worker identity and return jobs matching worker policy.

### Tests for User Story 1

- [X] T009 [P] [US1] Add repository/service tests for worker token lookup, inactive-token rejection, and policy normalization in `tests/unit/workflows/agent_queue/test_service_hardening.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).
- [X] T010 [P] [US1] Add API tests for worker-auth enforcement and worker-id mismatch handling in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-001, DOC-REQ-002).
- [X] T011 [P] [US1] Add claim filtering tests for repository allowlist, job-type allowlist, and required capabilities in `tests/unit/workflows/agent_queue/test_repositories.py` and `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-003, DOC-REQ-004, DOC-REQ-009).

### Implementation for User Story 1

- [X] T012 [US1] Implement worker token hashing/lookup and policy read methods in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-002, DOC-REQ-003).
- [X] T013 [US1] Implement worker-auth dependency and policy-aware claim/mutation guards (including artifact upload auth path) in `api_service/api/routers/agent_queue.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-010).
- [X] T014 [US1] Implement capability-aware claim selection path in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-004, DOC-REQ-009).
- [X] T015 [US1] Add minimal worker token admin endpoints (`create/list/revoke`) in `api_service/api/routers/agent_queue.py` and `moonmind/schemas/agent_queue_models.py` (DOC-REQ-002).

**Checkpoint**: Worker claims/mutations are authenticated and policy constrained.

---

## Phase 4: User Story 2 - Retry Backoff and Dead-Letter Semantics (Priority: P1)

**Goal**: Retryable failures back off before requeue and exhausted retries become dead-letter.

**Independent Test**: Retryable failures schedule delayed requeue; exhaustion transitions to dead-letter and prevents further claim.

### Tests for User Story 2

- [X] T016 [P] [US2] Add repository tests for `next_attempt_at` eligibility filtering and lease-expiry retry behavior in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-007, DOC-REQ-008, DOC-REQ-009).
- [X] T017 [P] [US2] Add service/router tests for dead-letter responses and retry metadata serialization in `tests/unit/workflows/agent_queue/test_service_hardening.py` and `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-007, DOC-REQ-008).

### Implementation for User Story 2

- [X] T018 [US2] Implement exponential backoff scheduling and delayed claim eligibility in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-007).
- [X] T019 [US2] Implement `dead_letter` terminal transition for retry exhaustion and expired leases in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/models.py` (DOC-REQ-008, DOC-REQ-009).
- [X] T020 [US2] Propagate retry/dead-letter fields through REST/MCP serializations in `moonmind/schemas/agent_queue_models.py`, `moonmind/mcp/tool_registry.py`, and `api_service/api/routers/mcp_tools.py` (DOC-REQ-007, DOC-REQ-008).

**Checkpoint**: Retry lifecycle is deterministic with explicit dead-letter handling.

---

## Phase 5: User Story 3 - Job Events and Streaming-ish Logs (Priority: P2)

**Goal**: Queue system stores append-only events and exposes incremental polling for progress/log updates.

**Independent Test**: Event append/list APIs and worker lifecycle emissions provide ordered incremental progress entries.

### Tests for User Story 3

- [X] T021 [P] [US3] Add repository/service tests for append-only event writes and `after` cursor filtering in `tests/unit/workflows/agent_queue/test_service_hardening.py` (DOC-REQ-005, DOC-REQ-006).
- [X] T022 [P] [US3] Add API tests for event append/list endpoints and payload validation in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-005, DOC-REQ-006).
- [X] T023 [P] [US3] Add worker loop tests for lifecycle event emission via queue client in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-006).
- [X] T024 [P] [US3] Add artifact upload auth regression tests ensuring validation behavior remains enforced with worker auth in `tests/unit/api/routers/test_agent_queue_artifacts.py` (DOC-REQ-010).

### Implementation for User Story 3

- [X] T025 [US3] Implement event append/list repository and service methods in `moonmind/workflows/agent_queue/repositories.py` and `moonmind/workflows/agent_queue/service.py` (DOC-REQ-005, DOC-REQ-006).
- [X] T026 [US3] Add queue event endpoints (`POST/GET /api/queue/jobs/{jobId}/events`) and error mapping in `api_service/api/routers/agent_queue.py` (DOC-REQ-005, DOC-REQ-006).
- [X] T027 [US3] Update worker queue client/loop to emit lifecycle events and include worker capabilities in claim payloads in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-004, DOC-REQ-006).

**Checkpoint**: Events/log polling and worker lifecycle telemetry are available.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Reconcile traceability and run final validation gates.

- [X] T028 [P] Reconcile final `DOC-REQ-*` mappings against `specs/013-queue-hardening-quality/contracts/requirements-traceability.md` and update drift.
- [X] T029 [P] Update operator docs for worker token usage, policy fields, and event polling in `docs/CodexTaskQueue.md` and `specs/013-queue-hardening-quality/quickstart.md` (DOC-REQ-001, DOC-REQ-006).
- [X] T030 [P] Verify worker token transport/header behavior in integration docs and worker config notes in `docs/CodexCliWorkers.md` (DOC-REQ-001, DOC-REQ-002).
- [ ] T031 Run unit validation via `./tools/test_unit.sh` including queue hardening tests.

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 -> Phase 2 -> Phase 3/4/5 -> Phase 6.
- All user stories depend on foundational tasks T004-T008.
- US2 depends on lifecycle updates from US1 claim/auth surfaces.

### User Story Dependencies

- US1 is the MVP and has no dependency on US2/US3.
- US2 depends on US1 policy-aware claim flow.
- US3 depends on US1 worker auth context and foundational schema updates.

### Parallel Opportunities

- T003, T007 run in parallel during setup/foundational work.
- T009-T011, T016-T017, T021-T024 are parallelizable test tasks.
- T028-T030 can run in parallel during polish.

---

## Implementation Strategy

### MVP First (US1)

1. Enforce worker identity and token policy on queue mutation endpoints.
2. Deliver policy-aware claim filtering for repos, job types, and capabilities.
3. Validate with dedicated API/repository tests.

### Incremental Delivery

1. Add retry backoff + dead-letter state transitions (US2).
2. Add append-only events and polling endpoints plus worker lifecycle event emission (US3).
3. Reconcile docs and run full unit validation.

### Runtime Scope Commitments

- Production runtime files will be modified in `api_service/`, `moonmind/workflows/agent_queue/`, and `moonmind/agents/codex_worker/`.
- Validation coverage will be delivered with unit tests plus execution of `./tools/test_unit.sh`.
