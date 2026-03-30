# Workflow Scheduling Guide

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-WorkflowSchedulingGuide.md`](../tmp/remaining-work/Temporal-WorkflowSchedulingGuide.md)

**Status:** Active
**Owner:** MoonMind Platform
**Last Updated:** 2026-03-30
**Audience:** Backend developers, UI developers, operators

## 1. Purpose

This guide covers how to start Temporal workflows at a specific time and how to create recurring schedules. It addresses both the backend API contracts and the Mission Control UI experience for each pattern.

## 2. Related Docs

- [TemporalArchitecture.md](TemporalArchitecture.md) — platform foundation and Temporal architecture
- [WorkflowTypeCatalogAndLifecycle.md](WorkflowTypeCatalogAndLifecycle.md) — workflow types, state model, update/signal contracts
- [MissionControlArchitecture.md](../UI/MissionControlArchitecture.md) — dashboard source model, route map, schedule source config
- [TaskExecutionCompatibilityModel.md](TaskExecutionCompatibilityModel.md) — task/workflow compatibility bridge
- [VisibilityAndUiQueryModel.md](VisibilityAndUiQueryModel.md) — query model and status mapping
- [ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md) — activity routing and worker fleet

---

## 3. Scheduling Patterns Overview

MoonMind supports three scheduling patterns for workflow execution:

| Pattern | Use Case | Backend Mechanism | UI Surface |
| --- | --- | --- | --- |
| **Immediate execution** | Run a workflow right now | `POST /api/executions` (no `schedule`) | `/tasks/new` → Submit |
| **Deferred one-time execution** | Run a workflow once at a specific future time | `POST /api/executions` with `schedule.mode=once` | `/tasks/new` → Schedule panel → "Run Once At" |
| **Recurring schedule** | Run a workflow on a repeating cadence | Temporal Schedule object | `/tasks/new` → Schedule panel → "Recurring" |

---

## 4. Temporal-Managed Scheduling (Canonical)

This section describes the canonical scheduling mechanisms for Temporal-managed workflows. These are the preferred paths for all new scheduling work.

### 4.1 One-Time Deferred Execution (`start_delay`)

The preferred mechanism for one-time deferred execution is Temporal's `start_delay` parameter on `client.start_workflow()`.

When `start_delay` is set:

- Temporal creates the workflow execution record immediately
- The workflow is visible in Temporal Visibility and Mission Control immediately
- The workflow is not dispatched to the task queue until the delay elapses
- Cancellation works — the user can cancel the workflow before it starts
- The detail page shows a "Scheduled to run at {time}" banner

#### Backend behavior

1. API validates `scheduledFor` is a valid future UTC timestamp.
2. API computes `start_delay = scheduledFor - now`.
3. API calls `TemporalClientAdapter.start_workflow()` with the `start_delay` parameter.
4. The execution record shows `mm_state=scheduled` until the start time.
5. In Visibility, the execution is immediately queryable with `state=scheduled`.
6. In Mission Control, it appears in the task list with `dashboardStatus=queued`.

#### Response

```json
{
  "workflowId": "mm:01HX...",
  "runId": "temporal-run-uuid",
  "workflowType": "MoonMind.Run",
  "state": "scheduled",
  "scheduledFor": "2026-03-19T02:00:00Z",
  "title": "Fix auth bug in login page",
  "startedAt": null,
  "redirectPath": "/tasks/mm:01HX...?source=temporal"
}
```

#### Task identity rule

For Temporal-backed deferred executions:

- `taskId == workflowId`
- the task appears in list views immediately with `state=scheduled` and `dashboardStatus=queued`
- the detail page anchors to `taskId == workflowId`

### 4.2 Recurring Schedules (Temporal Schedules)

The canonical mechanism for recurring workflow execution is **Temporal Schedules**.

Temporal Schedules are the authoritative recurring scheduling system for Temporal-managed workflows. They replace cron/beat-style external schedulers for Temporal-driven flows.

#### What a Temporal Schedule does

- defines a cron-like cadence
- starts a new workflow execution each time the schedule fires
- provides overlap, catchup, and jitter policies
- is manageable via Temporal CLI and API
- is visible in the Temporal UI and queryable through Temporal APIs

#### Backend behavior

1. API validates the cron expression, timezone, and policy.
2. API creates a Temporal Schedule object that starts a workflow execution on each cadence tick.
3. Each triggered workflow appears in Temporal Visibility as a normal execution with `mm_state=initializing` (or `scheduled` if `start_delay` is applied per-run).

#### Schedule policy options

| Policy | Description | Default |
| --- | --- | --- |
| `overlap` | `skip` prevents new runs while previous is active; `allow` permits concurrent | `skip` |
| `catchup` | `none` skips missed runs; `last` runs only the most recent; `all` backtracks | `last` |
| `jitter` | Random delay added to dispatch time to avoid thundering herd | `0` |

#### API endpoints for recurring schedules

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/recurring-tasks` | List schedule definitions |
| `POST` | `/api/recurring-tasks` | Create a new schedule |
| `GET` | `/api/recurring-tasks/{id}` | Get schedule detail |
| `PATCH` | `/api/recurring-tasks/{id}` | Update schedule |
| `POST` | `/api/recurring-tasks/{id}/run` | Trigger immediate manual run |
| `GET` | `/api/recurring-tasks/{id}/runs` | List run history |

#### Create payload

```json
{
  "name": "Nightly code scan",
  "description": "Run a security scan every night at 2 AM",
  "enabled": true,
  "cron": "0 2 * * *",
  "timezone": "America/Los_Angeles",
  "scopeType": "personal",
  "workflowType": "MoonMind.Run",
  "initialParameters": {
    "runtime": "gemini_cli",
    "model": "gemini-2.5-pro",
    "repository": "MoonLadderStudios/MoonMind"
  },
  "policy": {
    "overlap": "skip",
    "catchup": "last",
    "jitterSeconds": 30
  }
}
```

#### Key fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | Yes | Human-readable schedule name |
| `description` | `string` | No | Optional description |
| `enabled` | `bool` | No (default: `true`) | Whether the schedule is active |
| `cron` | `string` | Yes | Standard 5-field cron expression |
| `timezone` | `string` | No (default: `UTC`) | IANA timezone name |
| `scopeType` | `"personal"` or `"global"` | No (default: `"personal"`) | Scope — personal requires owner, global requires operator |
| `workflowType` | `string` | Yes | Workflow type to start (e.g., `MoonMind.Run`) |
| `initialParameters` | `object` | No | Runtime, model, effort, repository, publish mode |
| `policy` | `object` | No | Overlap, catchup, jitter policies |

#### Response

```json
{
  "scheduled": true,
  "definitionId": "def-uuid-123",
  "name": "Nightly code scan",
  "cron": "0 2 * * *",
  "timezone": "America/Los_Angeles",
  "nextRunAt": "2026-03-19T09:00:00Z",
  "redirectPath": "/tasks/schedules/def-uuid-123"
}
```

#### Scoping and authorization

| Scope | Who Can Manage | Visibility |
| --- | --- | --- |
| `personal` | Owner (matched by `owner_user_id`) | Only the owner |
| `global` | Operators (requires `is_superuser`) | All operators |

---

## 5. One-Time Workflow Execution (Immediate)

### 5.1 Concept

A one-time execution starts a single Temporal workflow and runs it through to completion. This is the standard path for ad-hoc tasks — the user submits a request and MoonMind starts a `MoonMind.Run` or `MoonMind.ManifestIngest` workflow.

### 5.2 Backend: Starting a One-Time Workflow

#### API Endpoint

```
POST /api/executions
```

#### Request Payload

```json
{
  "workflowType": "MoonMind.Run",
  "title": "Fix auth bug in login page",
  "inputArtifactRef": "art_abc123",
  "planArtifactRef": null,
  "manifestArtifactRef": null,
  "failurePolicy": null,
  "initialParameters": {
    "runtime": "gemini_cli",
    "model": "gemini-2.5-pro",
    "effort": "high",
    "repository": "MoonLadderStudios/MoonMind",
    "publishMode": "pr"
  },
  "idempotencyKey": null
}
```

**Key fields:**

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `workflowType` | `string` | Yes | `MoonMind.Run` or `MoonMind.ManifestIngest` |
| `title` | `string` | No | Human-readable title for the execution |
| `inputArtifactRef` | `string` | No | Reference to input artifact containing instructions |
| `planArtifactRef` | `string` | No | Pre-computed plan artifact |
| `manifestArtifactRef` | `string` | No | Required for `MoonMind.ManifestIngest` |
| `failurePolicy` | `string` | No | `"fail_fast"` or `"continue"` |
| `initialParameters` | `object` | No | Runtime, model, effort, repository, publish mode |
| `idempotencyKey` | `string` | No | Prevents duplicate starts |
| `schedule` | `object` | No | **Scheduling parameters** (see Section 4) |

#### What Happens on the Backend

1. **API service** receives the request, authenticates the caller, and validates the payload.
2. **If `schedule` is absent or null**, the execution starts immediately:
   - **`TemporalExecutionService.create_execution()`** generates a workflow ID (`mm:<ulid>`), persists a `TemporalExecutionRecord` in Postgres, and calls `TemporalClientAdapter.start_workflow()`.
   - **`TemporalClientAdapter.start_workflow()`** calls the Temporal SDK's `client.start_workflow()` with the workflow type, ID, task queue (`mm.workflow`), memo (title, summary), and search attributes (`mm_owner_id`, `mm_state`, `mm_entry`, `mm_updated_at`).
   - The **workflow worker** picks up the execution and drives it through the lifecycle: `initializing → planning → executing → proposals → finalizing → completed/failed`.
3. **If `schedule` is present**, the API routes to the scheduling path instead (see Section 4).

#### Response (Immediate Execution)

```json
{
  "workflowId": "mm:01HX...",
  "runId": "temporal-run-uuid",
  "workflowType": "MoonMind.Run",
  "state": "initializing",
  "title": "Fix auth bug in login page",
  "startedAt": "2026-03-18T21:48:00Z"
}
```

### 5.3 Mission Control UI: Submitting a One-Time Task

#### Route

```
/tasks/new
```

#### User Flow

1. User navigates to **New Task** from the dashboard sidebar or header.
2. The submit form offers:
   - **Instructions** text area
   - **Runtime** selector (Codex, Gemini CLI, Claude, Jules when enabled)
   - **Model** and **Effort** selection (runtime-specific defaults auto-populated)
   - **Repository** field
   - **Publish mode** (PR, direct commit, etc.)
   - **Attachments** (images, context files)
   - Optional **skill** or **step template** selection
   - **Schedule** panel (see Section 5.4 for details)
3. User clicks **Submit** (or **Schedule** when scheduling is configured).
4. The dashboard sends the request to the backend create endpoint. The backend determines the workflow type and handles scheduling if specified.
5. On success:
   - **Immediate execution:** redirect to `/tasks/{taskId}?source=temporal`
   - **Deferred one-time execution:** redirect to `/tasks/{taskId}?source=temporal` (shows scheduled banner)
   - **Recurring schedule:** redirect to `/tasks/schedules/{definitionId}`

#### Key UI Rules

- The UI does **not** offer a "Temporal" runtime option — Temporal is the orchestration substrate, not a runtime choice.
- The backend decides the workflow type and execution routing.
- For Temporal-backed executions, `taskId == workflowId`.
- After redirect, the detail page shows real-time status via polling.

### 5.4 Mission Control UI: Schedule Panel on Submit Form

The submit form at `/tasks/new` adds a **Schedule** panel that lets the user choose between immediate, deferred, and recurring execution.

#### UI Layout

The schedule panel appears below the main task fields and above the submit button:

```
┌─────────────────────────────────────────┐
│  When to run                            │
│                                         │
│  ( • ) Run immediately                  │
│  (   ) Schedule for later               │
│  (   ) Set up recurring schedule        │
│                                         │
│  ─── Shown when "Schedule for later" ── │
│  Date: [____-__-__]                     │
│  Time: [__:__]  Timezone: [_________▾]  │
│                                         │
│  ── Shown when "Recurring schedule" ──  │
│  Schedule name: [____________________]  │
│  Cron: [_______]  Timezone: [________▾] │
│  Preview: "Every weekday at 9:00 AM"    │
│                                         │
└─────────────────────────────────────────┘
│ [ Submit ]  or  [ Schedule ]            │
```

#### Behavior per Selection

| Selection | Submit button label | API payload | Redirect |
| --- | --- | --- | --- |
| **Run immediately** | "Submit" | No `schedule` field | `/tasks/{taskId}?source=temporal` |
| **Schedule for later** | "Schedule" | `schedule: { mode: "once", scheduledFor: "..." }` | `/tasks/{taskId}?source=temporal` (shows scheduled banner) |
| **Set up recurring schedule** | "Create Schedule" | `schedule: { mode: "recurring", ... }` | `/tasks/schedules/{definitionId}` |

#### Recurring Schedule Panel Fields

When the user selects "Set up recurring schedule", the panel expands to show:

1. **Schedule name** — auto-filled from task title, editable
2. **Cron expression** — text input with inline validation
3. **Cron preview** — human-readable label (e.g., "Every day at 2:00 AM Pacific")
4. **Timezone** — dropdown of common IANA timezones + search
5. **Enabled** — toggle (default: on)

Advanced policy options (overlap, catchup, jitter) are deferred to the schedule detail page after creation to keep the submit form simple.

#### Deferred One-Time Panel Fields

When the user selects "Schedule for later", the panel shows:

1. **Date picker** — calendar date
2. **Time picker** — hour and minute
3. **Timezone** — dropdown (defaults to browser timezone)
4. Combined values produce the `scheduledFor` ISO 8601 timestamp sent to the API.

> [!TIP]
> The timezone picker should default to the user's browser-detected timezone for the deferred one-time case, and to UTC for recurring schedules (matching existing behavior).

---

## 6. Scheduled Executions in Visibility and Mission Control

### 6.1 How deferred one-time executions appear

When a workflow is started with `start_delay`:

- it is immediately visible in Temporal Visibility with `mm_state=scheduled`
- Mission Control shows it in the task list with `dashboardStatus=queued`
- the detail page shows `state=scheduled` and a "Scheduled to run at {time}" indicator
- the execution transitions to `initializing` when the delay elapses

### 6.2 How recurring schedule runs appear

Each time a Temporal Schedule fires:

- a new workflow execution is created with a fresh `workflowId`
- `taskId == workflowId` for the newly created execution
- the execution starts with `mm_state=initializing` (or `scheduled` if per-run `start_delay` is applied)
- Schedule detail pages show the run history, linking each run to its task detail page

### 6.3 Schedule list and detail routes

| Route | Purpose |
| --- | --- |
| `/tasks/schedules` | List all recurring schedules (personal scope by default) |
| `/tasks/schedules/:scheduleId` | Schedule detail and run history |

---

## 7. Legacy Scheduling Compatibility

> [!NOTE]
> This section documents legacy scheduling infrastructure that may still exist in the codebase. It is not the canonical path for new scheduling work.

### 7.1 `RecurringTaskDefinition` / `RecurringTaskRun`

If `RecurringTaskDefinition` and `RecurringTaskRun` still exist in the codebase, they represent the earlier Postgres-backed scheduling infrastructure. This system was designed before Temporal Schedules became the canonical recurring mechanism.

Rules:

- new recurring scheduling work should use Temporal Schedules, not the Postgres-backed scheduler
- existing legacy schedules may continue to operate during migration
- the Postgres-backed scheduler should not be treated as the authoritative scheduling mechanism for Temporal-managed workflows

### 7.2 Queue-oriented target kinds

Legacy target kinds such as `queue_task`, `queue_task_template`, and `manifest_run` were designed for the earlier dispatch model. For Temporal-managed recurring work, the schedule target should reference a Temporal workflow type and start parameters, not queue-task dispatch objects.

---

## 8. Cron Expression Reference

The cron field uses standard 5-field POSIX cron syntax:

```
┌───────── minute (0–59)
│  ┌────── hour (0–23)
│  │  ┌─── day of month (1–31)
│  │  │  ┌ month (1–12)
│  │  │  │  ┌ day of week (0–7, where 0 and 7 = Sunday)
│  │  │  │  │
*  *  *  *  *
```

**Common examples:**

| Expression | Meaning |
| --- | --- |
| `0 2 * * *` | Every day at 2:00 AM |
| `*/15 * * * *` | Every 15 minutes |
| `0 9 * * 1-5` | Weekdays at 9:00 AM |
| `0 0 1 * *` | First of every month at midnight |
| `30 18 * * 5` | Every Friday at 6:30 PM |

> [!TIP]
> All cron times are interpreted in the schedule's configured `timezone`. When creating schedules via the API, ensure the timezone is a valid IANA name (e.g., `America/New_York`, `Europe/London`, `UTC`).

---

## 9. API Quick Reference

### Start a workflow immediately

```bash
curl -X POST http://localhost:8000/api/executions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "workflowType": "MoonMind.Run",
    "title": "Fix login bug",
    "initialParameters": {
      "runtime": "gemini_cli",
      "model": "gemini-2.5-pro",
      "repository": "MoonLadderStudios/MoonMind"
    }
  }'
```

### Start a deferred one-time workflow

```bash
curl -X POST http://localhost:8000/api/executions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "workflowType": "MoonMind.Run",
    "title": "Deploy staging environment",
    "initialParameters": {
      "runtime": "gemini_cli",
      "model": "gemini-2.5-pro",
      "repository": "MoonLadderStudios/MoonMind"
    },
    "schedule": {
      "mode": "once",
      "scheduledFor": "2026-03-19T02:00:00Z"
    }
  }'
```

### Create a recurring schedule

```bash
curl -X POST http://localhost:8000/api/recurring-tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "name": "Daily code review",
    "cron": "0 2 * * *",
    "timezone": "America/Los_Angeles",
    "scopeType": "personal",
    "workflowType": "MoonMind.Run",
    "initialParameters": {
      "runtime": "gemini_cli",
      "model": "gemini-2.5-pro",
      "repository": "MoonLadderStudios/MoonMind"
    }
  }'
```

### Trigger manual run

```bash
curl -X POST http://localhost:8000/api/recurring-tasks/$SCHEDULE_ID/run \
  -H "Authorization: Bearer $TOKEN"
```

### List run history

```bash
curl http://localhost:8000/api/recurring-tasks/$SCHEDULE_ID/runs?limit=50 \
  -H "Authorization: Bearer $TOKEN"
```

---

## 10. Runtime Config for Dashboard

The dashboard's schedule UI is powered by the `sources.schedules` block in the runtime config (built by `build_runtime_config()` in `task_dashboard_view_model.py`):

```json
{
  "sources": {
    "schedules": {
      "list": "/api/recurring-tasks?scope=personal",
      "create": "/api/recurring-tasks",
      "detail": "/api/recurring-tasks/{id}",
      "update": "/api/recurring-tasks/{id}",
      "runNow": "/api/recurring-tasks/{id}/run",
      "runs": "/api/recurring-tasks/{id}/runs?limit=200"
    }
  }
}
```

---

## 11. Troubleshooting

### Schedule is enabled but no runs appear

1. Verify the cron expression is valid and the next scheduled time is resolvable.
2. Check that the Temporal Schedule is active (use `temporal schedule describe`).
3. Confirm the overlap policy is not skipping runs due to a still-active previous execution.

### Deferred one-time workflow is not starting

1. Verify that `scheduledFor` was in the future when the workflow was created.
2. Check that the workflow exists in Temporal Visibility with `mm_state=scheduled`.
3. Confirm the workflow worker fleet is running and polling the `mm.workflow` queue.

### Timezone mismatches

All `scheduledFor` and `next_run_at` values are stored in UTC. Cron expressions are evaluated in the schedule's configured timezone. Verify the `timezone` field is a valid IANA timezone name, not an offset like `GMT-7`.
