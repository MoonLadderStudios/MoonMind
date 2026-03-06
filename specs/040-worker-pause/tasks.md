# Tasks: Worker Pause System

**Input**: Design documents from `/specs/035-worker-pause/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm feature context + tooling before touching runtime code

- [X] T001 Run `.specify/scripts/bash/check-prerequisites.sh --json` to confirm `FEATURE_DIR=/specs/035-worker-pause` artifacts exist before editing runtime files (DOC-REQ-001 readiness).
- [X] T002 Review `docs/WorkerPauseSystem.md` against `specs/035-worker-pause/spec.md` and record clarifications in `specs/035-worker-pause/checklists/worker-pause.md` so acceptance criteria cover DOC-REQ-001–DOC-REQ-010.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Persist pause state + shared schemas required by every story

- [X] T003 Add `SystemWorkerPauseState` and `SystemControlEvent` SQLAlchemy models inside `moonmind/workflows/agent_queue/models.py` with enums, FK to `user.id`, and monotonic `version` (DOC-REQ-002, DOC-REQ-003).
- [X] T004 Create Alembic migration `api_service/migrations/versions/202602200001_worker_pause_system.py` to create both tables, seed `id=1` state row, and add audit indexes (DOC-REQ-002, DOC-REQ-003).
- [X] T005 Extend `moonmind/workflows/agent_queue/repositories.py` with helpers (`get_pause_state_for_update`, `update_pause_state`, `append_system_control_event`, `fetch_worker_pause_metrics`) that avoid `_requeue_expired_jobs` (DOC-REQ-002, DOC-REQ-003, DOC-REQ-010).
- [X] T006 Implement `WorkerPauseMetrics` + `QueueSystemMetadata` dataclasses and snapshot/toggle helpers in `moonmind/workflows/agent_queue/service.py` so callers always receive system metadata (DOC-REQ-001, DOC-REQ-005, DOC-REQ-010).
- [X] T007 Define shared Pydantic schemas (`QueueSystemMetadataModel`, `WorkerPauseSnapshotResponse`) inside `api_service/api/schemas.py` for HTTP/MCP serialization (DOC-REQ-005, DOC-REQ-006).

---

## Phase 3: User Story 1 – Pause Workers for Upgrades (Priority: P1) 🎯 MVP

**Goal**: Give operators a POST control that pauses new claims immediately, skips `_requeue_expired_jobs`, and emits audit entries while workers observe `system` metadata.
**Independent Test**: Pause via API, assert `/api/queue/jobs/claim` returns `{job:null, system:{workersPaused:true}}` without repository calls, resume and confirm claims restart with audit entries recorded.

### Tests for User Story 1 (MANDATORY)

- [X] T008 [P] [US1] Add singleton + audit tests in `tests/unit/workflows/agent_queue/test_repositories.py` covering state seeding, `version` increments, and `SystemControlEvent` writes (DOC-REQ-001, DOC-REQ-002, DOC-REQ-003).
- [X] T009 [P] [US1] Extend `tests/unit/api/routers/test_agent_queue.py` to prove claim guard short-circuits and never calls `_requeue_expired_jobs` when `workersPaused=true` (DOC-REQ-004, DOC-REQ-005).
- [X] T010 [P] [US1] Create `tests/unit/api/routers/test_system_worker_pause.py` POST cases for reason/mode validation, audit fan-out, and conflict handling (DOC-REQ-001, DOC-REQ-006, DOC-REQ-009).

### Implementation for User Story 1

- [X] T011 [US1] Implement pause-aware claim/toggle logic in `moonmind/workflows/agent_queue/service.py` so paused states short-circuit before repository claims and emit structured metadata (DOC-REQ-001, DOC-REQ-004, DOC-REQ-005).
- [X] T012 [P] [US1] Update `api_service/api/routers/agent_queue.py` (plus any serializers) to attach the `system` block onto claim responses while keeping payload compatibility (DOC-REQ-004, DOC-REQ-005).
- [X] T013 [US1] Build `POST /api/system/worker-pause` inside `api_service/api/routers/system_worker_pause.py` with reason + mode validation, audit append, and service wiring (DOC-REQ-001, DOC-REQ-003, DOC-REQ-006).
- [X] T014 [US1] Register the new router in `api_service/main.py` with operator auth dependencies and ensure OpenAPI/ACL metadata reflects the pause control (DOC-REQ-001).

---

## Phase 4: User Story 2 – Monitor Drain Progress and Resume Safely (Priority: P2)

**Goal**: Surface GET + dashboard telemetry (queued/running/stale, `isDrained`, audit history) plus UI warnings so operators know when resuming is safe.
**Independent Test**: Pause the system, poll `/api/system/worker-pause` until `isDrained=true`, verify dashboard banner updates counts + warnings, then resume with confirmation when `isDrained=false`.

### Tests for User Story 2

- [X] T015 [P] [US2] Expand `tests/unit/api/routers/test_system_worker_pause.py` with GET coverage verifying metrics counts, `isDrained`, and `forceResume` warning payloads (DOC-REQ-006, DOC-REQ-010).
- [X] T016 [P] [US2] Update `tests/unit/api/routers/test_task_dashboard_view_model.py` to assert workerPause endpoint config + polling interval are emitted (DOC-REQ-008, DOC-REQ-009).
- [X] T017 [P] [US2] Add dashboard banner interaction tests in `tests/task_dashboard/test_worker_pause_banner.js` for Pause/Resume flows and isDrained confirmation dialogs (DOC-REQ-008, DOC-REQ-009).

### Implementation for User Story 2

- [X] T018 [US2] Complete `GET /api/system/worker-pause` in `api_service/api/routers/system_worker_pause.py` to aggregate metrics, audit history, and drain booleans (DOC-REQ-006, DOC-REQ-010).
- [X] T019 [P] [US2] Inject worker pause endpoint URLs + polling intervals into `api_service/api/routers/task_dashboard_view_model.py` for dashboard consumption (DOC-REQ-008, DOC-REQ-009).
- [X] T020 [P] [US2] Add the global worker banner + Pause/Resume form markup to `api_service/templates/task_dashboard.html`, ensuring auth tokens flow into the form (DOC-REQ-008).
- [X] T021 [P] [US2] Implement polling, POST submission, warnings, and styling inside `api_service/static/task_dashboard/dashboard.js` and `api_service/static/task_dashboard/dashboard.css` (DOC-REQ-008, DOC-REQ-009, DOC-REQ-010).
- [X] T022 [US2] Document Pause → Drain → Upgrade → Resume drills (plus dashboard guidance) in `specs/035-worker-pause/quickstart.md` (DOC-REQ-009, DOC-REQ-010).

---

## Phase 5: User Story 3 – Pause Running Jobs at Checkpoints (Quiesce Mode) (Priority: P3)

**Goal**: Teach workers + MCP tooling to honor `system.mode="quiesce"`, pause at checkpoints without dropping leases, and resume automatically when version changes.
**Independent Test**: Trigger Quiesce via API, observe worker logs showing checkpoint pause + continued heartbeats, then resume and verify execution restarts without duplicate steps.

### Tests for User Story 3

- [X] T023 [P] [US3] Add worker pause/quiesce unit tests in `tests/unit/agents/codex_worker/test_worker.py` covering poll intervals, version logging, and checkpoint events (DOC-REQ-007, DOC-REQ-009).
- [X] T024 [P] [US3] Extend `tests/unit/mcp/test_tool_registry.py` so `queue.claim`/`queue.heartbeat` responses expose the `system` metadata (DOC-REQ-005).
- [X] T025 [P] [US3] Enhance `tests/unit/api/routers/test_agent_queue.py` heartbeat cases to ensure running jobs receive `system` metadata + quiesce instructions (DOC-REQ-005, DOC-REQ-009).

### Implementation for User Story 3

- [X] T026 [US3] Update `moonmind/agents/codex_worker/worker.py` with `pause_poll_interval_ms`, `last_pause_version_logged`, and checkpoint pausing that leverages `system` metadata (DOC-REQ-005, DOC-REQ-007, DOC-REQ-009).
- [X] T027 [P] [US3] Return the `system` block on heartbeat/job payloads via `moonmind/workflows/agent_queue/service.py` and `api_service/api/routers/agent_queue.py`, including quiesce flags for running work (DOC-REQ-005, DOC-REQ-007).
- [X] T028 [P] [US3] Thread `system` metadata through `moonmind/mcp/tool_registry.py` queue tools so IDE clients honor pauses (DOC-REQ-005).
- [X] T029 [US3] Emit structured logs + metrics for pause/resume transitions and guard hits inside `moonmind/workflows/agent_queue/service.py` and `api_service/api/routers/system_worker_pause.py` (DOC-REQ-001, DOC-REQ-007, DOC-REQ-010).

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final documentation, validation, and manual drills across stories

- [X] T030 Run `./tools/test_unit.sh` to execute Python + dashboard suites and capture logs for the Worker Pause feature (DOC-REQ-001–DOC-REQ-010 validation gate).
- [X] T031 [P] Refresh `docs/WorkerPauseSystem.md` with the implemented API/UI behavior, linking to metrics fields and MCP propagation (DOC-REQ-001, DOC-REQ-009).
- [X] T032 Verify Pause → Drain → Upgrade → Resume plus Quiesce workflows manually using `specs/035-worker-pause/quickstart.md` and record results in `specs/035-worker-pause/checklists/worker-pause.md` (DOC-REQ-001, DOC-REQ-009).

---

## Dependencies & Execution Order

1. Setup (Phase 1) → establishes tooling context.
2. Foundational (Phase 2) → requires Setup; blocks every story.
3. User Story 1 (Phase 3) → depends on foundational persistence.
4. User Story 2 (Phase 4) → depends on Phase 2 and consumes POST outcomes from Phase 3.
5. User Story 3 (Phase 5) → depends on Phase 2 plus service metadata from Phase 3.
6. Polish (Phase 6) → after stories 1–3 reach acceptance.

**User Story Dependency Graph**: US1 → {US2, US3}; US2 and US3 can proceed in parallel once US1 service contracts exist.

## Parallel Execution Examples

- **US1**: T012 (agent_queue router update) and T013 (POST handler) can proceed in parallel once T011 defines the service API; tests T008–T010 may be authored concurrently following T003–T007.
- **US2**: T019–T021 (view model + HTML + JS/CSS) can run simultaneously after T018 exposes GET responses; tests T015–T017 run in parallel once DTOs are stable.
- **US3**: T027 (heartbeat serialization) and T028 (MCP propagation) may execute in parallel after T026 worker structures exist; tests T023–T025 can run together once metadata contracts are finalized.

## Implementation Strategy

1. Deliver MVP by completing Phases 1–3 so operators can pause/resume via API before UI/worker refinements.
2. Layer telemetry + dashboard UX from Phase 4 to support drain monitoring and safe resume prompts.
3. Finish quiesce + MCP behavior (Phase 5) to cover multi-runtime coordination, then close with manual drills + docs (Phase 6).
