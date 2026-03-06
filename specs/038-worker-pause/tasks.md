# Tasks: Worker Pause System

**Input**: Design documents from `/specs/034-worker-pause/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Run `./tools/test_unit.sh` to capture baseline API/worker/dashboard status before applying Worker Pause changes (`./tools/test_unit.sh`).

---

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T002 Create `SystemWorkerPauseState` and `SystemControlEvent` SQLAlchemy models in `moonmind/workflows/agent_queue/models.py` (DOC-REQ-001, DOC-REQ-002).
- [ ] T003 Generate Alembic migration `api_service/migrations/versions/20260220_worker_pause.py` that creates the new tables with defaults/indexes (DOC-REQ-001, DOC-REQ-002).
- [ ] T004 Add repository helpers in `moonmind/workflows/agent_queue/repositories.py` to load/update pause state, append control events, compute queue metrics snapshots (`queued`, `running`, `staleRunning`), and expose a cached read helper keyed by `version` for low-latency guards (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).
- [ ] T005 Add repository tests in `tests/unit/workflows/agent_queue/test_repositories_worker_pause.py` covering singleton creation, version increments, and control event persistence (DOC-REQ-001, DOC-REQ-002).

---

## Phase 3: User Story 1 - Pause for upgrades (Priority: P1) 🎯 MVP

**Goal**: Block new queue claims on demand and expose pause metadata via REST so operators can enter Drain mode safely.

**Independent Test**: Trigger `POST /api/system/worker-pause` (Drain) and confirm claim responses immediately return `{job:null, system:{...}}` while `_requeue_expired_jobs` is never invoked.

### Implementation for User Story 1

- [ ] T006 [US1] Update `moonmind/workflows/agent_queue/service.py` to return a structured `WorkerPauseStatus` payload, short-circuit `claim_job` when paused, and reuse an in-memory pause cache that refreshes whenever the stored `version` changes (DOC-REQ-004).
- [ ] T007 [US1] Implement `api_service/api/schemas/system_worker_pause.py` and router `api_service/api/routers/system_worker_pause.py` to handle `GET/POST /api/system/worker-pause` with validation + actor metadata wiring (DOC-REQ-003).
- [ ] T008 [US1] Extend `moonmind/schemas/agent_queue_models.py` plus `api_service/api/routers/agent_queue.py` to include a `system` block on claim/heartbeat responses and keep responses backward-compatible (DOC-REQ-005, DOC-REQ-009).

### Validation for User Story 1

- [ ] T009 [P] [US1] Create `tests/unit/api/routers/test_system_worker_pause.py` covering pause/resume actions, required reason/mode validation, and drain metrics in responses (DOC-REQ-003).
- [ ] T010 [US1] Add `tests/unit/workflows/agent_queue/test_service_worker_pause.py` verifying `AgentQueueService.claim_job` bypasses repository work while paused (DOC-REQ-004).
- [ ] T011 [P] [US1] Add `tests/unit/api/routers/test_agent_queue_pause_metadata.py` asserting claim and heartbeat responses expose the `system` object with correct aliases (DOC-REQ-005, DOC-REQ-009).

---

## Phase 4: User Story 2 - Resume after maintenance (Priority: P2)

**Goal**: Surface pause/resume controls, drain metrics, and audit history through the dashboard, API, and MCP adapters so operators can safely resume work.

**Independent Test**: Use the dashboard Pause/Resume button to toggle states, observe the global banner + drain progress, and confirm MCP `queue.claim` receives matching pause metadata.

### Implementation for User Story 2

- [ ] T012 [US2] Enrich `api_service/api/routers/system_worker_pause.py` GET handler with WorkerPauseMetrics (`queued`, `running`, `staleRunning`, `isDrained`), include latest audit entries, and surface the current pause `version` so dashboards can poll intelligently (DOC-REQ-003, DOC-REQ-002).
- [ ] T013 [US2] Update `api_service/api/routers/task_dashboard_view_model.py` to inject worker pause endpoints + poll intervals into the dashboard runtime config (DOC-REQ-008).
- [ ] T014 [US2] Enhance `api_service/static/task_dashboard/dashboard.js` and `dashboard.css` to render the Workers badge, drain panel, and Pause/Resume form that calls `/api/system/worker-pause` (DOC-REQ-008).
- [ ] T015 [US2] Extend `moonmind/mcp/tool_registry.py` (and any helper models) so `queue.claim` and `queue.heartbeat` tool responses forward the `system` metadata inline (DOC-REQ-009).

### Validation for User Story 2

- [ ] T016 [P] [US2] Add Node-based dashboard unit tests (e.g., `tests/task_dashboard/test_worker_pause_banner.js`) validating banner state + control wiring (DOC-REQ-008).
- [ ] T017 [US2] Extend `tests/unit/workflows/agent_queue/test_repositories_worker_pause.py` to assert control events are written for both pause and resume transitions (DOC-REQ-002).
- [ ] T018 [US2] Update `tests/unit/mcp/test_tool_registry.py` to verify MCP `queue.claim`/`queue.heartbeat` results now include `system` metadata (DOC-REQ-009).

---

## Phase 5: User Story 3 - Quiesce running jobs (Priority: P3)

**Goal**: Ensure workers idle politely while paused and honor Quiesce mode checkpoints so leases stay healthy without new work starting.

**Independent Test**: With Quiesce enabled, verify workers log the pause reason, sleep using the pause poll interval, continue heartbeating, and resume automatically once the system unpauses.

### Implementation for User Story 3

- [ ] T019 [US3] Enhance `moonmind/agents/codex_worker/worker.py` `QueueApiClient.claim_job`/`heartbeat` methods to parse `system` metadata, adjust polling via `pause_poll_interval_ms`, and log once per `version` (DOC-REQ-006).
- [ ] T020 [US3] Update `_heartbeat_loop` and `_wait_if_paused` in `moonmind/agents/codex_worker/worker.py` so Quiesce mode pauses between stage/step checkpoints while continuing heartbeats (DOC-REQ-007).
- [ ] T021 [US3] Wire pause awareness into the main run loop (sleep/backoff + reason logging) so drained workers idle without errors in `moonmind/agents/codex_worker/worker.py` (DOC-REQ-006).

### Validation for User Story 3

- [ ] T022 [P] [US3] Add `tests/unit/agents/codex_worker/test_worker.py` coverage proving paused claim responses trigger the new idle loop + logging cadence (DOC-REQ-006).
- [ ] T023 [US3] Extend `tests/unit/agents/codex_worker/test_worker.py` to cover Quiesce checkpoints and confirm `_wait_if_paused` unblocks once heartbeat metadata clears (DOC-REQ-007).

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T024 Document the Pause → Drain → Upgrade → Resume workflow updates in `specs/034-worker-pause/quickstart.md` and ensure `docs/WorkerPauseSystem.md` reflects the implemented API details.
- [ ] T025 Run the full test suite via `./tools/test_unit.sh` and capture results for the implementation report.
- [ ] T026 Add StatsD/observability hooks plus structured logging for pause/resume transitions (state gauge + action counters) inside `system_worker_pause` router and queue service so operators can alert on long-lived pauses (NFR-002, FR-010).
- [ ] T027 Extend existing API/service tests (or new dedicated tests) to assert instrumentation hooks execute without raising errors by injecting mock StatsD/log handlers (NFR-002, FR-010).

---

## Dependencies & Execution Order

- Setup (Phase 1) must complete before modifying the schema so baseline failures are known.
- Foundational (Phase 2) blocks all user stories because the table + repository helpers are required everywhere.
- User Story 1 (Pause) depends on Phase 2 and enables the primary MVP toggle; it must ship before US2/US3.
- User Story 2 (Resume/UI/MCP) depends on US1 but can proceed in parallel with US3 once the guard + metadata schema exist.
- User Story 3 (Quiesce) depends on US1 (system metadata) but not US2 (dashboard).
- Polish tasks run after all desired stories are complete.

## Parallel Opportunities

1. After completing Foundational tasks, US1 service/router work (T006–T008) can run in parallel with dashboard contract planning because they touch different directories.
2. In US2, dashboard JS (T014) and MCP tooling (T015) are independent and marked `[P]` tasks T016/T018 for testing can execute concurrently once implementations exist.
3. In US3, worker idle-loop enhancements (T019, T021) and Quiesce checkpoint logic (T020) can progress simultaneously on separate helper methods, with validation tasks T022 and T023 split across different test cases.
