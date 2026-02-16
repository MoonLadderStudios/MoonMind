# Task UI Architecture

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-15

## 1. Purpose

Define a web UI architecture for MoonMind that lets users:

- Submit new tasks.
- See currently running and queued work.
- Inspect logs/events and artifacts per execution.
- Monitor Agent Queue jobs, SpecKit workflow runs, and Orchestrator runs from one UI surface.

The design prioritizes minimal backend changes by reusing existing APIs.

## 2. Goals and Non-Goals

### Goals

- Deliver a production-usable MVP quickly using existing REST endpoints.
- Provide a clear path from polling to real-time updates.
- Keep authentication aligned with current MoonMind auth provider behavior.
- Avoid coupling UI implementation to one worker system.

### Non-Goals

- Replacing Open-WebUI.
- Rewriting existing workflow/orchestrator backends.
- Defining new worker execution semantics.

## 3. Existing Backend Capabilities

As of 2026-02-15, MoonMind already exposes submit plus monitor primitives for all three systems.

### 3.1 Agent Queue (`/api/queue`)

Implemented in `api_service/api/routers/agent_queue.py`.

- `POST /api/queue/jobs` creates jobs.
- `GET /api/queue/jobs` lists jobs with `status`, `type`, `limit` filters.
- `GET /api/queue/jobs/{job_id}` returns one job.
- `GET /api/queue/jobs/{job_id}/events` returns append-only events with `after` cursor support.
- `GET /api/queue/jobs/{job_id}/artifacts` lists artifacts.
- `GET /api/queue/jobs/{job_id}/artifacts/{artifact_id}/download` downloads artifact bytes.

Status model already supports dashboard needs: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `dead_letter`.

### 3.2 SpecKit Workflows (`/api/workflows/speckit`)

Implemented in `api_service/api/routers/workflows.py`.

- `POST /api/workflows/speckit/runs` creates workflow runs.
- `GET /api/workflows/speckit/runs` lists runs.
- `GET /api/workflows/speckit/runs/{run_id}` returns run detail with optional tasks and artifacts.
- `GET /api/workflows/speckit/runs/{run_id}/tasks` returns task timeline.
- `GET /api/workflows/speckit/runs/{run_id}/artifacts` returns artifacts.
- `POST /api/workflows/speckit/runs/{run_id}/retry` retries failed runs.

Status model: `pending`, `running`, `succeeded`, `failed`, `no_work`, `cancelled`, `retrying`.

### 3.3 Orchestrator (`/orchestrator`)

Implemented in `api_service/api/routers/orchestrator.py`.

- `POST /orchestrator/runs` creates runs.
- `GET /orchestrator/runs` lists runs with `status` and `service` filters.
- `GET /orchestrator/runs/{run_id}` returns run detail including plan steps.
- `GET /orchestrator/runs/{run_id}/artifacts` lists artifacts.
- `POST /orchestrator/runs/{run_id}/approvals` grants approvals.
- `POST /orchestrator/runs/{run_id}/retry` retries runs.

Status model: `pending`, `running`, `awaiting_approval`, `succeeded`, `failed`, `rolled_back`.

## 4. Recommended Architecture

### 4.1 Recommendation

Adopt Strategy 1 as the default MVP: a thin dashboard over existing REST APIs, with polling.

Adopt Strategy 2 for deployment ergonomics: run dashboard as a sidecar service and link from Open-WebUI.

Prepare for Strategy 3 and Strategy 5 by implementing a frontend normalization layer now.

### 4.2 Why this approach

- Fastest path to usable UI with near-zero backend work.
- Leaves backend ownership boundaries unchanged.
- Keeps future upgrades incremental: polling to SSE, per-system lists to unified runs.

## 5. UI Information Architecture

### 5.1 Routes

Use a dedicated dashboard app with route groups:

- `/tasks` unified running view (aggregated client-side from all three systems).
- `/tasks/queue` list of Agent Queue jobs.
- `/tasks/queue/new` submit Agent Queue job.
- `/tasks/queue/:jobId` job detail with events and artifacts.
- `/tasks/speckit` list of SpecKit runs.
- `/tasks/speckit/new` submit SpecKit run.
- `/tasks/speckit/:runId` run detail with task timeline and artifacts.
- `/tasks/orchestrator` list of Orchestrator runs.
- `/tasks/orchestrator/new` submit Orchestrator run.
- `/tasks/orchestrator/:runId` run detail with plan steps, approvals, retry, artifacts.

### 5.2 Shared Components

- `RunTable` with status, owner, created time, duration, source system.
- `StatusBadge` for normalized and source-native status display.
- `EventLogPanel` with incremental loading for queue events.
- `ArtifactList` with size/type and download actions.
- `SubmitFormRegistry` keyed by job/run type to avoid permanent raw JSON forms.

## 6. API-to-Page Contract

### 6.1 Running View (`/tasks`)

The UI performs parallel fetches and merges rows client-side.

- Queue running: `GET /api/queue/jobs?status=running&limit=200`
- Queue queued: `GET /api/queue/jobs?status=queued&limit=200`
- SpecKit running: `GET /api/workflows/speckit/runs?status=running&limit=100`
- SpecKit pending/retrying: `GET /api/workflows/speckit/runs?status=pending&limit=100` and `status=retrying`
- Orchestrator running: `GET /orchestrator/runs?status=running&limit=100`
- Orchestrator pending approval: `GET /orchestrator/runs?status=awaiting_approval&limit=100`

### 6.2 Queue Detail (`/tasks/queue/:jobId`)

- Primary data: `GET /api/queue/jobs/{job_id}`
- Event timeline: `GET /api/queue/jobs/{job_id}/events?after=<timestamp>`
- Artifacts: `GET /api/queue/jobs/{job_id}/artifacts`

### 6.3 SpecKit Detail (`/tasks/speckit/:runId`)

- Run detail: `GET /api/workflows/speckit/runs/{run_id}`
- Task timeline: `GET /api/workflows/speckit/runs/{run_id}/tasks`
- Artifacts: `GET /api/workflows/speckit/runs/{run_id}/artifacts`

### 6.4 Orchestrator Detail (`/tasks/orchestrator/:runId`)

- Run detail: `GET /orchestrator/runs/{run_id}`
- Artifacts: `GET /orchestrator/runs/{run_id}/artifacts`
- Approval action: `POST /orchestrator/runs/{run_id}/approvals`
- Retry action: `POST /orchestrator/runs/{run_id}/retry`

## 7. Frontend Data Normalization

Define a shared view model so one table can show all systems:

```ts
export type UnifiedRun = {
  source: "queue" | "speckit" | "orchestrator";
  id: string;
  title: string;
  status: "queued" | "running" | "awaiting_action" | "succeeded" | "failed" | "cancelled";
  rawStatus: string;
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
  requestedBy?: string;
  link: string;
};
```

Normalization rules:

- Queue `queued -> queued`, `running -> running`, `succeeded -> succeeded`, `failed|dead_letter -> failed`, `cancelled -> cancelled`.
- SpecKit `pending|retrying -> queued`, `running -> running`, `succeeded|no_work -> succeeded`, `failed -> failed`, `cancelled -> cancelled`.
- Orchestrator `pending -> queued`, `running -> running`, `awaiting_approval -> awaiting_action`, `succeeded|rolled_back -> succeeded`, `failed -> failed`.

## 8. Realtime Strategy

### 8.1 MVP Polling

- List pages poll every 5 seconds.
- Detail pages poll every 2 seconds.
- Queue events use incremental polling via `after` cursor every 1 second.
- Pause polling when document is hidden.

### 8.2 Upgrade Path

Add SSE endpoint for queue events first:

- `GET /api/queue/jobs/{job_id}/events/stream`

Implementation can start with DB polling loop and later move to Postgres `LISTEN/NOTIFY`.

WebSockets are optional and only needed if bidirectional messaging is introduced.

## 9. Authentication and Authorization

### 9.1 User actions

UI calls submit/list/detail endpoints using end-user auth context.

- `AUTH_PROVIDER=disabled`: default local user behavior remains intact.
- OIDC mode: bearer/cookie auth should be forwarded by the UI.

### 9.2 Worker actions

Queue mutation endpoints used by workers (`claim`, `heartbeat`, `complete`, `fail`, event append, artifact upload) require worker auth via `X-MoonMind-Worker-Token` or OIDC worker identity.

The dashboard must not use worker tokens for user pages.

### 9.3 Current gap

`/orchestrator/*` routes currently do not enforce `get_current_user()` dependencies, unlike queue/workflow routes. For multi-user deployments, add auth dependency parity before exposing the dashboard outside trusted networks.

## 10. Deployment Topology

### 10.1 Preferred

Run a dedicated dashboard container and link it from Open-WebUI.

- Keep Open-WebUI unchanged.
- Expose dashboard at `/tasks` (reverse-proxy path) or separate port.
- Configure dashboard API base URL to MoonMind API service (`http://api:5000`).

### 10.2 Compose shape (conceptual)

```yaml
moonmind-dashboard:
  build: ./services/dashboard
  environment:
    - NEXT_PUBLIC_MOONMIND_API_BASE_URL=http://api:5000
  ports:
    - "8090:3000"
  depends_on:
    - api
  networks:
    - local-network
```

## 11. Delivery Plan

### Phase 1: Thin dashboard MVP

- Implement route groups and submit/detail/list pages.
- Implement polling and unified running page.
- No new backend endpoints required.

### Phase 2: UX hardening

- Add submit form registry for typed payloads.
- Add filters and saved views.
- Add explicit empty/error states and retry UX.

### Phase 3: Live logs

- Add SSE for queue events.
- Replace event polling in detail page with `EventSource`.

### Phase 4: Optional unified backend model

- If query fan-out becomes expensive, add a server-side unified `runs` projection table and a single `GET /api/runs` endpoint.

## 12. Risks and Mitigations

- API inconsistency across systems can fragment UI behavior.
- Mitigation: normalize in frontend adapter layer now.

- Polling load can grow with active users.
- Mitigation: short-term polling backoff and visibility pause; mid-term SSE.

- Auth mismatch can leak orchestrator data in shared environments.
- Mitigation: add auth dependency parity on orchestrator routes.

## 13. Acceptance Criteria

- User can submit Agent Queue, SpecKit, and Orchestrator runs from UI.
- User can see running and queued work across all three systems in one screen.
- User can open a detail page and view progress plus artifacts.
- Queue event logs update within 2 seconds in MVP polling mode.
- UI works with both `AUTH_PROVIDER=disabled` and OIDC deployments.

## 14. Implementation Status (2026-02-15)

The Strategy 1 MVP implementation is now available via API-served dashboard routes:

- Runtime router: `api_service/api/routers/task_dashboard.py`
- Runtime status normalization: `api_service/api/routers/task_dashboard_view_model.py`
- Template and static assets:
  - `api_service/templates/task_dashboard.html`
  - `api_service/static/task_dashboard/dashboard.js`
  - `api_service/static/task_dashboard/dashboard.css`
- Main app integration: `api_service/main.py`
- Unit tests:
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
