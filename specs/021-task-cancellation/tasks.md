# Tasks: Agent Queue Task Cancellation

**Input**: Design documents from `/specs/021-task-cancellation/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Include queue repository/service/API, MCP, worker, and dashboard-facing validation tasks per `DOC-REQ-013`.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- `[P]`: Can run in parallel (different files, no dependencies)
- `[Story]`: User story label (`[US1]`, `[US2]`, `[US3]`)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Prepare schema and contract scaffolding for cancellation.

- [X] T001 Add queue cancellation migration for `agent_jobs` metadata fields in `api_service/migrations/versions/202602170001_agent_queue_task_cancellation.py` (DOC-REQ-003)
- [X] T002 [P] Update cancellation API and traceability contracts in `specs/021-task-cancellation/contracts/queue-cancellation.openapi.yaml` and `specs/021-task-cancellation/contracts/requirements-traceability.md` (DOC-REQ-004, DOC-REQ-005)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Add shared queue model/repository/service/router primitives used by all stories.

- [X] T003 Extend queue ORM model fields in `moonmind/workflows/agent_queue/models.py` for cancellation request metadata (DOC-REQ-003)
- [X] T004 [P] Extend queue schema models and request payloads in `moonmind/schemas/agent_queue_models.py` for cancel/ack contracts and serialized job fields (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005)
- [X] T005 Implement repository cancellation primitives (`request_cancel`, `ack_cancel`) and cancellation-aware requeue/retry behavior in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-005, DOC-REQ-006)
- [X] T006 Update queue service cancellation orchestration and event emission in `moonmind/workflows/agent_queue/service.py` (DOC-REQ-001, DOC-REQ-002, DOC-REQ-006, DOC-REQ-007)
- [X] T007 Add queue cancel/ack REST endpoints and exception mapping in `api_service/api/routers/agent_queue.py` (DOC-REQ-004, DOC-REQ-005)

**Checkpoint**: Queue subsystem supports cancellation request + acknowledgement semantics and serialization fields.

---

## Phase 3: User Story 1 - Cancel queued jobs immediately (Priority: P1) 🎯 MVP

**Goal**: Queued jobs can be cancelled immediately and remain unclaimable.

**Independent Test**: Queued cancel request transitions to `cancelled`, emits events, and remains idempotent.

### Tests for User Story 1

- [X] T008 [P] [US1] Add repository tests for queued cancellation, idempotency, and claim exclusion in `tests/unit/workflows/agent_queue/test_repositories.py` (DOC-REQ-001, DOC-REQ-006, DOC-REQ-013)
- [X] T009 [P] [US1] Add service tests for queued cancel event emission and cancellation metadata behavior in `tests/unit/workflows/agent_queue/test_service_hardening.py` (DOC-REQ-001, DOC-REQ-003, DOC-REQ-007, DOC-REQ-013)
- [X] T010 [US1] Add API router tests for `POST /api/queue/jobs/{id}/cancel` status transitions and conflict mapping in `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-004, DOC-REQ-013)

### Implementation for User Story 1

- [X] T011 [US1] Ensure queued cancel path finalizes terminal state and audit events in `moonmind/workflows/agent_queue/service.py` and `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-001, DOC-REQ-007)
- [X] T012 [US1] Ensure retryable failure and lease-expiry paths never resurrect cancel-requested jobs in `moonmind/workflows/agent_queue/repositories.py` (DOC-REQ-006)

**Checkpoint**: P1 behavior complete and independently testable.

---

## Phase 4: User Story 2 - Cancel running jobs cooperatively (Priority: P2)

**Goal**: Running jobs respond to cancellation requests through heartbeat-driven worker cooperation and cancellation acknowledgement.

**Independent Test**: Running cancel requests set metadata, worker detects via heartbeat, stops execution, and acknowledges cancellation.

### Tests for User Story 2

- [X] T013 [P] [US2] Add repository/API tests for running cancel request metadata and `cancel/ack` ownership/state rules in `tests/unit/workflows/agent_queue/test_repositories.py` and `tests/unit/api/routers/test_agent_queue.py` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-005, DOC-REQ-013)
- [X] T014 [P] [US2] Add worker tests for cancellation heartbeat detection, stage-boundary abort, and cancel-ack terminalization in `tests/unit/agents/codex_worker/test_worker.py` (DOC-REQ-010, DOC-REQ-011, DOC-REQ-013)
- [X] T015 [P] [US2] Add command-runner cancellation tests for terminate/kill behavior in `tests/unit/agents/codex_worker/test_handlers.py` (DOC-REQ-012, DOC-REQ-013)

### Implementation for User Story 2

- [X] T016 [US2] Extend worker queue client and heartbeat handling in `moonmind/agents/codex_worker/worker.py` to read cancel request fields and cap heartbeat interval responsiveness (DOC-REQ-010)
- [X] T017 [US2] Implement worker cancellation flow with cancel event checks and `/cancel/ack` invocation in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-011)
- [X] T018 [US2] Make command execution cancellation-aware with cooperative subprocess interruption in `moonmind/agents/codex_worker/handlers.py` (DOC-REQ-012)

**Checkpoint**: Running cancellation is cooperative and worker-owned terminalization is enforced.

---

## Phase 5: User Story 3 - Consistent cancellation across REST, MCP, and dashboard (Priority: P3)

**Goal**: Cancellation is exposed consistently through queue REST, MCP tooling, and dashboard UX.

**Independent Test**: MCP `queue.cancel` and dashboard cancel action produce consistent queue cancellation behavior.

### Tests for User Story 3

- [X] T019 [P] [US3] Add MCP tool registry tests for `queue.cancel` discovery/dispatch in `tests/unit/mcp/test_tool_registry.py` (DOC-REQ-008, DOC-REQ-013)
- [X] T020 [P] [US3] Add dashboard config tests for queue cancellation endpoint exposure in `tests/unit/api/routers/test_task_dashboard_view_model.py` (DOC-REQ-009, DOC-REQ-013)

### Implementation for User Story 3

- [X] T021 [US3] Add `queue.cancel` MCP request model, registration, and handler in `moonmind/mcp/tool_registry.py` (DOC-REQ-008)
- [X] T022 [US3] Expose queue cancel endpoint in runtime dashboard config via `api_service/api/routers/task_dashboard_view_model.py` (DOC-REQ-009)
- [X] T023 [US3] Add queue detail cancel button and cancellation-requested indicator behavior in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-009)

**Checkpoint**: API, MCP, and dashboard all support coherent cancellation behavior.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and scope gate checks.

- [X] T024 Run full cancellation regression validation via `./tools/test_unit.sh` and record outcomes in `specs/021-task-cancellation/quickstart.md` (DOC-REQ-013)
- [X] T025 Run implementation scope gates using `.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime` and `.specify/scripts/bash/validate-implementation-scope.sh --check diff --mode runtime --base-ref origin/main` (DOC-REQ-013)

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 is required before any story implementation.
- User stories execute in priority order; US2 depends on foundational queue changes and queued-cancel primitives from US1.
- US3 can proceed after Phase 2 and may overlap with late US2 testing once cancellation endpoints stabilize.
- Polish tasks run last after implementation and tests are complete.

## Parallel Opportunities

- T002 can run with T001.
- T004 can run with T003 after migration scaffold exists.
- US1 test tasks T008 and T009 can run in parallel; T010 follows endpoint wiring.
- US2 test tasks T013, T014, and T015 can run in parallel once worker/repository changes compile.
- US3 test tasks T019 and T020 can run in parallel with implementation split across MCP/UI config.

## Implementation Strategy

1. Deliver MVP by completing queued cancellation (US1).
2. Add cooperative running cancellation flow (US2).
3. Add MCP and dashboard consistency layer (US3).
4. Run full validation and scope gates before finalizing.
