# Workflow Scheduling Guide

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-WorkflowSchedulingGuide.md`](../tmp/remaining-work/Temporal-WorkflowSchedulingGuide.md)  
**Status:** Active  
**Owner:** MoonMind Platform  
**Last Updated:** 2026-03-27  
**Audience:** Backend developers, UI developers, operators

## 1. Purpose

This guide covers how MoonMind starts Temporal workflows immediately, at a
future time, and on recurring schedules. It describes both the backend API
contracts and the Mission Control UI experience.

## 2. Related Docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalScheduling.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/UI/MissionControlArchitecture.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`

---

## 3. Scheduling Patterns Overview

MoonMind supports three scheduling patterns for workflow execution:

| Pattern | Use Case | Backend Mechanism | UI Surface |
| --- | --- | --- | --- |
| **Immediate execution** | Run a workflow now | `POST /api/executions` without `schedule` | `/tasks/new` |
| **Deferred one-time execution** | Run once at a future time | `POST /api/executions` with `schedule.mode=once` | `/tasks/new` schedule panel |
| **Recurring schedule** | Run on a repeating cadence | `POST /api/executions` with `schedule.mode=recurring`, delegating to recurring-tasks service | `/tasks/new` and `/tasks/schedules/*` |

> [!NOTE]
> The standalone `/api/recurring-tasks` API remains supported as the control
> plane for recurring schedule definitions. It is not a legacy execution queue;
> it creates and manages recurring schedules that launch Temporal executions.

---

## 4. Immediate and Deferred Execution

### 4.1 API endpoint

```text
POST /api/executions
```

### 4.2 Request shape

```json
{
  "workflowType": "MoonMind.Run",
  "title": "Fix auth bug in login page",
  "initialParameters": {
    "runtime": "gemini_cli",
    "model": "gemini-2.5-pro",
    "effort": "high",
    "repository": "MoonLadderStudios/MoonMind",
    "publishMode": "pr"
  },
  "schedule": {
    "mode": "once",
    "scheduledFor": "2026-03-19T02:00:00Z"
  }
}
```

### 4.3 Backend behavior

When `schedule` is absent:

1. the API authenticates and validates the request
2. `TemporalExecutionService.create_execution()` creates the execution record
3. `TemporalClientAdapter.start_workflow()` starts the workflow immediately
4. the workflow enters its normal lifecycle

When `schedule.mode=once`:

1. the API validates `scheduledFor`
2. it computes a Temporal `start_delay`
3. the workflow is created immediately but begins execution later
4. the execution remains visible in Mission Control with `state=scheduled`

### 4.4 Response examples

Immediate:

```json
{
  "workflowId": "mm:01HX...",
  "runId": "temporal-run-uuid",
  "workflowType": "MoonMind.Run",
  "state": "initializing",
  "title": "Fix auth bug in login page"
}
```

Deferred:

```json
{
  "workflowId": "mm:01HX...",
  "runId": "temporal-run-uuid",
  "workflowType": "MoonMind.Run",
  "state": "scheduled",
  "scheduledFor": "2026-03-19T02:00:00Z",
  "title": "Fix auth bug in login page",
  "redirectPath": "/tasks/mm:01HX...?source=temporal"
}
```

### 4.5 Mission Control submit behavior

The `/tasks/new` form supports:

- instructions
- runtime/model/effort selection
- repository and publish settings
- attachments
- optional schedule panel

Rules:

- the UI does **not** expose a "Temporal" runtime option
- the form always submits through the execution control plane
- immediate and deferred runs redirect to `/tasks/{taskId}?source=temporal`

---

## 5. Recurring Schedules

### 5.1 Concept

A recurring schedule is a control-plane definition that launches Temporal
workflow executions on a cron-based cadence.

MoonMind keeps a `RecurringTaskDefinition` domain record for ownership,
authorization, descriptive metadata, and target configuration. Execution-time
schedule behavior is reconciled into Temporal-native scheduling primitives.

### 5.2 Core recurring endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/recurring-tasks` | List schedule definitions |
| `POST` | `/api/recurring-tasks` | Create a schedule |
| `GET` | `/api/recurring-tasks/{id}` | Get schedule detail |
| `PATCH` | `/api/recurring-tasks/{id}` | Update schedule |
| `POST` | `/api/recurring-tasks/{id}/run` | Trigger immediate run |
| `GET` | `/api/recurring-tasks/{id}/runs` | List recent run history |

### 5.3 Target kinds

| `target.kind` | Meaning |
| --- | --- |
| `queue_task` | Task-shaped payload that starts a Temporal execution |
| `queue_task_template` | Run a task template via the normal task execution path |
| `manifest_run` | Trigger a manifest workflow |

> [!NOTE]
> `queue_task`, `queue_task_template`, and `manifest_run` are stable payload
> enum values used by the recurring schedule contract. All of them resolve
> through the Temporal execution control plane.

### 5.4 Run history

Recurring run history should surface:

- `scheduledFor`
- trigger type (`schedule` vs `manual`)
- outcome
- linked `workflowId` / `temporalRunId` when a run was launched

### 5.5 Mission Control schedule pages

Routes:

- `/tasks/schedules`
- `/tasks/schedules/{scheduleId}`

Expected UI behaviors:

- list schedules for the current scope
- show name, cadence, timezone, enabled status, and next run
- allow run-now, enable/disable, and edit actions
- show linked Temporal execution history for recent runs

---

## 6. Inline `schedule` Object

```json
{
  "schedule": {
    "mode": "once | recurring",
    "scheduledFor": "2026-03-19T02:00:00Z",
    "name": "Nightly code scan",
    "cron": "0 2 * * *",
    "timezone": "America/Los_Angeles",
    "enabled": true
  }
}
```

Field reference:

| Field | Required | Applies to | Description |
| --- | --- | --- | --- |
| `mode` | Yes | all | `once` or `recurring` |
| `scheduledFor` | Yes | `once` | UTC timestamp for deferred start |
| `name` | No | `recurring` | Schedule name |
| `cron` | Yes | `recurring` | Cron cadence |
| `timezone` | No | `recurring` | IANA timezone |
| `enabled` | No | `recurring` | Initial enabled state |

---

## 7. Scheduling Semantics

MoonMind uses Temporal-native time controls:

- **immediate:** normal workflow start
- **deferred once:** Temporal `start_delay`
- **reschedulable wait:** workflow timer plus signal/update pattern
- **recurring:** Temporal-backed recurring schedule definitions

No current scheduling contract should imply:

- an external scheduler loop outside the Temporal execution model
- a queue-dispatch daemon
- a second live execution substrate for scheduled runs

---

## 8. Cron Reference

The cron field uses standard 5-field POSIX cron syntax:

```text
┌───────── minute (0–59)
│  ┌────── hour (0–23)
│  │  ┌─── day of month (1–31)
│  │  │  ┌ month (1–12)
│  │  │  │  ┌ day of week (0–7, where 0 and 7 = Sunday)
│  │  │  │  │
*  *  *  *  *
```

Examples:

| Expression | Meaning |
| --- | --- |
| `0 2 * * *` | Every day at 2:00 AM |
| `*/15 * * * *` | Every 15 minutes |
| `0 9 * * 1-5` | Weekdays at 9:00 AM |

---

## 9. Dashboard Runtime Config

The dashboard's schedule UI is powered by the `sources.schedules` block:

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

## 10. Troubleshooting

### Schedule is enabled but no runs appear

1. Verify the cron expression and timezone are valid.
2. Check schedule detail and next-run metadata.
3. Confirm the recurring definition is enabled and owned by the expected scope.

### Scheduled execution exists but has not started yet

1. Confirm the execution is in `state=scheduled`.
2. Verify `scheduledFor` is still in the future.
3. Cancel or reschedule through the execution APIs if needed.

### Recurring run launches but the workflow fails

1. Inspect the linked `workflowId` in Mission Control.
2. Review finish summary and linked artifacts.
3. Correct the target payload or runtime settings and rerun.
