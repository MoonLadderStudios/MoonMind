# Implementation Plan: Worker Pause System

**Branch**: `001-worker-pause` | **Date**: 2026-02-20 | **Spec**: specs/001-worker-pause/spec.md  
**Input**: Feature specification from `/specs/001-worker-pause/spec.md`

## Summary
Implement the Worker Pause System described in `docs/WorkerPauseSystem.md` by adding persistent pause state + audit tables, guarded queue claim/heartbeat paths that surface pause metadata, operator APIs/UI controls, and worker runtime handling for Drain/Quiesce modes. Research decisions cover persistence location, service-layer guardrails, shared schema design, and dashboard propagation (see `research.md`).

## Technical Context

**Language/Version**: Python 3.11 (FastAPI service + async SQLAlchemy)  
**Primary Dependencies**: FastAPI, SQLAlchemy, Pydantic v2, httpx (worker client), Tailwind/vanilla JS dashboard bundle  
**Storage**: PostgreSQL (existing `agent_jobs` schema + new `system_worker_pause_state` and `system_control_events` tables)  
**Testing**: `./tools/test_unit.sh` (pytest + async fixtures) for API/service/worker/dashboard unit coverage  
**Target Platform**: Linux containers (FastAPI API service, queue workers, MCP adapters)  
**Project Type**: Multi-service backend (FastAPI API + MoonMind queue workers + dashboard static assets)  
**Performance Goals**: Pause toggle + claim guard must add <50 ms overhead per request; GET metrics should stay <200 ms for queues <= 10k jobs  
**Constraints**: Queue claims cannot mutate job state while paused; API responses must stay backward compatible; dashboards require zero additional bundlers (vanilla JS edits only)  
**Scale/Scope**: Single queue deployment (~tens of concurrent workers) but needs deterministic behavior for all runtimes (Codex now, Gemini/Claude later)

## Constitution Check

*No explicit constitution content exists in `.specify/memory/constitution.md`; treat governance requirements as PASS but flag that future constitution updates may introduce new gates. No blocking violations detected.*

## Project Structure

### Documentation (this feature)

```text
specs/001-worker-pause/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
└── contracts/
    ├── requirements-traceability.md
    └── system-worker-pause-api.md
```

### Source Code (repository root)

```text
api_service/
├── api/
│   ├── routers/
│   │   ├── agent_queue.py          # claim/heartbeat REST handlers
│   │   └── system_worker_pause.py  # (new) GET/POST worker pause endpoints
│   ├── schemas/
│   │   └── system_worker_pause.py  # (new) Pydantic models for pause APIs
│   └── templates/static            # dashboard Jinja + JS bundle
├── db/models.py                    # SQLAlchemy models (may expose audit helpers)
├── migrations/versions/            # Alembic migration for new tables & columns
└── static/task_dashboard/dashboard.js  # Banner + Pause/Resume controls

moonmind/
├── schemas/agent_queue_models.py   # Shared response models (system metadata)
├── workflows/agent_queue/
│   ├── models.py                   # ORM definitions for queue + pause state
│   ├── repositories.py             # Data access, metrics, pause helpers
│   └── service.py                  # Business logic + claim guard
└── agents/codex_worker/worker.py   # Worker runtime + queue HTTP client

moonmind/mcp/tool_registry.py       # MCP queue tools results propagation

api_service/tests/... & tests/unit   # REST + worker test suites to extend
```

**Structure Decision**: Reuse existing API + workflow modules and introduce a focused router/schema pair for `/api/system/worker-pause`. Worker + MCP changes occur in their respective packages so downstream runtimes inherit the new metadata without reorganizing directories.

## Complexity Tracking

_No constitution-driven complexity exceptions required._

## Architecture & Data Flow

- **Persistence layer**: `SystemWorkerPauseState` (singleton row) and `SystemControlEvent` (append-only) live in the `agent_jobs` schema. Writes run through SQLAlchemy models inside `moonmind/workflows/agent_queue/models.py`, with repository helpers performing `SELECT ... FOR UPDATE` to guarantee atomic state flips and monotonic `version` increments.
- **API service**: FastAPI router `system_worker_pause.py` exposes `GET/POST /api/system/worker-pause`. It shares Pydantic models with queue routers so REST, dashboard, MCP, and workers consume the same schema. The router calls repository helpers for state mutation plus queue metrics (`queued`, `running`, `staleRunning`) to compute `isDrained`.
- **Queue service guard**: `AgentQueueService.claim_job` checks the cached pause state before touching repository claim logic. When paused, it returns `{job: null, system: WorkerPauseStatus}` immediately, preventing `_requeue_expired_jobs` or other side effects.
- **Worker + MCP clients**: `QueueApiClient` and MCP `queue.claim`/`queue.heartbeat` copy the `system` envelope straight through responses so runtimes know whether to idle, quiesce, or resume.
- **Dashboard**: The dashboard view model polls `/api/system/worker-pause`, merges metrics + audit history, and passes the data to `dashboard.js`, which renders the global banner, Pause/Resume controls, and drain progress card.

## Data Model & Migration Strategy

1. **Models**: Add SQLAlchemy models mirroring the tables defined in `docs/WorkerPauseSystem.md`. `SystemWorkerPauseState` enforces `id=1`, stores `paused`, `mode`, `reason`, `requested_by_user_id`, `requested_at`, `updated_at`, and `version`. `SystemControlEvent` records `control`, `action`, `mode`, `reason`, `actor_user_id`, `created_at`.
2. **Migration**: Create Alembic revision `20260220_worker_pause.py` that:
   - Creates both tables with indexes on `control`/`created_at`.
   - Seeds the singleton row (`paused=false`, `mode=NULL`, `version=1`) so API handlers can assume existence.
3. **Repository Utilities**:
   - `get_worker_pause_state(session)` returns the current state (optionally cached in memory with optimistic reload when `version` changes).
   - `set_worker_pause_state(session, action, mode, reason, actor_id)` handles validation, row locking, `version` bump, and audit insertion in one transaction.
   - `get_worker_pause_metrics(session)` aggregates queue counts and surfaces `staleRunning` by looking for expired leases (`lease_expires_at < now()`).

## Service/API Execution Plan

- **Schema objects**: Introduce shared Pydantic models `WorkerPauseSystemInfo`, `WorkerPauseMetrics`, and `WorkerPauseResponse` under `api_service/api/schemas/system_worker_pause.py` and re-export in `moonmind/schemas/agent_queue_models.py` for worker consumption.
- **Router logic**:
  - `GET`: returns `state`, `metrics`, and the last N control events (e.g., 10) sorted descending, enabling dashboard history.
  - `POST`: validates the action payload, rejects invalid modes, enforces reason requirement, and prevents `resume` when already running. Successful transitions return the new state plus metrics for immediate UI refresh.
- **Claim/heartbeat response updates**: `api_service/api/routers/agent_queue.py` attaches the `system` object on every claim/heartbeat response without altering existing top-level fields to maintain backward compatibility.
- **Error handling**: Map repository validation errors to HTTP 400 with operator-friendly messages (e.g., “System already paused; update reason via action=pause to change metadata.”).

## Worker & MCP Runtime Changes

- **QueueApiClient**: parse `system` metadata on claim + heartbeat responses. When `workersPaused=true`, log `Paused (Drain)` or `Paused (Quiesce)` once per `system.version`, sleep with `pause_poll_interval_ms` (default to `poll_interval_ms`), and avoid marking the lack of work as failure.
- **Quiesce checkpoints**: `_wait_if_paused` monitors heartbeat returns. During Quiesce, it pauses between wrapper stages or task steps, keeps heartbeating, and emits warnings if checkpoint pauses exceed a configurable timeout.
- **MCP tool registry**: Extend tool responses so `queue.claim` + `queue.heartbeat` mirror REST `system` payloads, ensuring CLI workflows obey pause state.

## Dashboard & Operator UX

- **View model**: `task_dashboard_view_model.py` fetches pause state + metrics once per poll cycle and injects them into the rendered HTML as JSON so `dashboard.js` can bootstrap without an extra request.
- **UI controls**: `dashboard.js` renders:
  - A badge indicating `Workers: Running`, `Workers: Paused (Drain)`, or `Workers: Paused (Quiesce)` plus the operator reason.
  - A Pause form with mode selector (Drain default, Quiesce secondary) and required reason field.
  - A Resume button that is disabled unless `paused=true`.
  - Drain metrics card showing counts, `isDrained` indicator, and stale running job callouts.
- **Accessibility & resilience**: Error states display inline toasts; if the pause API fails, the badge switches to “Unknown” without breaking the rest of the dashboard.

## Validation & Observability Strategy

- **Unit/API tests** (`./tools/test_unit.sh`):
  - Repository tests verifying singleton creation, `version` increments, audit writes, and metric aggregation queries.
  - Service tests confirming `AgentQueueService.claim_job` short-circuits while paused.
  - API router tests covering validation failures, action transitions, and schema compatibility on claim/heartbeat endpoints.
  - Worker tests for pause idle loops + Quiesce checkpoints; MCP tests for metadata propagation; dashboard JS tests for UI state updates.
- **Monitoring hooks**:
  - Emit StatsD gauges/counters (`worker_pause.paused`, `worker_pause.drained`, `worker_pause.actions`) during state transitions.
  - Structured logs include `pause_version`, `mode`, and `reason` for correlation across services.
  - Dashboard polls at 5s (running) / 15s (paused) intervals to reduce load while still updating operators promptly.

## Operational Runbook Alignment

1. **Pause**: Operators trigger Pause (Drain default) via dashboard or API. The system records the event, notifies workers, and begins drain monitoring.
2. **Drain confirmation**: Dashboard displays `running=0` and `isDrained=true`. Operators also review `staleRunning` to ensure no expired leases remain.
3. **Upgrade/Maintenance**: With workers idle, teams can restart containers, rebuild images, run migrations, or rotate credentials without job mutations.
4. **Resume**: Operators click Resume, clearing the pause state. Workers observe the higher `system.version`, exit idle loops, and resume claims automatically.
5. **Audit review**: `system_control_events` retains the entire toggle history for compliance checks, and dashboards surface the latest entries for transparency.
