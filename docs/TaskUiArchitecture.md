# Task UI Architecture

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-02-18

## 1. Purpose

Define a web UI architecture for MoonMind that lets users:

- Submit new Tasks with typed controls.
- See currently running and queued work.
- Inspect logs/events and artifacts per execution.
- Monitor Agent Queue jobs and Orchestrator runs from one UI surface, with SpecKit workloads represented as queue tasks using skill selection.

Execution semantics are defined by `docs/TaskArchitecture.md`; this document defines how the UI maps to those contracts.

## 2. Goals and Non-Goals

### Goals

- Deliver a production-usable MVP quickly using existing REST endpoints.
- Provide typed Task submission fields with safe defaults.
- Keep authentication aligned with current MoonMind auth provider behavior.
- Avoid coupling UI implementation to one runtime.

### Non-Goals

- Replacing Open-WebUI.
- Rewriting existing workflow/orchestrator backends.
- Redefining worker execution semantics outside `docs/TaskArchitecture.md`.

## 3. Existing Backend Capabilities

As of 2026-02-18, MoonMind exposes submit plus monitor primitives for queue and orchestrator dashboard categories, while SpecKit APIs remain available for backend workflows.

### 3.1 Agent Queue (`/api/queue`)

Implemented in `api_service/api/routers/agent_queue.py`.

- `POST /api/queue/jobs` creates jobs.
- `GET /api/queue/jobs` lists jobs with `status`, `type`, `limit` filters.
- `GET /api/queue/jobs/{job_id}` returns one job.
- `GET /api/queue/jobs/{job_id}/events` returns append-only events with `after` cursor support.
- `GET /api/queue/jobs/{job_id}/events/stream` streams the same queue events via SSE (`text/event-stream`).
- `GET /api/queue/jobs/{job_id}/artifacts` lists artifacts.
- `GET /api/queue/jobs/{job_id}/artifacts/{artifact_id}/download` downloads artifact bytes.

Status model supports dashboard needs: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `dead_letter`.

### 3.2 SpecKit Workflows (`/api/workflows/speckit`)

Implemented in `api_service/api/routers/workflows.py`.

These endpoints continue to support backend workflow operations, but the dashboard no longer exposes a dedicated SpecKit category. Operators launch SpecKit behavior from queue task submissions by selecting a SpecKit skill id and optional skill args.

### 3.3 Orchestrator (`/orchestrator`)

Implemented in `api_service/api/routers/orchestrator.py`.

- `POST /orchestrator/runs` creates runs.
- `GET /orchestrator/runs` lists runs with `status` and `service` filters.
- `GET /orchestrator/runs/{run_id}` returns run detail including plan steps.
- `GET /orchestrator/runs/{run_id}/artifacts` lists artifacts.
- `POST /orchestrator/runs/{run_id}/approvals` grants approvals.
- `POST /orchestrator/runs/{run_id}/retry` retries runs.

## 4. Recommended Architecture

### 4.1 Recommendation

Adopt Strategy 1 as the default MVP: a thin dashboard over existing REST APIs, with polling baseline and SSE on queue detail.

Adopt Strategy 2 for deployment ergonomics: run dashboard as a sidecar service and link from Open-WebUI.

Prepare for Strategy 3 and Strategy 5 by implementing a frontend normalization layer now.

### 4.2 Why this approach

- Fast path to typed Task UX with near-zero backend rework.
- Keeps backend ownership boundaries unchanged.
- Supports resilient realtime behavior with SSE primary + polling fallback and client fan-out to unified runs endpoints.

## 5. UI Information Architecture

### 5.1 Routes

Use a dedicated dashboard app with route groups:

- `/tasks` unified running view (aggregated client-side from queue and orchestrator).
- `/tasks/queue` list of Agent Queue jobs.
- `/tasks/queue/new` submit typed Task job (`type="task"`).
- `/tasks/queue/:jobId` job detail with events and artifacts.
- `/tasks/orchestrator` list of Orchestrator runs.
- `/tasks/orchestrator/new` submit Orchestrator run.
- `/tasks/orchestrator/:runId` run detail with plan steps, approvals, retry, artifacts.

### 5.2 Shared Components

- `RunTable` with status, owner, created time, duration, source system.
- `StatusBadge` for normalized and source-native status display.
- `EventLogPanel` with incremental loading for queue events.
- `ArtifactList` with size/type and download actions.
- `TaskSubmitForm` for typed fields from `docs/TaskArchitecture.md`.

## 6. Task Submit Contract (UI -> Queue)

Queue submit is a typed Task form, not a raw payload editor.

### 6.1 Fields Presented to Users

- `instructions` (required)
- `skill` (optional, default `auto`)
- `skillArgs` (optional JSON object, default `{}`)
- `runtime` (optional, default system runtime)
- `model` (optional)
- `effort` (optional)
- `repository` (optional, default system repo)
- `startingBranch` (optional, default repo default branch at execution)
- `newBranch` (optional)
- `publishMode` (optional, default `branch`)

### 6.2 Request Envelope

UI submits `CreateJobRequest`:

- `type: "task"`
- `payload: CanonicalTaskPayload`
- `priority: number` (default `0`)
- `affinityKey?: string`
- `maxAttempts?: number` (default `3`)

### 6.3 Payload Emitted by UI

```json
{
  "repository": "owner/repo",
  "requiredCapabilities": ["git", "codex"],
  "targetRuntime": "codex",
  "auth": { "repoAuthRef": null, "publishAuthRef": null },
  "task": {
    "instructions": "Implement feature X",
    "skill": { "id": "auto", "args": {} },
    "runtime": { "mode": "codex", "model": null, "effort": null },
    "git": { "startingBranch": null, "newBranch": null },
    "publish": {
      "mode": "branch",
      "prBaseBranch": null,
      "commitMessage": null,
      "prTitle": null,
      "prBody": null
    }
  }
}
```

Defaults that depend on repository state (for example default branch) are resolved by worker Pre stage at execution time.
The Task UI remains token-free: it does not collect or submit raw credential values.

## 7. API-to-Page Contract

### 7.1 Running View (`/tasks`)

UI performs parallel fetches and merges rows client-side.

- Queue running: `GET /api/queue/jobs?status=running&limit=200`
- Queue queued: `GET /api/queue/jobs?status=queued&limit=200`
- Orchestrator running: `GET /orchestrator/runs?status=running&limit=100`
- Orchestrator pending approval: `GET /orchestrator/runs?status=awaiting_approval&limit=100`

### 7.2 Queue Detail (`/tasks/queue/:jobId`)

- Primary data: `GET /api/queue/jobs/{job_id}`
- Event timeline: `GET /api/queue/jobs/{job_id}/events?after=<timestamp>`
- Event stream (preferred): `GET /api/queue/jobs/{job_id}/events/stream`
- Artifacts: `GET /api/queue/jobs/{job_id}/artifacts`

## 8. Frontend Data Normalization

```ts
export type UnifiedRun = {
  source: "queue" | "orchestrator";
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
- Orchestrator `pending -> queued`, `running -> running`, `awaiting_approval -> awaiting_action`, `succeeded|rolled_back -> succeeded`, `failed -> failed`.

## 9. Realtime Strategy

### 9.1 Current Behavior

- List pages poll every 5 seconds.
- Detail pages poll every 2 seconds.
- Queue detail events use SSE (`EventSource`) against `/events/stream` when supported.
- Queue detail falls back to incremental polling (`after` cursor every 1 second) when SSE is unavailable or errors.
- Pause polling when document is hidden.

### 9.2 Compatibility

- Polling endpoint behavior remains backward-compatible for existing clients.
- SSE uses the same event source/service as polling and does not require schema changes.

## 10. Authentication and Authorization

### 10.1 User actions

UI calls submit/list/detail endpoints using end-user auth context.

### 10.2 Worker actions

Queue mutation endpoints used by workers (`claim`, `heartbeat`, `complete`, `fail`, event append, artifact upload) require worker auth via `X-MoonMind-Worker-Token` or OIDC worker identity.

### 10.3 Current gap

`/orchestrator/*` routes currently do not enforce `get_current_user()` dependencies, unlike queue/workflow routes. Align this before broad multi-user deployment.

## 11. Delivery Plan

### Phase 1: Thin dashboard MVP

- Implement route groups and submit/detail/list pages.
- Implement typed Task submit form and unified running page.
- No new backend endpoints required for baseline submission and monitoring.

### Phase 2: UX hardening

- Add contextual validation/help for skill/runtime/publish options.
- Add filters and saved views.
- Add explicit empty/error states and retry UX.

### Phase 3: Live logs

- Completed for queue detail: SSE stream endpoint + `EventSource` client path with polling fallback.
- Future extensions can add SSE to additional pages if needed.

## 12. Related

- `docs/TaskArchitecture.md`
- `docs/TaskUiStrategy1ThinDashboard.md`
- `docs/CodexTaskQueue.md`

## 13. Recent Updates

- **2026-02-18 – Tailwind Style System Phase 2**: The dashboard stylesheet now compiles from `dashboard.tailwind.css` with `npm run dashboard:css`. Tokens were renamed to the `--mm-*` palette, gradients shifted to purple/cyan/pink, and status chips/cards adopt the “liquid glass” direction described in `docs/TailwindStyleSystem.md`. Screenshot assets live under `docs/assets/task_dashboard/phase2/` for regression tracking.
