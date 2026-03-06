# Task UI Architecture

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-05

## 1. Purpose

Define the concrete implementation architecture for the MoonMind Mission Control.

This document contains UI-specific behavior and contracts:

- Routes and page responsibilities.
- Endpoint-level integrations.
- Task submit payload construction.
- Realtime/polling behavior.
- Task detail controls.

High-level system architecture intentionally lives in `docs/TaskArchitecture.md`.

### 1.1 Selected strategy

The dashboard follows a thin UI strategy over existing REST endpoints:

- Reuse queue/orchestrator/proposal/manifest APIs, plus compatibility adapters for newer contracts, instead of introducing a UI-specific backend.
- Keep dashboard concerns focused on typed submit forms, list/detail rendering, and operator controls.
- Minimize operational risk by avoiding high-churn architectural changes in task execution services.

### 1.2 Scope

In scope:

- Dedicated dashboard task operations UI under `/tasks*`.
- Unified list/detail views across queue and orchestrator sources, with compatibility redirects from older routes.
- Typed submit forms for queue task jobs, orchestrator tasks, and manifests.
- Detail pages with events/logs, artifacts, and supported controls.
- Polling refresh with queue detail SSE when available.

Out of scope:

- WebSocket migration for queue events (SSE + polling fallback is current design).
- Unified backend `runs` table across all workflow systems.
- Worker/fleet heartbeat model redesign.
- Open-WebUI plugin internals.
- Full Temporal-native UI replacement in this document; task compatibility surfaces remain the current product contract.

---

## 2. Implementation Snapshot

The current dashboard is a thin, server-hosted web app:

- HTML shell: `api_service/templates/task_dashboard.html`
- Client app: `api_service/static/task_dashboard/dashboard.js`
- Runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`
- Route shell router: `api_service/api/routers/task_dashboard.py`

Runtime config currently injects:

- Poll intervals: list `5000ms`, detail `2000ms`, events `1000ms`
- Source endpoint templates for queue/orchestrator/proposals/manifests
- Status normalization maps
- System defaults (queue name, runtime, model/effort defaults, default repository)
- Task template catalog feature-flag metadata

Default/fallback values in current code path:

- Default task runtime: first entry from supported runtime list (`codex`, `gemini`, and `claude` only when `ANTHROPIC_API_KEY` is configured)
- Default codex model: `gpt-5.3-codex`
- Default codex effort: `high`
- Default repository fallback: `MoonLadderStudios/MoonMind`

---

## 3. Route Map

Current target dashboard routes rendered by `dashboard.js`:

| Route | Purpose |
| --- | --- |
| `/tasks` | Compatibility alias that redirects to `/tasks/list` |
| `/tasks/list` | Unified task list across queue + orchestrator workloads |
| `/tasks/new` | Unified submit page (worker runtimes + orchestrator; defaults to queue runtime) |
| `/tasks/queue/new` | Alias to `/tasks/new` with worker default runtime; prefill mode uses `?editJobId=<jobId>` and resolves to edit or resubmit based on source status |
| `/tasks/:taskId` | Unified task detail shell resolved by source |
| `/tasks/queue/:jobId` | Compatibility alias to unified task detail with `source=queue` |
| `/tasks/orchestrator/new` | Alias to `/tasks/new?runtime=orchestrator` |
| `/tasks/orchestrator` | Compatibility alias to `/tasks/list?filterRuntime=orchestrator` |
| `/tasks/orchestrator/:runId` | Compatibility alias to unified task detail with `source=orchestrator` |
| `/tasks/manifests` | Manifest run list (queue jobs filtered by `type=manifest`) |
| `/tasks/manifests/new` | Manifest submit flow (inline or registry-backed) |
| `/tasks/proposals` | Proposal queue list and triage actions |
| `/tasks/proposals/:proposalId` | Proposal detail, promote/dismiss/priority/snooze actions |

Notes:

- Server route allowlist currently accepts manifest detail paths (`/tasks/manifests/{id}`), but the client router does not render a manifest detail page yet.
- The canonical list/detail experience is now `/tasks/list` and `/tasks/:taskId`.
- Queue and orchestrator legacy paths should redirect instead of remaining first-class pages.

---

## 4. Backend Integration Contracts

### 4.1 Queue endpoints (`/api/queue/*`)

Used by dashboard pages:

- `POST /api/queue/jobs`
- `GET /api/queue/jobs`
- `GET /api/queue/jobs/{job_id}`
- `PUT /api/queue/jobs/{job_id}`
- `POST /api/queue/jobs/{job_id}/resubmit`
- `POST /api/queue/jobs/{job_id}/cancel`
- `GET /api/queue/jobs/{job_id}/events`
- `GET /api/queue/jobs/{job_id}/events/stream`
- `GET /api/queue/jobs/{job_id}/artifacts`
- `GET /api/queue/jobs/{job_id}/artifacts/{artifact_id}/download`
- `GET /api/queue/telemetry/migration`

Queue detail operational controls:

- `GET /api/queue/jobs/{job_id}/live-session`
- `POST /api/queue/jobs/{job_id}/live-session`
- `POST /api/queue/jobs/{job_id}/live-session/grant-write`
- `POST /api/queue/jobs/{job_id}/live-session/revoke`
- `POST /api/queue/jobs/{job_id}/control`
- `POST /api/queue/jobs/{job_id}/operator-messages`

### 4.2 Orchestrator endpoints (`/orchestrator/*`)

- Preferred:
  - `POST /orchestrator/tasks`
  - `GET /orchestrator/tasks`
  - `GET /orchestrator/tasks/{task_id}`
  - `GET /orchestrator/tasks/{task_id}/artifacts`
  - `POST /orchestrator/tasks/{task_id}/approvals`
  - `POST /orchestrator/tasks/{task_id}/retry`
- Transitional compatibility:
  - `POST /orchestrator/runs`
  - `GET /orchestrator/runs`
  - `GET /orchestrator/runs/{run_id}`
  - `GET /orchestrator/runs/{run_id}/artifacts`
  - `POST /orchestrator/runs/{run_id}/approvals`
  - `POST /orchestrator/runs/{run_id}/retry`

### 4.3 Proposal endpoints (`/api/proposals/*`)

- `GET /api/proposals`
- `GET /api/proposals/{proposal_id}`
- `POST /api/proposals/{proposal_id}/promote`
- `POST /api/proposals/{proposal_id}/dismiss`
- `POST /api/proposals/{proposal_id}/priority`
- `POST /api/proposals/{proposal_id}/snooze`
- `POST /api/proposals/{proposal_id}/unsnooze`

### 4.4 Manifest endpoints

Queue-backed list/submit:

- `GET /api/queue/jobs?type=manifest&limit=200`
- `POST /api/queue/jobs` with `type="manifest"` (inline source flow)

Registry-backed submit:

- `GET /api/manifests`
- `POST /api/manifests/{name}/runs`

### 4.5 Task template catalog endpoints (`/api/task-step-templates/*`)

Used when `FEATURE_FLAGS__TASK_TEMPLATE_CATALOG=1`:

- `GET /api/task-step-templates`
- `GET /api/task-step-templates/{slug}`
- `POST /api/task-step-templates/{slug}:expand`
- `POST /api/task-step-templates/save-from-task`
- `POST /api/task-step-templates/{slug}:favorite`

### 4.6 Skills endpoint

- `GET /api/tasks/skills`  
  Used to populate runtime-aware skill suggestions. The response should support grouped worker and orchestrator skill catalogs.

---

## 5. Queue Task Submit: Detailed Contract

Queue Task submit is fully typed and emits canonical queue create requests (`type="task"`).

### 5.1 Form model

Current UI fields:

- Runtime (`supported runtimes`, currently `codex`, `gemini`, and `claude` only when `ANTHROPIC_API_KEY` is configured)
- Step editor (primary step plus optional additional steps)
- Optional task preset/template controls
- Model override
- Effort override
- Repository
- Starting branch
- New branch
- Publish mode (`pr` default, `branch`, `none`)
- Queue priority
- Max attempts

Primary step:

- Must include instructions.
- Skill id/args/capabilities map to `task.skill`.

Additional steps:

- Optional `id`, `title`, `instructions`, `skill`.
- Serialized into `task.steps` when non-empty.

### 5.2 Validation rules in UI

- At least one step must exist.
- Primary step instructions are required.
- Repository must be one of:
  - `owner/repo`
  - `https://<host>/<path>` without embedded credentials
  - `git@<host>:<path>`
- Runtime must be one of supported task runtimes.
- Publish mode must be `none`, `branch`, or `pr`.
- `priority` must be an integer.
- `maxAttempts` must be integer >= 1.
- Skill args (primary and per-step) must be JSON objects when provided.

### 5.3 Payload assembly behavior

The submit flow:

1. Derives `requiredCapabilities` from runtime + publish mode + skill capability extensions.
2. Builds canonical payload shape under `payload.task`.
3. Optionally includes:
   - `task.steps`
   - `task.appliedStepTemplates` metadata
4. Emits queue create envelope with `type`, `payload`, `priority`, and `maxAttempts`.

Security constraint:

- The dashboard form must remain token-free.
- Optional auth references are backend-governed; raw credentials are never entered in dashboard fields.

### 5.4 Queue prefill modes (`/tasks/queue/new?editJobId=<jobId>`)

- Queue detail resolves the source job into one of two actions:
  - `Edit` for queued, never-started task jobs.
  - `Resubmit` for failed/cancelled task jobs.
- Both modes preload job detail and reuse the same submit form.
- Edit mode:
  - primary action label: `Update`
  - submit target: `PUT /api/queue/jobs/{job_id}`
  - includes `expectedUpdatedAt` when available.
- Resubmit mode:
  - primary action label: `Resubmit`
  - submit target: `POST /api/queue/jobs/{job_id}/resubmit`
  - success redirects to the new job detail and surfaces `Resubmitted from <sourceJobId>`.
- Attachment edits/copy are out of scope for v1 and are not exposed in either mode.

Example emitted request body:

```json
{
  "type": "task",
  "priority": 0,
  "maxAttempts": 3,
  "payload": {
    "repository": "MoonLadderStudios/MoonMind",
    "requiredCapabilities": ["codex", "git", "gh"],
    "targetRuntime": "codex",
    "task": {
      "instructions": "Implement feature and add tests",
      "skill": {
        "id": "auto",
        "args": {}
      },
      "runtime": {
        "mode": "codex",
        "model": "gpt-5.3-codex",
        "effort": "high"
      },
      "git": {
        "startingBranch": null,
        "newBranch": null
      },
      "publish": {
        "mode": "pr",
        "prBaseBranch": null,
        "commitMessage": null,
        "prTitle": null,
        "prBody": null
      },
      "steps": [
        {
          "id": "step-1",
          "title": "Implement",
          "instructions": "Implement feature and add tests"
        }
      ]
    }
  }
}
```

Server-side normalization still applies after submit and remains source-of-truth.

---

## 6. Other Submit Flows

### 6.1 Orchestrator submit

Fields:

- Ordered step editor using the shared task step model
- `targetService` (required)
- `priority` (`normal` or `high`)
- Optional `approvalToken`
- Optional skill ids and args per step
- Queue-style publish/repo fields may remain visible when supported by the runtime flow

Request:

- Preferred: `POST /orchestrator/tasks`
- Compatibility: `POST /orchestrator/runs`

Behavior:

- If `steps[]` is provided, the dashboard submits explicit orchestrator task steps.
- If `steps[]` is omitted, the backend may preserve legacy autogenerated action-plan behavior.

### 6.2 Manifest submit

Fields:

- Manifest name
- Action (`run` or `plan`)
- Source kind (`inline` or `registry`)
- Optional options (`dryRun`, `forceFull`, `maxDocs`)
- Queue priority (inline flow)

Request routing:

- `sourceKind=inline`: `POST /api/queue/jobs` with `type="manifest"`
- `sourceKind=registry`: `POST /api/manifests/{name}/runs`

---

## 7. List Pages and Triage Flows

### 7.1 Unified task list (`/tasks/list`)

Client fan-out:

- `GET /api/queue/jobs?limit=200`
- `GET /orchestrator/tasks?limit=200`

Behavior:

- Normalizes queue and orchestrator rows into one shared task table
- Supports source/runtime/status filtering
- Keeps compatibility with older queue/orchestrator links through redirect routes
- May layer migration telemetry from `GET /api/queue/telemetry/migration?windowHours=168` where useful for queue-backed rows

### 7.2 Compatibility list aliases

- `/tasks` redirects to `/tasks/list`
- `/tasks/queue` redirects to `/tasks/list?source=queue`
- `/tasks/orchestrator` redirects to `/tasks/list?filterRuntime=orchestrator`

### 7.3 Manifest list (`/tasks/manifests`)

Behavior:

- Loads `GET /api/queue/jobs?type=manifest&limit=200`
- Renders rows as a dedicated "Manifests" source label.

### 7.4 Proposals list/detail (`/tasks/proposals*`)

List supports:

- status, repository, category filters
- include snoozed toggle
- inline promote/dismiss actions

Detail supports:

- promote
- edit + promote override flow
- dismiss
- priority update
- snooze and unsnooze
- similar proposal links

---

## 8. Unified Task Detail Behavior

Unified detail (`/tasks/:taskId`) combines:

- summary and state header
- source-aware timeline table
- artifact table/download links
- live output and operator controls where the underlying source supports them

### 8.1 Data loading

Primary source resolution:

- Use `?source=` when present
- Otherwise probe queue first, then orchestrator

Queue-backed fetches:

- `GET /api/queue/jobs/{job_id}`
- `GET /api/queue/jobs/{job_id}/artifacts`
- `GET /api/queue/jobs/{job_id}/live-session` (best-effort; handles missing route/state)

Orchestrator-backed fetches:

- `GET /orchestrator/tasks/{task_id}`
- `GET /orchestrator/tasks/{task_id}/artifacts`

Events:

- Initial load via `GET /events?limit=200&sort=desc`
- Incremental polling via `after` + `afterEventId`
- "Load older" pagination via `before` + `beforeEventId`
- SSE stream via `/events/stream` when available

### 8.2 Live output panel

Features:

- Follow-output toggle
- Output filters (`all`, `stages`, `logs`, `warnings`)
- Copy-to-clipboard
- Transport status indicator (`SSE` vs `Polling`)
- Full-log quick download when a log artifact is detected

Queue-backed detail retains:

- SSE or polling event transport
- live output filters
- live session state

Orchestrator-backed detail retains:

- action-plan or step timeline
- approval state and approval actions
- orchestrator artifacts and retry controls

### 8.3 Operator controls

Current source-aware control actions exposed:

- Queue-backed detail:
  - Cancel job
  - Enable live session
  - Grant write access (15 minutes)
  - Revoke live session
  - Pause/resume
  - Takeover request
  - Send operator message
- Orchestrator-backed detail:
  - Approve
  - Retry
  - Artifact refresh and timeline refresh

Queue-backed controls post through queue control/live-session endpoints. Orchestrator-backed controls post through `/orchestrator/tasks*` endpoints or their transitional `/orchestrator/runs*` aliases, then refresh detail state.

---

## 9. Status Normalization

Dashboard normalized statuses:

| Source | Raw status -> Normalized |
| --- | --- |
| Queue | `queued,pending -> queued`; `running -> running`; `succeeded,success,completed -> succeeded`; `failed,error,dead_letter -> failed`; `cancelled -> cancelled` |
| Orchestrator | `pending -> queued`; `running -> running`; `awaiting_approval -> awaiting_action`; `succeeded,rolled_back -> succeeded`; `failed -> failed` |
| Proposals | `open -> queued`; `promoted,accepted -> succeeded`; `dismissed -> cancelled`; `rejected -> failed` |

Unknown values use fallback logic (running/success/failure keyword checks, else queued).

---

## 10. Polling and Realtime

Current behavior:

- List pages poll every 5 seconds.
- Detail pages poll every 2 seconds.
- Queue event polling interval is 1 second.
- Polling skips while the document is hidden.
- Queue detail uses SSE first and falls back to polling when unavailable/error.

There is no generalized exponential backoff layer in current dashboard polling.

---

## 11. Authentication and Authorization

### 11.1 Dashboard/UI access

- `/tasks` shell routes require authenticated user context.
- `/api/tasks/skills` requires authenticated user context.

### 11.2 User vs worker API boundaries

- User-facing read/write endpoints (queue list/detail/create/cancel, proposals, manifests) are accessed with end-user auth context.
- Worker mutation endpoints (claim/heartbeat/complete/fail/event append/artifact upload) require worker identity (`X-MoonMind-Worker-Token` or OIDC worker auth).

### 11.3 Known auth gap

`/orchestrator/tasks*` and transitional `/orchestrator/runs*` routes do not yet enforce the same explicit `get_current_user()` dependency pattern used by queue/proposal/manifests routes. Alignment is still pending.

---

## 12. Known UX/Route Gaps

- `/tasks/manifests/{id}` passes server allowlist checks, but no client renderer is implemented yet.
- Active page focuses on queue + orchestrator fan-out; proposals and manifests remain dedicated pages.
- Proposal edit/promote and template save paths currently use prompt-driven UX; they are functional but not yet fully structured form experiences.

### 12.1 Action button styling contract

- Create/Promote/Dismiss flows use action-button classes (`.queue-action`, `.queue-submit-primary`) and derive color via `--queue-action-color`.
- Create/commit actions default `--queue-action-color` to `--mm-action-primary`; danger actions override to `--mm-danger`.
- Secondary actions (`Cancel`, `Back`, `View details`) use `.button.secondary` and retain neutral glass-surface styling.
- Full interaction and visual rules live in `docs/TaskDashboardStyleSystem.md`.

---

## 13. Related Documents

- `docs/TaskArchitecture.md`
- `docs/TaskQueueSystem.md`
- `docs/TaskProposalQueue`
- `docs/ManifestTaskSystem.md`
- `docs/TaskDashboardStyleSystem.md`
