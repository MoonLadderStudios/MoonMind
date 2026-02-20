# Implementation Plan: Worker Pause System

**Branch**: `002-worker-pause` | **Date**: 2026-02-20 | **Spec**: `specs/002-worker-pause/spec.md`

MoonMind needs a global Worker Pause System per `docs/WorkerPauseSystem.md` so operators can enter Pause → Drain → Upgrade → Resume, guard queue claim paths, surface telemetry, and coordinate Codex/Gemini/Claude workers plus the dashboard. This plan covers schema changes, API/services, worker + MCP runtime behavior, UI controls, and validation artifacts required before implementation.

## Summary

We will persist a singleton `system_worker_pause_state` and append-only `system_control_events`, wire FastAPI + service layers to expose `/api/system/worker-pause`, enrich claim/heartbeat responses with versioned `system` metadata, teach queue workers/MCP clients to honor drain and quiesce modes, and add a dashboard banner/control plus documentation/testing so operators can safely pause/resume all runtimes without mutating queued work.

## Technical Context

**Language/Version**: Python 3.11 across `api_service` and `moonmind` packages, JavaScript (ES2020) bundled in `api_service/static/task_dashboard`, Node 20 for dashboard unit tests.  
**Primary Dependencies**: FastAPI + Pydantic for HTTP APIs, SQLAlchemy/Alembic for persistence, httpx for worker queue client, vanilla JS + Jinja templates for dashboard, pytest + pytest-asyncio for unit tests.  
**Storage**: PostgreSQL via `api_service.db.models.Base` (existing queue tables) for pause state + audit, filesystem artifacts unchanged.  
**Testing**: `./tools/test_unit.sh` (pytest) plus Node-based smoke tests in `tests/task_dashboard/*.js`; integration tests remain in CI.  
**Target Platform**: Dockerized Linux containers (api_service, Celery workers, Codex worker CLI) fronted by MoonMind dashboard.  
**Project Type**: Multi-service backend with REST API, CLI worker runtimes, and static dashboard UI (no separate SPA build).  
**Performance Goals**: Pause snapshot reads must stay sub-10 ms (single row), claim guard adds <5 ms latency, dashboard polling ≤10 s without hammering DB, worker pause backoff defaults to 3–10 s while ensuring heartbeats remain under lease TTL.  
**Constraints**: Claim guard must skip `_requeue_expired_jobs` while paused (DOC-REQ-004), all pause/resume actions require non-empty reasons + audit (DOC-REQ-003), quiesce mode cannot drop leases and must preserve checkpoint semantics, response shapes must remain backward compatible for workers/MCP clients.  
**Scale/Scope**: Shared queue handles dozens of concurrent workers across Codex/Gemini/Claude runtimes, thousands of tasks/day, and is polled continuously by dashboard + MCP tooling.

## Constitution Check

The constitution file at `.specify/memory/constitution.md` is placeholder-only (no enforceable principles). With no ratified directives to validate, this gate passes with a note: document every decision, keep tests mandatory, and re-run the gate once the constitution gains content.

## Project Structure

### Documentation (feature artifacts)

```text
specs/002-worker-pause/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── system-worker-pause-api.md
│   └── requirements-traceability.md
└── checklists/
    └── ...
```

### Source Code (implementation touchpoints)

```text
docs/WorkerPauseSystem.md
api_service/
├── api/
│   ├── routers/
│   │   ├── agent_queue.py
│   │   ├── system_worker_pause.py        # new router
│   │   └── task_dashboard.py / _view_model.py
│   └── schemas.py + new system schemas
├── static/task_dashboard/
│   ├── dashboard.js
│   └── dashboard.css
├── templates/task_dashboard.html
└── migrations/versions/
    └── 202602200001_worker_pause_system.py (new Alembic migration)
moonmind/
├── workflows/agent_queue/
│   ├── models.py
│   ├── repositories.py
│   └── service.py
├── agents/codex_worker/worker.py
└── mcp/tool_registry.py
tests/
├── unit/api/routers/test_agent_queue.py
├── unit/api/routers/test_system_worker_pause.py (new)
├── unit/api/routers/test_task_dashboard_view_model.py
├── unit/workflows/agent_queue/test_repositories.py
├── unit/workflows/agent_queue/test_service_hardening.py
├── unit/mcp/test_tool_registry.py
└── task_dashboard/test_theme_runtime.js (+ new JS smoke tests)
```

**Structure Decision**: Keep everything inside the existing FastAPI + MoonMind packages (no new services). Persistence, API validation, worker runtime, MCP tools, and dashboard assets already live in these paths, so enhancing in place keeps transaction boundaries, dependency injection, and deployment baked into current containers.

## Implementation Strategy

### 1. Persistence & Migration (DOC-REQ-002, FR-001, FR-007)
- Define `SystemWorkerPauseState` (singleton `id=1`) and `SystemControlEvent` models in `moonmind/workflows/agent_queue/models.py` with FK to `user.id`, enum-safe `mode`, `requested_at`, `updated_at`, and monotonic `version`.
- Write Alembic migration `api_service/migrations/versions/202602200001_worker_pause_system.py` that creates both tables, seeds the singleton row (paused=false, version=1), and adds supporting indexes (e.g., on `system_control_events.created_at`).
- Extend `AgentQueueRepository` with helpers to upsert the singleton, lock it (`SELECT ... FOR UPDATE`), append audit events, and fetch recent history. Include a metrics query that counts queued/running/stale-running jobs without disturbing `_requeue_expired_jobs`.

### 2. Pause Snapshot & Claim Guard (DOC-REQ-001, DOC-REQ-004, DOC-REQ-010, FR-002–FR-006, FR-011)
- Introduce dataclasses / Pydantic schemas (e.g., `QueueSystemMetadataModel`, `WorkerPauseMetricsModel`, `WorkerPauseSnapshotResponse`) so routers, MCP tools, and workers share the same shape for `{ system: { workersPaused, mode, reason, version, requestedAt, updatedAt } }`.
- Update `AgentQueueService.claim_job` to fetch the latest pause snapshot before touching the repository; when paused, return early with `job=None` and the metadata while skipping `_requeue_expired_jobs`. When running, return `(job, metadata)` so version changes are still observable.
- Extend `AgentQueueService.heartbeat` (and other job serializers) to include `system` metadata in their responses. Add service methods to toggle pause/resume (validating action/mode/reason) and to expose drain metrics + recent audit events for GET.
- Modify `api_service/api/routers/agent_queue.py` to emit the new `system` block in claim responses and attach metadata to heartbeat/job responses without breaking existing clients (likely via `model_copy(update=...)`).

### 3. Operator API & Audit Surface (DOC-REQ-003, DOC-REQ-006, DOC-REQ-009)
- Add `api_service/api/routers/system_worker_pause.py` with `GET /api/system/worker-pause` and `POST /api/system/worker-pause`, protected by the same operator auth dependencies as the dashboard. Validation rules: non-empty reason for pause/resume, mode required for pause, resume rejected when already running unless explicitly acknowledged (HTTP 409/400 message).
- Return pause snapshot + `metrics` (queued/running/staleRunning/isDrained) and `audit.latest` (limit 5) so the UI can render progress + history. Hook the router into `api_service/main.py`.
- Document the contract in `contracts/system-worker-pause-api.md` and map each `DOC-REQ-*` in `contracts/requirements-traceability.md`.

### 4. Worker & MCP Behavior (DOC-REQ-005, DOC-REQ-007, DOC-REQ-009, DOC-REQ-011, FR-005–FR-010)
- Teach `QueueApiClient.claim_job` and `QueueApiClient.heartbeat` (`moonmind/agents/codex_worker/worker.py`) to parse the `system` payload and return a structured result (job + metadata). Add worker config `pause_poll_interval_ms` (env `MOONMIND_PAUSE_POLL_INTERVAL_MS`, default 5000) and track `last_pause_version_logged`.
- Update the Codex worker loop: when claim returns `workersPaused=true`, log the status once per version, sleep for `pause_poll_interval`, and skip `_claim_next_job` work while continuing to heartbeat running tasks. Heartbeat loop should set `_active_pause_event` whenever the global `system.mode=="quiesce"` so checkpoint hooks pause safely while continuing to heartbeat.
- Ensure resume clears pause events, and stale leases stay visible (metrics only) so operators can check `staleRunning`.
- Mirror the JSON shape in `moonmind/mcp/tool_registry.py` so `queue.claim` / `queue.heartbeat` responses deliver `system` metadata to IDE tools without additional parsing.

### 5. Dashboard UX & Quickstart (DOC-REQ-008, DOC-REQ-009, DOC-REQ-010)
- Extend `api_service/api/routers/task_dashboard_view_model.py` config with `system.workerPause` endpoints + polling interval so JS knows where to fetch/push state.
- Add a global banner/control surface in `api_service/templates/task_dashboard.html` + `static/task_dashboard/dashboard.{js,css}`: show “Workers: Running/Paused (Drain|Quiesce)”, queued/running/stale counts, `isDrained` indicator, and Pause/Resume forms with reason/mode fields. POST to the new API, disable controls while requests are in flight, and warn operators if they resume while `isDrained=false`.
- Capture this workflow in `quickstart.md` (API curl steps + dashboard instructions) so operators can validate Pause → Drain → Upgrade → Resume + Quiesce drills locally.

### 6. Validation & Observability
- Update pytest suites: repository + service tests for pause state transitions, new API router tests for validation/audit output, agent_queue router tests for `system` envelope, MCP tests for schema changes, worker tests to ensure pause backoff logic triggers, and dashboard view-model/JS tests to cover the banner/control contract.
- Ensure `./tools/test_unit.sh` runs both Python + Node suites (Node tests executed via npm scripts already referenced in `package.json`). Add targeted fixtures/mocks for pause snapshots.
- Emit structured logs/metrics (e.g., `logger.info("worker_pause.change", ...)` in service layer) so StatsD/Grafana can hook in later; expose `version`, `mode`, and counts for downstream alerts.

## Complexity Tracking

No constitution violations to document at this time (single API + UI feature built inside existing services).
