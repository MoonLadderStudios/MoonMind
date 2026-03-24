# Workflow Scheduling Guide

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-WorkflowSchedulingGuide.md`](../tmp/remaining-work/Temporal-WorkflowSchedulingGuide.md)

**Status:** Active
**Owner:** MoonMind Platform
**Last Updated:** 2026-03-18
**Audience:** Backend developers, UI developers, operators

## 1. Purpose

This guide covers how to start Temporal workflows at a specific time and how to create recurring schedules. It addresses both the backend API contracts and the Mission Control UI experience for each pattern.

## 2. Related Docs

- [TemporalArchitecture.md](TemporalArchitecture.md) — platform foundation and migration phases
- [WorkflowTypeCatalogAndLifecycle.md](WorkflowTypeCatalogAndLifecycle.md) — workflow types, state model, update/signal contracts
- [MissionControlArchitecture.md](../UI/MissionControlArchitecture.md) — dashboard source model, route map, schedule source config
- [ActivityCatalogAndWorkerTopology.md](ActivityCatalogAndWorkerTopology.md) — activity routing and worker fleet

---

## 3. Scheduling Patterns Overview

MoonMind supports three scheduling patterns for workflow execution:

| Pattern | Use Case | Backend Mechanism | UI Surface |
| --- | --- | --- | --- |
| **Immediate execution** | Run a workflow right now | `POST /api/executions` (no `schedule`) | `/tasks/new` → Submit |
| **Deferred one-time execution** | Run a workflow once at a specific future time | `POST /api/executions` with `schedule.mode=once` | `/tasks/new` → Schedule panel → "Run Once At" |
| **Recurring schedule** | Run a workflow on a repeating cron cadence | `POST /api/executions` with `schedule.mode=recurring`, or `POST /api/recurring-tasks` | `/tasks/new` → Schedule panel → "Recurring", or `/tasks/schedules/new` |

> [!NOTE]
> The standalone `/api/recurring-tasks` API remains fully supported. The inline `schedule` parameter on the create endpoint is a convenience that unifies the submit experience — it delegates to the same recurring tasks service on the backend.

---

## 4. One-Time Workflow Execution

### 4.1 Concept

A one-time execution starts a single Temporal workflow and runs it through to completion. This is the standard path for ad-hoc tasks — the user submits a request and MoonMind starts a `MoonMind.Run` or `MoonMind.ManifestIngest` workflow.

### 4.2 Backend: Starting a One-Time Workflow

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
| `schedule` | `object` | No | **Scheduling parameters** (see Section 4.4) |

#### What Happens on the Backend

1. **API service** receives the request, authenticates the caller, and validates the payload.
2. **If `schedule` is absent or null**, the execution starts immediately:
   - **`TemporalExecutionService.create_execution()`** generates a workflow ID (`mm:<ulid>`), persists a `TemporalExecutionRecord` in Postgres, and calls `TemporalClientAdapter.start_workflow()`.
   - **`TemporalClientAdapter.start_workflow()`** calls the Temporal SDK's `client.start_workflow()` with the workflow type, ID, task queue (`mm.workflow`), memo (title, summary), and search attributes (`mm_owner_id`, `mm_state`, `mm_entry`, `mm_updated_at`).
   - The **workflow worker** (`temporal-worker-workflow`) picks up the execution and drives it through the lifecycle: `initializing → planning → executing → finalizing → succeeded/failed`.
3. **If `schedule` is present**, the API routes to the scheduling path instead (see Section 4.4).

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

### 4.3 Mission Control UI: Submitting a One-Time Task

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
   - **Schedule** panel (see Section 4.5 for details)
3. User clicks **Submit** (or **Schedule** when scheduling is configured).
4. The dashboard sends the request to the backend create endpoint. The backend determines the execution substrate (Temporal or queue) transparently and handles scheduling if specified.
5. On success:
   - **Immediate execution:** redirect to `/tasks/{taskId}?source=temporal`
   - **Scheduled execution:** redirect to the schedule detail page or show a confirmation with the scheduled time

#### Key UI Rules

- The UI does **not** offer a "Temporal" runtime option — Temporal is the orchestration substrate, not a runtime choice.
- The backend decides whether to route the submission to Temporal or the queue path.
- After redirect, the detail page shows real-time status via polling.

### 4.4 Inline Scheduling Parameters (Backend Design)

The `schedule` field on the create execution endpoint enables both one-time deferred and recurring execution from the same submit flow.

#### `schedule` Object Schema

```json
{
  "schedule": {
    "mode": "once | recurring",

    // --- Fields for mode=once ---
    "scheduledFor": "2026-03-19T02:00:00Z",

    // --- Fields for mode=recurring ---
    "name": "Nightly code scan",
    "description": "Run a security scan every night at 2 AM",
    "cron": "0 2 * * *",
    "timezone": "America/Los_Angeles",
    "enabled": true,
    "scopeType": "personal",
    "policy": {
      "overlap": { "mode": "skip", "maxConcurrentRuns": 1 },
      "catchup": { "mode": "last", "maxBackfill": 3 },
      "misfireGraceSeconds": 900,
      "jitterSeconds": 30
    }
  }
}
```

#### Field Reference

| Field | Type | Required | Applies to | Description |
| --- | --- | --- | --- | --- |
| `mode` | `"once"` or `"recurring"` | Yes | All | Whether to schedule a single deferred run or a repeating schedule |
| `scheduledFor` | `ISO 8601 datetime` | Yes (for `once`) | `once` | The UTC timestamp at which the workflow should execute |
| `name` | `string` | No | `recurring` | Schedule name (auto-derived from task title if omitted) |
| `description` | `string` | No | `recurring` | Optional description |
| `cron` | `string` | Yes (for `recurring`) | `recurring` | Standard 5-field cron expression |
| `timezone` | `string` | No (default: `UTC`) | `recurring` | IANA timezone |
| `enabled` | `bool` | No (default: `true`) | `recurring` | Whether the schedule starts enabled |
| `scopeType` | `"personal"` or `"global"` | No (default: `"personal"`) | `recurring` | Scope (global requires operator) |
| `policy` | `object` | No | `recurring` | Overlap, catchup, misfire, jitter policies (see Section 5.2) |

#### Backend Behavior by Mode

**`mode=once` (Deferred One-Time Execution)**

1. API validates `scheduledFor` is a valid future UTC timestamp.
2. API creates a `RecurringTaskDefinition` with a cron expression equivalent to the target time and `enabled=true`, then immediately creates a `RecurringTaskRun` for the specified time.
3. Alternatively (preferred path): the API uses Temporal's `start_delay` parameter on `client.start_workflow()` to defer the workflow start to the specified time without touching the recurring tasks system. The workflow appears in Mission Control immediately with a `scheduled` state.
4. Response includes the `workflowId` and the `scheduledFor` timestamp. The execution record shows `mm_state=scheduled` until the start time.

> [!IMPORTANT]
> **Preferred implementation: Temporal `start_delay`**
>
> The Temporal SDK's `start_workflow()` accepts a `start_delay` parameter (a `timedelta`). When set, Temporal creates the workflow execution record immediately but does not dispatch it to the task queue until the delay elapses. This is the cleanest path for one-time deferred execution because:
>
> - The workflow is immediately visible in Temporal Visibility and Mission Control (no "invisible waiting" period).
> - No intermediate recurring task infrastructure is needed.
> - Cancellation works — the user can cancel the workflow before it starts.
> - The detail page can show a "Scheduled to run at {time}" banner.
>
> Backend changes required:
> - Add `start_delay` parameter to `TemporalClientAdapter.start_workflow()`
> - Add `scheduled_for` field to `TemporalExecutionRecord`
> - Map `schedule.mode=once` + `schedule.scheduledFor` → `start_delay=scheduledFor - now`

**`mode=recurring` (Recurring Schedule from Create Flow)**

1. API validates the cron expression, timezone, and policy.
2. API constructs a target payload from the rest of the create request:
   - For task-shaped requests (`CreateJobRequest`): builds `target.kind=queue_task` with the task payload.
   - For Temporal-shaped requests (`CreateExecutionRequest`): builds `target.kind=queue_task` with the initial parameters mapped to a task payload.
3. API delegates to `RecurringTasksService.create_definition()` with the schedule fields and target payload.
4. Response returns a `ScheduleCreatedResponse` with the `definitionId`, `name`, `nextRunAt`, and a `redirectPath` to the schedule detail page.

#### Response Variants

**Immediate execution (no schedule):**

Standard `ExecutionModel` response (see Section 4.2).

**Deferred one-time (`schedule.mode=once`):**

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

**Recurring (`schedule.mode=recurring`):**

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

#### Full Example: Deferred One-Time Submit

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

#### Full Example: Recurring Submit from Create Endpoint

```bash
curl -X POST http://localhost:8000/api/executions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "type": "task",
    "payload": {
      "task": {
        "instructions": "Review open PRs and suggest improvements",
        "runtime": { "mode": "gemini_cli", "model": "gemini-2.5-pro" }
      },
      "repository": "MoonLadderStudios/MoonMind"
    },
    "schedule": {
      "mode": "recurring",
      "name": "Daily PR review",
      "cron": "0 9 * * 1-5",
      "timezone": "America/Los_Angeles"
    }
  }'
```

### 4.5 Mission Control UI: Schedule Panel on Submit Form

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

## 5. Recurring Workflow Schedules

### 5.1 Concept

A recurring schedule creates `RecurringTaskRun` records on a cron-based cadence. A Temporal-driven scheduler loop picks up due runs and dispatches them as queue jobs (which may route to Temporal workflows). Schedules are persisted in Postgres — they are MoonMind domain objects, not native Temporal Schedules (see Section 7 for the rationale).

### 5.2 Backend: Recurring Task Schedule API

#### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/recurring-tasks` | List schedule definitions |
| `POST` | `/api/recurring-tasks` | Create a new schedule |
| `GET` | `/api/recurring-tasks/{id}` | Get schedule detail |
| `PATCH` | `/api/recurring-tasks/{id}` | Update schedule |
| `POST` | `/api/recurring-tasks/{id}/run` | Trigger immediate manual run |
| `GET` | `/api/recurring-tasks/{id}/runs` | List run history |

#### Create Payload

```json
{
  "name": "Nightly code scan",
  "description": "Run a security scan every night at 2 AM",
  "enabled": true,
  "scheduleType": "cron",
  "cron": "0 2 * * *",
  "timezone": "America/Los_Angeles",
  "scopeType": "personal",
  "target": {
    "kind": "queue_task",
    "job": {
      "type": "task",
      "payload": {
        "instructions": "Run security scan on the main branch",
        "runtime": "gemini_cli",
        "model": "gemini-2.5-pro",
        "repository": "MoonLadderStudios/MoonMind"
      }
    }
  },
  "policy": {
    "overlap": {
      "mode": "skip",
      "maxConcurrentRuns": 1
    },
    "catchup": {
      "mode": "last",
      "maxBackfill": 3
    },
    "misfireGraceSeconds": 900,
    "jitterSeconds": 30
  }
}
```

#### Key Fields

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | `string` | Yes | Human-readable schedule name |
| `description` | `string` | No | Optional description |
| `enabled` | `bool` | No (default: `true`) | Whether the schedule is active |
| `scheduleType` | `string` | Yes | Currently only `"cron"` |
| `cron` | `string` | Yes | Standard 5-field cron expression |
| `timezone` | `string` | No (default: `UTC`) | IANA timezone name |
| `scopeType` | `"personal"` or `"global"` | No (default: `"personal"`) | Scope — personal requires owner, global requires operator |
| `target` | `object` | Yes | What to run (see target kinds below) |
| `policy` | `object` | No | Overlap, catchup, misfire, and jitter policies |

#### Target Kinds

| `target.kind` | Description | Required Fields |
| --- | --- | --- |
| `queue_task` | Submit a queue job with explicit payload | `target.job.type` = `"task"`, `target.job.payload` with instructions/runtime/model/repo |
| `queue_task_template` | Expand and run a saved task step template | `target.template.slug`, `target.template.version`, optional `target.template.inputs` |
| `manifest_run` | Trigger a manifest run | `target.name` (manifest name), optional `target.action` (`"run"` or `"plan"`), optional `target.options` |

#### Scheduling Policies

| Policy | Description | Default |
| --- | --- | --- |
| `overlap.mode` | `"skip"` prevents new runs while previous is active; `"allow"` permits concurrent | `"skip"` |
| `overlap.maxConcurrentRuns` | Max simultaneous runs when mode is `"allow"` | `1` |
| `catchup.mode` | `"none"` skips missed runs; `"last"` runs only the most recent; `"all"` backtracks | `"last"` |
| `catchup.maxBackfill` | Cap how many missed occurrences to backfill | `3` |
| `misfireGraceSeconds` | How long past the scheduled time a run may still dispatch | `900` (15 min) |
| `jitterSeconds` | Random delay added to dispatch time to avoid thundering herd | `0` |

#### Scoping and Authorization

| Scope | Who Can Manage | Visibility |
| --- | --- | --- |
| `personal` | Owner (matched by `owner_user_id`) | Only the owner |
| `global` | Operators (requires `is_superuser`) | All operators |

#### How the Scheduler Loop Works

1. A background loop (`schedule_due_definitions()`) runs periodically.
2. It queries enabled definitions whose `next_run_at` is past due.
3. For each due definition, it computes occurrences that were missed and applies the `catchup` policy.
4. It inserts one `RecurringTaskRun` row per selected occurrence (with `PENDING_DISPATCH` status and optional jitter).
5. A separate dispatch loop picks up `PENDING_DISPATCH` runs, checks `overlap` policy, and enqueues the target as a queue job via `AgentQueueService`.
6. The definition's `next_run_at` is advanced to the next future occurrence.

### 5.3 Mission Control UI: Schedule Management

#### Routes

| Route | Purpose |
| --- | --- |
| `/tasks/schedules` | List all recurring schedules (personal scope by default) |
| `/tasks/schedules/new` | Create a new recurring schedule |
| `/tasks/schedules/:scheduleId` | Schedule detail and run history |

#### Schedule List Page (`/tasks/schedules`)

The list page shows all schedules for the current user (personal scope) with:

- **Name** (links to detail page)
- **Target summary** (auto-generated from target kind/runtime)
- **Cron expression** and **timezone**
- **Status** badge (enabled/disabled)
- **Next run** timestamp
- **"New Schedule"** button for creation

#### Create Schedule Page (`/tasks/schedules/new`)

The form allows the user to define:

1. **Schedule name** — descriptive label
2. **Target type** selector:
   - **Queue Task** — inline task instructions, runtime, model, repository
   - **Task Template** — select a saved step template by slug
   - **Manifest Run** — select a registered manifest by name
3. **Cron expression** — five-field cron (e.g., `0 2 * * *`)
4. **Timezone** selector (defaults to `UTC`)
5. **Enabled** toggle

On submit, the form `POST`s to `/api/recurring-tasks` and redirects to the new schedule's detail page.

#### Schedule Detail Page (`/tasks/schedules/:scheduleId`)

The detail page shows:

- **Header**: name, description, enabled status, cron expression (with human-readable label), timezone, next run timestamp, creation and update timestamps
- **Actions**:
  - **Run Now** — triggers an immediate manual run (`POST /api/recurring-tasks/{id}/run`)
  - **Enable / Disable** — toggles the schedule (`PATCH /api/recurring-tasks/{id}`)
  - **Edit** — inline editing of name, description, cron, timezone
- **Run History** table: lists recent `RecurringTaskRun` records with:
  - `scheduledFor` timestamp
  - `trigger` (schedule vs manual)
  - `outcome` (pending_dispatch, enqueued, dispatch_error, etc.)
  - Link to the dispatched queue job when available

---

## 6. Cron Expression Reference

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

## 7. Architecture Note: MoonMind Schedules vs Temporal Schedules

MoonMind's recurring schedule system is currently implemented as a **Postgres-backed scheduler** rather than using Temporal's native Schedule primitive. The key reasons:

1. **Target flexibility** — MoonMind schedules can dispatch to queue tasks, step templates, and manifests. Not all targets are Temporal workflows yet.
2. **Policy richness** — The MoonMind scheduler provides overlap detection, catchup modes, misfire grace, jitter, and scope-based authorization — features that would need to be layered on top of Temporal Schedules.
3. **Migration compatibility** — During the Temporal migration, the scheduling system must support both Temporal-backed and queue-backed dispatch targets.

> [!IMPORTANT]
> As MoonMind completes its migration to Temporal as the primary execution substrate, the scheduler may evolve to use Temporal Schedules natively for Temporal-backed targets while preserving the current API surface. The target state from `TemporalArchitecture.md` states:
>
> > *Temporal Schedules replace cron/beat-style scheduling for Temporal-managed flows.*
>
> Until then, the Postgres-backed system is the authoritative scheduler.

---

## 8. API Quick Reference

### One-Time Execution

```bash
# Start a workflow immediately
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

### Create Recurring Schedule

```bash
# Create a daily schedule at 2 AM Pacific
curl -X POST http://localhost:8000/api/recurring-tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "name": "Daily code review",
    "cron": "0 2 * * *",
    "timezone": "America/Los_Angeles",
    "scopeType": "personal",
    "target": {
      "kind": "queue_task",
      "job": {
        "type": "task",
        "payload": {
          "instructions": "Review open PRs and suggest improvements",
          "runtime": "gemini_cli"
        }
      }
    }
  }'
```

### Trigger Manual Run

```bash
# Immediately run a schedule
curl -X POST http://localhost:8000/api/recurring-tasks/$SCHEDULE_ID/run \
  -H "Authorization: Bearer $TOKEN"
```

### List Run History

```bash
# View past runs for a schedule
curl http://localhost:8000/api/recurring-tasks/$SCHEDULE_ID/runs?limit=50 \
  -H "Authorization: Bearer $TOKEN"
```

---

## 9. Runtime Config for Dashboard

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

Dashboard JavaScript functions that implement the UI:

| Function | File Location | Purpose |
| --- | --- | --- |
| `renderSchedulesListPage()` | `dashboard.js` | Renders schedule list table |
| `renderScheduleCreatePage()` | `dashboard.js` | Renders create form with target type selector |
| `renderScheduleDetailPage(id)` | `dashboard.js` | Renders detail view with run history |

---

## 10. Troubleshooting

### Schedule is enabled but no runs appear

1. Verify the cron expression is valid and the `next_run_at` timestamp is in the past.
2. Check that the scheduler loop is running (look for `schedule_due_definitions` in API logs).
3. Confirm the `catchup.mode` is not `"none"` — that discards missed occurrences.

### Schedule dispatches but the job fails

1. Check the run history at `/tasks/schedules/:id` — the `outcome` field will show `dispatch_error` with a `message`.
2. Verify the target payload matches the expected schema for its kind.

### Overlap policy is skipping runs

If `overlap.mode` is `"skip"` (default), the scheduler will not dispatch a new run while a previous run is `PENDING_DISPATCH` or `ENQUEUED`. Wait for the previous run to complete, or change the policy to `"allow"`.

### Timezone mismatches

All `next_run_at` values are stored in UTC. The cron expression is evaluated in the schedule's configured timezone. Verify the `timezone` field is a valid IANA timezone name, not an offset like `GMT-7`.
