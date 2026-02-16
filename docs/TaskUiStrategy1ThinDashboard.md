# Task UI Technical Design - Strategy 1 Thin Dashboard

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-15

## 1. Decision

### Selected Strategy

Use **Strategy 1: Thin Dashboard UI over existing REST endpoints**.

### Why this is best for current project goals

This project’s primary goals are:

- Show currently running tasks.
- Submit new tasks.
- Minimize backend changes.
- Ship quickly with low operational risk.

Strategy 1 is the best fit because MoonMind already has complete submit/list/detail primitives for:

- Agent Queue (`/api/queue/*`)
- SpecKit workflows (`/api/workflows/speckit/*`)
- Orchestrator runs (`/orchestrator/*`)

No new core workflow APIs are required for MVP.

## 2. Scope

### In Scope

- A dedicated dashboard web app for task operations.
- Views for running, queued, and historical work.
- Submit forms for Agent Queue jobs, SpecKit runs, and Orchestrator runs.
- Detail pages with logs/events and artifacts.
- Polling-based refresh (no real-time sockets in MVP).

### Out of Scope

- SSE/WebSocket implementation.
- Unified backend `runs` table.
- Worker/fleet heartbeat model.
- Open-WebUI plugin internals.

## 3. Backend Contract Reuse

This design directly uses existing routes.

### 3.1 Agent Queue

From `api_service/api/routers/agent_queue.py`:

- `POST /api/queue/jobs`
- `GET /api/queue/jobs?status=&type=&limit=`
- `GET /api/queue/jobs/{job_id}`
- `GET /api/queue/jobs/{job_id}/events?after=&limit=`
- `GET /api/queue/jobs/{job_id}/artifacts`
- `GET /api/queue/jobs/{job_id}/artifacts/{artifact_id}/download`

### 3.2 SpecKit

From `api_service/api/routers/workflows.py`:

- `POST /api/workflows/speckit/runs`
- `GET /api/workflows/speckit/runs?status=&limit=&cursor=`
- `GET /api/workflows/speckit/runs/{run_id}`
- `GET /api/workflows/speckit/runs/{run_id}/tasks`
- `GET /api/workflows/speckit/runs/{run_id}/artifacts`
- `POST /api/workflows/speckit/runs/{run_id}/retry`

### 3.3 Orchestrator

From `api_service/api/routers/orchestrator.py`:

- `POST /orchestrator/runs`
- `GET /orchestrator/runs?status=&service=&limit=&offset=`
- `GET /orchestrator/runs/{run_id}`
- `GET /orchestrator/runs/{run_id}/artifacts`
- `POST /orchestrator/runs/{run_id}/approvals`
- `POST /orchestrator/runs/{run_id}/retry`

## 4. UI Architecture

### 4.1 App Structure

A standalone dashboard app (React/Next.js or equivalent) with these routes:

- `/tasks` consolidated running and queued view.
- `/tasks/queue` Agent Queue list.
- `/tasks/queue/new` create queue job.
- `/tasks/queue/:jobId` queue job details.
- `/tasks/speckit` SpecKit run list.
- `/tasks/speckit/new` create SpecKit run.
- `/tasks/speckit/:runId` SpecKit run details.
- `/tasks/orchestrator` Orchestrator run list.
- `/tasks/orchestrator/new` create Orchestrator run.
- `/tasks/orchestrator/:runId` Orchestrator run details.

### 4.2 Shared UI Modules

- `RunStatusBadge`: source-native and normalized status labels.
- `RunTable`: list display with source, status, age, duration.
- `ArtifactList`: metadata and download links.
- `EventTimeline`: queue event stream via polling.
- `SubmitPanels`: typed forms for each system.

## 5. Data Model in the UI

The dashboard uses a frontend-normalized list model for aggregated pages:

```ts
export type DashboardRun = {
  source: "queue" | "speckit" | "orchestrator";
  id: string;
  displayName: string;
  normalizedStatus: "queued" | "running" | "awaiting_action" | "succeeded" | "failed" | "cancelled";
  rawStatus: string;
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
  link: string;
};
```

Normalization rules:

- Queue: `queued -> queued`, `running -> running`, `succeeded -> succeeded`, `failed|dead_letter -> failed`, `cancelled -> cancelled`.
- SpecKit: `pending|retrying -> queued`, `running -> running`, `succeeded|no_work -> succeeded`, `failed -> failed`, `cancelled -> cancelled`.
- Orchestrator: `pending -> queued`, `running -> running`, `awaiting_approval -> awaiting_action`, `succeeded|rolled_back -> succeeded`, `failed -> failed`.

## 6. Page-Level API Contracts

### 6.1 Consolidated Running/Queued Page (`/tasks`)

Client fan-out with parallel fetches:

- `GET /api/queue/jobs?status=running&limit=200`
- `GET /api/queue/jobs?status=queued&limit=200`
- `GET /api/workflows/speckit/runs?status=running&limit=100`
- `GET /api/workflows/speckit/runs?status=pending&limit=100`
- `GET /api/workflows/speckit/runs?status=retrying&limit=100`
- `GET /orchestrator/runs?status=running&limit=100`
- `GET /orchestrator/runs?status=pending&limit=100`
- `GET /orchestrator/runs?status=awaiting_approval&limit=100`

### 6.2 Queue Submit (`/tasks/queue/new`)

POST body shape (from `CreateJobRequest`):

- `type: string`
- `payload: object`
- `priority: number` (default `0`)
- `affinityKey?: string`
- `maxAttempts?: number` (default `3`)

Endpoint: `POST /api/queue/jobs`

### 6.3 Queue Detail (`/tasks/queue/:jobId`)

- `GET /api/queue/jobs/{job_id}`
- `GET /api/queue/jobs/{job_id}/events?after=<lastSeenTimestamp>&limit=200`
- `GET /api/queue/jobs/{job_id}/artifacts`

### 6.4 SpecKit Submit (`/tasks/speckit/new`)

POST body shape (from `CreateWorkflowRunRequest`):

- `repository: owner/repo`
- `featureKey?: string`
- `forcePhase?: discover|submit|apply|publish`
- `notes?: string`

Endpoint: `POST /api/workflows/speckit/runs`

### 6.5 SpecKit Detail (`/tasks/speckit/:runId`)

- `GET /api/workflows/speckit/runs/{run_id}`
- `GET /api/workflows/speckit/runs/{run_id}/tasks`
- `GET /api/workflows/speckit/runs/{run_id}/artifacts`

### 6.6 Orchestrator Submit (`/tasks/orchestrator/new`)

POST body shape (from `OrchestratorCreateRunRequest`):

- `instruction: string`
- `targetService: string`
- `approvalToken?: string`
- `priority?: normal|high`

Endpoint: `POST /orchestrator/runs`

### 6.7 Orchestrator Detail (`/tasks/orchestrator/:runId`)

- `GET /orchestrator/runs/{run_id}`
- `GET /orchestrator/runs/{run_id}/artifacts`
- `POST /orchestrator/runs/{run_id}/approvals`
- `POST /orchestrator/runs/{run_id}/retry`

## 7. Polling and Refresh Model

Polling is the only runtime update mechanism in this strategy.

- Aggregated/list pages: poll every 5 seconds.
- Detail pages: poll every 2 seconds.
- Queue events: poll every 1 second with `after` cursor.
- Suspend polling when tab is hidden.
- Apply exponential backoff on HTTP 429/5xx.

This gives near-live UX without backend protocol changes.

## 8. Authentication Model

### 8.1 UI-to-API auth

UI uses user authentication context for submit/list/detail calls.

- `AUTH_PROVIDER=disabled`: existing default-user behavior.
- OIDC: bearer/cookie pass-through.

### 8.2 Worker token separation

Do not use `X-MoonMind-Worker-Token` in dashboard requests. Worker tokens remain for worker-only mutation endpoints.

### 8.3 Security note

`/orchestrator` routes currently do not use `get_current_user()` dependencies. Before broad deployment, align orchestrator auth enforcement with queue/workflow routes.

## 9. Deployment

### 9.1 Preferred deployment pattern

Run dashboard as an additional service and link from existing Open-WebUI.

- One operational UI entry point for users.
- Minimal changes to existing `ui` service behavior.

### 9.2 Environment

- `MOONMIND_DASHBOARD_API_BASE_URL` (or framework equivalent) -> `http://api:5000`

## 10. Implementation Plan

### Phase 1 (MVP)

- Build all list/detail/submit pages.
- Implement status normalization and consolidated `/tasks`.
- Implement polling and error states.

### Phase 2 (Usability)

- Add saved filters and table presets.
- Add JSON payload editors with templates for common queue job types.
- Add per-page quick actions (retry, approve, copy IDs).

## 11. Risks and Mitigations

- Polling load growth with many active users.
- Mitigation: visibility pause, adaptive backoff, small limits.

- Inconsistent field naming across APIs.
- Mitigation: strict adapter layer with typed transforms.

- Orchestrator auth parity gap.
- Mitigation: add route dependency enforcement before external exposure.

## 12. Acceptance Criteria

- Users can submit all three run/job types from the UI.
- Users can view running and queued work in one consolidated page.
- Users can inspect queue events and artifacts from detail pages.
- Dashboard runs without requiring any new backend endpoint for MVP.

## 13. Implementation Status (2026-02-15)

Initial Strategy 1 implementation is now present in the API service:

- Router: `api_service/api/routers/task_dashboard.py`
- View-model/status normalization helper: `api_service/api/routers/task_dashboard_view_model.py`
- Template shell: `api_service/templates/task_dashboard.html`
- Static client assets:
  - `api_service/static/task_dashboard/dashboard.js`
  - `api_service/static/task_dashboard/dashboard.css`
- App wiring: `api_service/main.py` includes the dashboard router.
- Unit coverage:
  - `tests/unit/api/routers/test_task_dashboard.py`
  - `tests/unit/api/routers/test_task_dashboard_view_model.py`
