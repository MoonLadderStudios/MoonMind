# Task UI Technical Design - Strategy 1 Thin Dashboard

Status: Proposed  
Owners: MoonMind Engineering  
Last Updated: 2026-02-16

## 1. Decision

### Selected Strategy

Use Strategy 1: Thin Dashboard UI over existing REST endpoints.

### Why this is best for current project goals

- Show currently running tasks.
- Submit new tasks through typed forms.
- Minimize backend changes.
- Ship quickly with low operational risk.

MoonMind already has submit/list/detail primitives for:

- Agent Queue (`/api/queue/*`)
- Orchestrator runs (`/orchestrator/*`)

## 2. Scope

### In Scope

- Dedicated dashboard web app for task operations.
- Views for running, queued, and historical work.
- Typed submit forms for Agent Queue Tasks and Orchestrator runs.
- Detail pages with logs/events and artifacts.
- Polling-based refresh.

### Out of Scope

- SSE/WebSocket implementation in MVP.
- Unified backend `runs` table.
- Worker/fleet heartbeat model redesign.
- Open-WebUI plugin internals.

## 3. Backend Contract Reuse

### 3.1 Agent Queue

- `POST /api/queue/jobs`
- `GET /api/queue/jobs?status=&type=&limit=`
- `GET /api/queue/jobs/{job_id}`
- `GET /api/queue/jobs/{job_id}/events?after=&limit=`
- `GET /api/queue/jobs/{job_id}/artifacts`
- `GET /api/queue/jobs/{job_id}/artifacts/{artifact_id}/download`

### 3.2 Orchestrator

- `POST /orchestrator/runs`
- `GET /orchestrator/runs?status=&service=&limit=&offset=`
- `GET /orchestrator/runs/{run_id}`
- `GET /orchestrator/runs/{run_id}/artifacts`
- `POST /orchestrator/runs/{run_id}/approvals`
- `POST /orchestrator/runs/{run_id}/retry`

## 4. UI Architecture

### 4.1 App Structure

- `/tasks` consolidated running and queued view.
- `/tasks/queue` Agent Queue list.
- `/tasks/queue/new` create queue Task job (`type="task"`).
- `/tasks/queue/:jobId` queue job details.
- `/tasks/orchestrator` Orchestrator run list.
- `/tasks/orchestrator/new` create Orchestrator run.
- `/tasks/orchestrator/:runId` Orchestrator run details.

### 4.2 Shared UI Modules

- `RunStatusBadge`
- `RunTable`
- `ArtifactList`
- `EventTimeline`
- `TaskSubmitPanel` (typed fields; no free-form payload textarea in default flow)

## 5. Data Model in the UI

```ts
export type DashboardRun = {
  source: "queue" | "orchestrator";
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

## 6. Page-Level API Contracts

### 6.1 Consolidated Running/Queued Page (`/tasks`)

Client fan-out with parallel fetches:

- `GET /api/queue/jobs?status=running&limit=200`
- `GET /api/queue/jobs?status=queued&limit=200`
- `GET /orchestrator/runs?status=running&limit=100`
- `GET /orchestrator/runs?status=pending&limit=100`
- `GET /orchestrator/runs?status=awaiting_approval&limit=100`

### 6.2 Queue Submit (`/tasks/queue/new`)

UI submits typed Task values, then emits `CreateJobRequest`:

- `type: "task"`
- `payload: CanonicalTaskPayload`
- `priority: number` (default `0`)
- `affinityKey?: string`
- `maxAttempts?: number` (default `3`)

Typed UI fields (only `instructions` required):

- `instructions`
- `skill` (default `auto`)
- `skillArgs` (optional JSON object; default `{}`)
- `runtime` (default deployment runtime)
- `model`
- `effort`
- `repository`
- `startingBranch`
- `newBranch`
- `publishMode`

Example payload:

```json
{
  "repository": "owner/repo",
  "requiredCapabilities": ["git", "codex"],
  "targetRuntime": "codex",
  "auth": { "repoAuthRef": null, "publishAuthRef": null },
  "task": {
    "instructions": "Run tests and fix failures",
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

The form remains token-free. Optional auth references are backend-governed and
must never carry raw token values.

### 6.3 Queue Detail (`/tasks/queue/:jobId`)

- `GET /api/queue/jobs/{job_id}`
- `GET /api/queue/jobs/{job_id}/events?after=<lastSeenTimestamp>&limit=200`
- `GET /api/queue/jobs/{job_id}/artifacts`

### 6.4 Orchestrator Submit (`/tasks/orchestrator/new`)

- `instruction: string`
- `targetService: string`
- `approvalToken?: string`
- `priority?: normal|high`

## 7. Polling and Refresh Model

- Aggregated/list pages: poll every 5 seconds.
- Detail pages: poll every 2 seconds.
- Queue events: poll every 1 second with `after` cursor.
- Suspend polling when tab is hidden.
- Apply exponential backoff on `429`/`5xx`.

## 8. Authentication Model

### 8.1 UI-to-API auth

UI uses end-user auth context for submit/list/detail calls.

### 8.2 Worker token separation

Do not use `X-MoonMind-Worker-Token` in dashboard requests.

### 8.3 Security note

`/orchestrator` routes currently do not use `get_current_user()` dependencies. Align with queue/workflow routes before broad deployment.

## 9. Implementation Plan

### Phase 1 (MVP)

- Build all list/detail/submit pages.
- Implement status normalization and consolidated `/tasks`.
- Implement typed queue Task submit.

### Phase 2 (Usability)

- Add saved filters and table presets.
- Add validation/help text for runtime/skill/publish controls.
- Add per-page quick actions (retry, approve, copy IDs).

## 10. References

- `docs/TaskArchitecture.md`
- `docs/TaskUiArchitecture.md`
- `docs/CodexTaskQueue.md`
