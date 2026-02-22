# Task UI Architecture

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-02-19

## 1. Purpose

Define the concrete implementation architecture for the MoonMind Tasks Dashboard.

This document contains UI-specific behavior and contracts:

- Routes and page responsibilities.
- Endpoint-level integrations.
- Task submit payload construction.
- Realtime/polling behavior.
- Run detail controls.

High-level system architecture intentionally lives in `docs/TaskArchitecture.md`.

### 1.1 Selected strategy

The dashboard follows a thin UI strategy over existing REST endpoints:

- Reuse queue/orchestrator/proposal/manifest APIs instead of introducing a UI-specific backend.
- Keep dashboard concerns focused on typed submit forms, list/detail rendering, and operator controls.
- Minimize operational risk by avoiding high-churn architectural changes in task execution services.

### 1.2 Scope

In scope:

- Dedicated dashboard task operations UI under `/tasks*`.
- Views for active, queued, and historical work by source.
- Typed submit forms for queue task jobs, orchestrator runs, and manifests.
- Detail pages with events/logs, artifacts, and supported controls.
- Polling refresh with queue detail SSE when available.

Out of scope:

- WebSocket migration for queue events (SSE + polling fallback is current design).
- Unified backend `runs` table across all workflow systems.
- Worker/fleet heartbeat model redesign.
- Open-WebUI plugin internals.

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

- Default task runtime: `codex` (unless configured runtime resolves to `gemini` or `claude`)
- Default codex model: `gpt-5.3-codex`
- Default codex effort: `high`
- Default repository fallback: `MoonLadderStudios/MoonMind`

---

## 3. Route Map

Current dashboard routes rendered by `dashboard.js`:

| Route | Purpose |
| --- | --- |
| `/tasks` | Unified active view across queue + orchestrator running/queued workloads |
| `/tasks/queue` | Queue job list with runtime/skill/status/publish filters |
| `/tasks/queue/new` | Typed queue Task submit form (`type="task"`) |
| `/tasks/queue/:jobId` | Queue job detail (summary, events, live output, artifacts, controls) |
| `/tasks/orchestrator` | Orchestrator run list |
| `/tasks/orchestrator/new` | Orchestrator submit form |
| `/tasks/orchestrator/:runId` | Orchestrator run detail |
| `/tasks/manifests` | Manifest run list (queue jobs filtered by `type=manifest`) |
| `/tasks/manifests/new` | Manifest submit flow (inline or registry-backed) |
| `/tasks/proposals` | Proposal queue list and triage actions |
| `/tasks/proposals/:proposalId` | Proposal detail, promote/dismiss/priority/snooze actions |

Notes:

- Server route allowlist currently accepts manifest detail paths (`/tasks/manifests/{id}`), but the client router does not render a manifest detail page yet.
- The "Active" page intentionally fans out to queue + orchestrator sources only.

---

## 4. Backend Integration Contracts

### 4.1 Queue endpoints (`/api/queue/*`)

Used by dashboard pages:

- `POST /api/queue/jobs`
- `GET /api/queue/jobs`
- `GET /api/queue/jobs/{job_id}`
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
  Used to populate queue submit skill suggestions.

---

## 5. Queue Task Submit: Detailed Contract

Queue Task submit is fully typed and emits canonical queue create requests (`type="task"`).

### 5.1 Form model

Current UI fields:

- Runtime (`codex`/`gemini`/`claude`)
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

- `instruction` (required)
- `targetService` (required)
- `priority` (`normal` or `high`)
- Optional `approvalToken`

Request:

- `POST /orchestrator/runs`

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

### 7.1 Active page (`/tasks`)

Client fan-out:

- `GET /api/queue/jobs?status=running&limit=200`
- `GET /api/queue/jobs?status=queued&limit=200`
- `GET /orchestrator/runs?status=running&limit=100`
- `GET /orchestrator/runs?status=pending&limit=100`
- `GET /orchestrator/runs?status=awaiting_approval&limit=100`

### 7.2 Queue list (`/tasks/queue`)

Behavior:

- Loads `GET /api/queue/jobs?limit=200`
- Loads migration telemetry from `GET /api/queue/telemetry/migration?windowHours=168`
- Supports client filters:
  - runtime
  - skill
  - normalized stage status
  - publish mode

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

## 8. Queue Detail Page Behavior

Queue detail (`/tasks/queue/:jobId`) combines:

- Job summary and cancellation state
- Event timeline table
- Live output panel
- Artifact table/download links
- Live session and operator controls

### 8.1 Data loading

Primary fetches:

- `GET /api/queue/jobs/{job_id}`
- `GET /api/queue/jobs/{job_id}/artifacts`
- `GET /api/queue/jobs/{job_id}/live-session` (best-effort; handles missing route/state)

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

### 8.3 Operator controls

Current control actions exposed:

- Cancel job
- Enable live session
- Grant write access (15 minutes)
- Revoke live session
- Pause/resume
- Takeover request
- Send operator message

All control actions post through queue control/live-session endpoints, then refresh detail/events.

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

`/orchestrator/*` routes currently do not enforce the same explicit `get_current_user()` dependency pattern used by queue/proposal/manifests routes. Alignment is still pending.

---

## 12. Known UX/Route Gaps

- `/tasks/manifests/{id}` passes server allowlist checks, but no client renderer is implemented yet.
- Active page focuses on queue + orchestrator fan-out; proposals and manifests remain dedicated pages.
- Proposal edit/promote and template save paths currently use prompt-driven UX; they are functional but not yet fully structured form experiences.

---

## 13. Related Documents

- `docs/TaskArchitecture.md`
- `docs/TaskQueueSystem.md`
- `docs/TaskProposalQueue`
- `docs/ManifestTaskSystem.md`
- `docs/TailwindStyleSystem.md`
