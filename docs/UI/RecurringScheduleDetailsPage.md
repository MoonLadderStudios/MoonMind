# Recurring Schedule Details Page

**Status:** Active
**Owner:** MoonMind Engineering
**Last Updated:** 2026-07-10
**Audience:** UI developers, backend developers, operators

## 1. Purpose

This document defines the dashboard detail page for recurring schedules. It complements the canonical scheduling docs by specifying what happens after a user selects a schedule from the recurring schedule list.

The design intent is simple: a recurring schedule detail page should feel like a close derivative of the normal workflow detail page, with only the differences required by the fact that the primary entity is a schedule definition rather than a single workflow execution.

## 2. Related Docs

- `docs/UI/CollectionWorkspaceLayout.md` — shared far-left workspace geometry and `EntityDetailFrame`.
- `docs/UI/WorkflowDetailsPage.md` — entity-detail content conventions shared with schedules.
- `docs/Temporal/WorkflowSchedulingGuide.md` — scheduling flows, API contracts, and runtime config
- `docs/Temporal/TemporalScheduling.md` — Temporal-native scheduling desired state
- `docs/UI/WorkflowConsoleArchitecture.md` — dashboard route model and workflow detail conventions

---

## 3. Requirements

1. The recurring schedule list at `/schedules` must make each schedule row navigable to `/schedules/{definitionId}`.
2. Clicking a recurring schedule opens the recurring schedule detail page for that schedule.
3. The recurring schedule detail page should reuse the normal workflow detail page composition as much as practical.
4. The detail page must support editing the schedule.
5. The detail page should be designed for deleting the schedule once the backend delete contract is available.
6. Schedule deletion, when implemented, must remove or disable future dispatch and then navigate the user back to the recurring schedule list.

---

## 4. Route Model

| Route | Purpose |
| --- | --- |
| `/schedules` | Recurring schedule list |
| `/schedules/{definitionId}` | Recurring schedule detail, controls, configuration, and run history |

`definitionId` is the stable MoonMind product identity for the schedule. It should remain stable across schedule edits and across workflow executions spawned by the schedule.

Navigation rules:

- The primary schedule name in each `/schedules` row is a link to `/schedules/{definitionId}`.
- New recurring schedule creation redirects to `/schedules/{definitionId}`.
- After deletion is available and succeeds, the UI redirects to `/schedules` and shows a success toast or banner.
- Run-history rows link to the spawned workflow execution detail route, `/workflows/{workflowId}?source=temporal`.

---

## 5. Page Composition

The recurring schedule detail page is a schedule adapter rendered through the same `EntityDetailFrame` as Workflow detail. The Recurring sidebar is the single contextual sidebar and a workspace sibling; it lists schedules only and is never mounted inside the frame or beside a page-navigation sidebar.

Reuse from workflow detail:

- page shell, header spacing, panel rhythm, facts rail, tabs, loading states, error states, and action-button placement
- status badge style and attention/error presentation
- route-level data loading and stale/refetch posture
- confirmation patterns for destructive actions

Minimal differences:

| Workflow detail concept | Recurring schedule detail equivalent |
| --- | --- |
| Workflow title | Schedule name |
| Workflow state | Schedule state: active, paused, disabled, or needs attention |
| Workflow ID / run ID | Schedule definition ID / Temporal Schedule ID |
| Runtime facts | Target workflow type, runtime, model, repository, publish mode |
| Start/update timing | Next run, last scheduled run, last dispatch result, last updated |
| Steps tab | Omitted by default; schedules do not have execution steps |
| Artifacts tab | Omitted by default; artifacts belong to spawned workflow runs |
| Runs tab | Schedule run history, linking each spawned run to workflow detail |
| Workflow actions | Schedule actions: edit, pause/resume, run now, and delete when the backend delete contract is available |

Run now acknowledges the request as "Requesting" while the API responds, then
shows a link for "Execution started", "Already running; no new execution
started", or "Waiting for execution evidence". Both detail and list row actions
refresh pending messages from durable run observations. An exhausted observation
shows the API's unconfirmed-acceptance message and schedule-history link before
retry, without presenting the workflow itself as failed. A lost HTTP response
retains the same idempotency key for retry; it is not a new launch request.
UUID generation also supports the established plain-HTTP LAN/VPN dashboard.

The page should not invent a completely separate visual language. Users should understand that a recurring schedule is a control plane object that repeatedly creates normal workflow executions.

---

## 6. Default Detail Layout

```text
┌──────────────────┬──────────────────────────┬───────────────────────────────────────────┐
│ Application rail │ Recurring sidebar        │ Shared EntityDetailFrame                  │
│ viewport far-left│ content-region far-left  │ breadcrumb + title/state + actions        │
│                  │                          │ summary/facts + tabs + main + facts rail  │
└──────────────────┴──────────────────────────┴───────────────────────────────────────────┘
```

The frame uses the same header spacing, panel rhythm, status family, action placement, summary strip, tabs, facts rail, loading states, error states, and responsive stacking as Workflow detail. Workflow-only steps, artifacts, logs, and remediation are not copied onto the schedule definition.

Default tab behavior:

- **Overview** shows the schedule summary, next run, cadence, timezone, target, policy, and latest dispatch status.
- **Runs** loads recent and historical runs from `/api/recurring-workflows/{definitionId}/runs`; each spawned run links to its Workflow detail page.
- **Configuration** shows editable schedule fields in read-only form until the user chooses **Edit schedule**.
- **Activity** is optional and may show audit events, reconciliation warnings, and the latest Temporal describe metadata when available.

---

## 7. Data Contract

The page needs the following schedule data:

| Field | Purpose |
| --- | --- |
| `id` / `definitionId` | Route key and product identity |
| `name` | Page title and editable schedule name |
| `description` | Page subtitle and editable description |
| `enabled` | Active/paused state |
| `cron` | Cadence configuration |
| `timezone` | Cadence timezone |
| `nextRunAt` | Summary card and overview fact |
| `lastScheduledFor` | Recent-run summary |
| `lastDispatchStatus` | Attention/error state |
| `lastDispatchError` | Error detail |
| `scopeType` / `scopeRef` | Legacy provenance only; never visibility or action authorization |
| `permissions` / `actions` | Backend-owned action availability and disabled reasons |
| `target` | Workflow type, runtime, model, repository, input refs |
| `policy` | Overlap, catchup, jitter |
| `temporalScheduleId` | Advanced/debug fact when available |
| `updatedAt` | Metadata and freshness display |

The page uses the existing schedule endpoints. Delete is surfaced only when the runtime config exposes the backend delete route.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/recurring-workflows/{definitionId}` | Load schedule detail |
| `PATCH` | `/api/recurring-workflows/{definitionId}` | Save schedule edits |
| `POST` | `/api/recurring-workflows/{definitionId}/run` | Trigger immediate manual run |
| `GET` | `/api/recurring-workflows/{definitionId}/runs` | Load run history |
| `DELETE` | `/api/recurring-workflows/{definitionId}` | Delete schedule and stop future dispatch |

Current runtime config exposes these templates under `sources.schedules`;
the [shared API example](./RecurringSchedulesPage.md#14-api-and-runtime-config-expectations)
records the producer and list-query compatibility behavior.
`sources.schedules.delete` is required for rendering the destructive delete action.

---

## 8. Edit Behavior

The primary edit path is an **Edit schedule** action on `/schedules/{definitionId}`.

Acceptable UI patterns:

- inline edit mode inside the Configuration tab
- right-side drawer
- modal dialog, if the form remains small

Editable fields:

- schedule name
- description
- enabled/paused state
- cron expression
- timezone
- overlap policy
- catchup policy
- jitter seconds
- target workflow parameters when supported by the backend contract

Save behavior:

1. Validate cron and timezone client-side for immediate feedback.
2. Submit a `PATCH /api/recurring-workflows/{definitionId}` request containing only changed fields when practical.
3. Keep the user on `/schedules/{definitionId}` after a successful save.
4. Refetch detail and run-history data after save.
5. Show validation and reconciliation errors inline without leaving the page.

The edit surface should not require users to return to `/workflows/new` to adjust an existing recurring schedule.

---

## 9. Delete Behavior

The delete action is destructive and should live in the detail page action area, visually separated from routine actions.

Required behavior:

1. The user chooses **Delete schedule** from `/schedules/{definitionId}`.
2. The UI shows a destructive confirmation dialog naming the schedule.
3. On confirmation, the client calls `DELETE /api/recurring-workflows/{definitionId}`.
4. The backend deletes or soft-deletes the MoonMind definition and deletes or pauses the corresponding Temporal Schedule so no future runs are dispatched.
5. Existing workflow executions spawned by the schedule are not deleted; they remain available under `/workflows/{workflowId}`.
6. On success, the UI redirects to `/schedules` and confirms the deletion.
7. On failure, the UI keeps the user on the detail page and shows the error.

The confirmation copy should make the distinction explicit: deleting a schedule stops future recurring runs, but it does not delete prior workflow executions or their artifacts.

---

## 10. Run History Relationship

A recurring schedule is not itself a workflow execution. It owns a series of workflow executions.

Rules:

- The schedule detail page shows schedule-owned run history.
- Each run row includes the spawned `workflowId`, scheduled time, actual start time when available, and link to workflow detail. **Dispatch** is the run's own `outcome` (pending dispatch, enqueued, skipped, dispatch error), which only says whether an execution was started. **Execution** is that workflow's recorded lifecycle state and derived outcome (for example Handed off or Verification blocked), shown with the Workflows list pill and read from the execution projection alongside the start time; it shows `-` until an execution is recorded.
- Clicking a run opens the normal workflow detail page.
- Workflow detail pages may show a compact `Created by schedule` provenance link back to `/schedules/{definitionId}` when that metadata is available.

This keeps the recurring schedule page focused on controlling the cadence and reviewing spawned runs, while preserving the normal workflow detail page as the place for execution-specific steps, artifacts, logs, and diagnostics.

---

## 11. Empty, Loading, and Error States

- If the schedule no longer exists, show a not-found state with a link back to `/schedules`.
- If the detail request succeeds but run history fails, keep the schedule controls available and show a localized runs-panel error.
- If update fails because reconciliation with Temporal failed, present the MoonMind API error and leave the page state unchanged.
- If delete is unavailable because runtime config omits `sources.schedules.delete`, do not render the delete action.
- If the schedule is disabled, the page should show it as paused/disabled rather than failed.
- If `lastDispatchStatus` or `lastDispatchError` indicates failure, the page should show an attention state but still allow edit, run now, and available destructive actions when authorized.

---

## 12. Authorization Rules

The recurring router owns instance visibility and action permissions. It still
admits requests through the predecessor `get_current_user()` dependency; this
does not imply migration to the account-free admission primitive.

- After admission, the operator can view and manage every instance schedule.
  Stored `ownerUserId`, `scopeType`, and `scopeRef` are legacy provenance, not
  personal/global authorization gates.
- The client uses returned `permissions` / `actions` (`canEdit`, `canRunNow`,
  `canDelete`, `disabledReasons`), never owner or scope fields, for action
  availability. Denied actions retain useful explanations; delete also requires
  `sources.schedules.delete`.
- Requests without admission must not expose schedule identities. Preserve
  existing admission, execution-state validation, and scoped machine authority
  as the shared authentication boundary changes.

---

## 13. Non-Goals

- Do not make schedule details a separate product surface with a unique design system.
- Do not render the Recurring sidebar inside the detail frame or a centered/max-width wrapper with a large left margin.
- Do not duplicate workflow execution steps, artifacts, logs, or diagnostics on the schedule page.
- Do not delete historical workflow executions when deleting a recurring schedule.
- Do not require a user to create a new schedule just to change a cadence or policy value.
