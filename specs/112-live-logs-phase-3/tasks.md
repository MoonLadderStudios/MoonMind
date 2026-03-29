# Implementation Tasks: Live Logs Phase 3

**Feature**: Live Logs Phase 3 

## Dependencies

- **US1: Live Streaming Connection (P1)**: Depends on Foundational Phase
- **US2: Resilient Streaming & Disconnects (P1)**: Depends on US1
- **US3: Graceful Artifact Fallback (P2)**: Depends on US1

## Phase 1: Setup

Goal: Initialize files and observability namespace.

- [x] T001 Create missing observability service directory structure in `moonmind/services/observability/`
- [x] T002 Initialize `pytest` integration test directories for SSE in `tests/integration/api/`

## Phase 2: Foundational

Goal: Define Data Models.

- [x] T003 [P] Define `LogStreamEvent` DTO in `moonmind/services/observability/models.py` [DOC-REQ-003, DOC-REQ-008]

## Phase 3: User Story 1 - Live Streaming Connection (P1)

Goal: Operators can open a live stream for an active run.

- [x] T004 [US1] Implement in-memory/Redis `ObservabilityPublisher` with `publish` method in `moonmind/services/observability/publisher.py` [DOC-REQ-002]
- [x] T005 [US1] Implement HTTP log stream proxy generator in `moonmind/services/observability/subscriber.py` [DOC-REQ-001]
- [x] T006 [US1] Expose `GET /api/task-runs/{id}/logs/stream` endpoint in `api_service/api/routers/task_runs.py` integrating the publisher [DOC-REQ-004]
- [x] T007 [P] [US1] Write unit tests asserting standard log publication fan-out in `tests/integration/api/test_live_logs.py` [DOC-REQ-001, DOC-REQ-002, DOC-REQ-003, DOC-REQ-004, DOC-REQ-008]

## Phase 4: User Story 2 - Resilient Streaming & Disconnects (P1)

Goal: Provide robust reconnects and cleanup.

- [x] T008 [US2] Update `subscribe` to parse and apply the `since` sequence query parameter in `moonmind/services/observability/subscriber.py` [DOC-REQ-004, DOC-REQ-005]
- [x] T009 [US2] Detect HTTP disconnects (e.g. `await request.is_disconnected()`) and explicitly close resources in `moonmind/api/routers/task_runs.py` [DOC-REQ-005]
- [x] T010 [US2] Hook stream lifecycle events to update the run observation summary state `last_log_at` in `moonmind/services/observability/publisher.py` [DOC-REQ-006]
- [x] T011 [P] [US2] Write unit tests validating `since` history resumption and disconnect release in `tests/integration/api/test_live_logs.py` [DOC-REQ-004, DOC-REQ-005, DOC-REQ-006]

## Phase 5: User Story 3 - Graceful Artifact Fallback (P2)

Goal: Seamless transition to artifact retrievals.

- [x] T012 [US3] Throw 404 or emit specific error-indicator streams when the run has completed to allow clients to fallback to durable artifacts in `moonmind/api/routers/task_runs.py` [DOC-REQ-007]
- [x] T013 [P] [US3] Write unit tests validating fallback indications on ended tasks in `tests/integration/api/test_live_logs.py` [DOC-REQ-007]

## Phase 6: Polish

Goal: Cleanup and style.

- [x] T014 Run `ruff` and `black` against `moonmind/services/observability` and `api_service/api/routers/task_runs.py`

