# Mission Control Architecture

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-27

**Implementation tracking:** [`docs/tmp/remaining-work/UI-MissionControlArchitecture.md`](../tmp/remaining-work/UI-MissionControlArchitecture.md)

## 1. Purpose

Define the concrete architecture for the MoonMind Mission Control UI:
component tree, routing schema, runtime config, execution integration, action
mapping, and artifact flows.

Mission Control is a **task-oriented operator UI** over a **Temporal-backed
execution plane**.

## 2. Related Docs

- `docs/MoonMindArchitecture.md`
- `docs/Tasks/TaskArchitecture.md`
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/TaskExecutionCompatibilityModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/UI/MissionControlStyleGuide.md`

## 3. Implementation Snapshot

The dashboard is a thin, server-hosted web app built without heavy bundlers:

- HTML shell: `api_service/templates/task_dashboard.html`
- Client app: `api_service/static/task_dashboard/dashboard.js`
- Runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`
- Route shell: `api_service/api/routers/task_dashboard.py`

### 3.1 File Layout

```text
package.json
package-lock.json
tailwind.config.cjs
postcss.config.cjs

api_service/templates/
└── task_dashboard.html

api_service/static/task_dashboard/
├── dashboard.js
├── dashboard.tailwind.css   # source of truth
└── dashboard.css            # generated + served

tools/
└── build-dashboard-css.sh   # optional helper
```

## 4. Route Map

| Route | Purpose |
| --- | --- |
| `/tasks/list` | Primary task list for workflow executions |
| `/tasks/new` | Task submission form |
| `/tasks/queue/new` | Alias to `/tasks/new`; prefill mode uses `?editJobId=<jobId>` |
| `/tasks/{taskId}` | Canonical task detail route |
| `/tasks/proposals` | Proposal review queue |
| `/tasks/proposals/{proposalId}` | Proposal detail and actions |
| `/tasks/schedules` | Recurring schedule list |
| `/tasks/schedules/{scheduleId}` | Recurring schedule detail |

### 4.1 Query Parameters

Supported execution query parameters:

| Query parameter | Meaning |
| --- | --- |
| `source=temporal` | Explicit Temporal execution routing hint |
| `workflowType=` | Filter execution list by workflow type |
| `state=` | Filter by `mm_state` |
| `entry=` | Filter by `mm_entry` (`run`, `manifest`) |
| `ownerType=` | Operator/admin-only owner class filter |
| `ownerId=` | Admin-only owner filter |
| `nextPageToken=` | Temporal pagination token |
| `repo=` | Optional repo filter |
| `integration=` | Optional integration filter |

Ownership rules:

- standard users are implicitly scoped to their own executions
- `ownerType` and `ownerId` are operator/admin controls

## 5. Source Model

### 5.1 Execution source

Mission Control has one live execution source:

- `temporal`

### 5.2 Adjacent modules

The runtime config may also expose:

- `proposals`
- `schedules`
- `manifests`

These are **not** alternate execution substrates. They are adjacent product
modules that either create Temporal executions or inspect related control-plane
data.

### 5.3 Temporal is not a runtime choice

Temporal is the orchestration substrate, not a replacement for runtime choices
such as `codex`, `gemini_cli`, `claude_code`, or `jules`.

Rules:

- do **not** add `temporal` to the runtime picker
- do **not** overload the runtime picker to mean "execution engine"
- keep execution-engine routing invisible to normal product flows

### 5.4 Product vocabulary

- use **task** in primary UX
- use **workflow execution** in advanced/debug metadata
- never present Temporal Task Queues as a user-facing queue product

## 6. Runtime Config Contract

### 6.1 `sources.temporal` Block

```json
{
  "sources": {
    "temporal": {
      "list": "/api/executions",
      "create": "/api/executions",
      "detail": "/api/executions/{workflowId}",
      "update": "/api/executions/{workflowId}/update",
      "signal": "/api/executions/{workflowId}/signal",
      "cancel": "/api/executions/{workflowId}/cancel",
      "reschedule": "/api/executions/{workflowId}/reschedule",
      "artifacts": "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts",
      "artifactCreate": "/api/artifacts",
      "artifactMetadata": "/api/artifacts/{artifactId}",
      "artifactPresignDownload": "/api/artifacts/{artifactId}/presign-download",
      "artifactDownload": "/api/artifacts/{artifactId}/download"
    }
  }
}
```

### 6.2 Status mapping

Preferred UI contract for Temporal-backed rows/details:

- `dashboardStatus` for broad badges and filters
- `rawState` for exact MoonMind workflow state
- `temporalStatus` and `closeStatus` for advanced/detail views
- `waitingReason` and `attentionRequired` for blocked executions

Example client fallback mapping:

```json
{
  "temporal": {
    "scheduled": "queued",
    "initializing": "queued",
    "waiting_on_dependencies": "waiting",
    "planning": "running",
    "awaiting_slot": "queued",
    "executing": "running",
    "proposals": "running",
    "awaiting_external": "awaiting_action",
    "finalizing": "running",
    "completed": "completed",
    "failed": "failed",
    "canceled": "canceled"
  }
}
```

## 7. Detail View Lifecycle

When viewing `/tasks/{taskId}`, the dashboard reads the execution through the
control-plane APIs backed by Temporal and the execution projection layer.

Canonical fetch sequence:

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
3. optional artifact metadata/download calls as the user expands outputs

Rules:

- the route remains anchored to `taskId == workflowId`
- artifact fetch must use the latest `temporalRunId` returned by the detail API
- the detail page should surface finish outcomes such as `PUBLISHED_PR`,
  `NO_CHANGES`, or `FAILED` when available

### 7.1 Detail header fields

Temporal-backed detail should render:

- title
- normalized status badge
- summary
- workflow type
- runtime, model, and effort when present
- latest run metadata
- started/updated/closed timestamps

Advanced/debug views may also show:

- `workflowId`
- `temporalRunId`
- `namespace`
- `temporalStatus`
- `rawState`
- `closeStatus`
- `waitingReason`
- `attentionRequired`

### 7.2 Timeline posture

V1 detail behavior:

- show a synthesized summary/timeline panel
- treat artifacts as the primary durable evidence surface
- show update/signal/cancel actions when enabled
- defer raw Temporal event history browsing to dedicated backend contracts

## 8. List Page Integration

`/tasks/list` is the primary execution list surface.

Behavior:

- call `GET /api/executions`
- pass `workflowType`, `state`, `entry`, `ownerType`, `ownerId`, `pageSize`,
  `nextPageToken`, `repo`, and `integration` when applicable
- use returned `nextPageToken`, `count`, and `countMode` as-is
- sort primarily by `mm_updated_at` or `updatedAt`

### 8.1 Row model

| Dashboard field | Temporal source |
| --- | --- |
| `id` / `taskId` | `workflowId` |
| `source` | `temporal` |
| `sourceLabel` | `Temporal` |
| `title` | `memo.title` or fallback |
| `summary` | `memo.summary` |
| `workflowType` | `workflowType` |
| `entry` | `searchAttributes.mm_entry` |
| `status` | `dashboardStatus` |
| `rawState` | exact `state` |
| `temporalStatus` | `temporalStatus` |
| `closeStatus` | `closeStatus` |
| `ownerType` | `searchAttributes.mm_owner_type` |
| `ownerId` | `searchAttributes.mm_owner_id` |
| `repository` | `searchAttributes.mm_repo` |
| `integration` | `searchAttributes.mm_integration` |
| `waitingReason` | wait detail when blocked |
| `attentionRequired` | whether the UI should surface action-needed state |
| `startedAt` | `startedAt` |
| `updatedAt` | `updatedAt` / `mm_updated_at` |
| `closedAt` | `closedAt` |
| `workflowId` | `workflowId` |
| `temporalRunId` | latest run instance |

## 9. Action Mapping

| Dashboard action | Temporal API | Contract |
| --- | --- | --- |
| Create execution | `POST /api/executions` | Start `MoonMind.Run` or `MoonMind.ManifestIngest` |
| Edit inputs | `POST /api/executions/{workflowId}/update` | `updateName="UpdateInputs"` |
| Rename / retitle | `POST /api/executions/{workflowId}/update` | `updateName="SetTitle"` |
| Rerun | `POST /api/executions/{workflowId}/update` | `updateName="RequestRerun"` |
| Approve | `POST /api/executions/{workflowId}/signal` | `signalName="Approve"` |
| Pause | `POST /api/executions/{workflowId}/signal` | `signalName="Pause"` |
| Resume | `POST /api/executions/{workflowId}/signal` | `signalName="Resume"` |
| Cancel | `POST /api/executions/{workflowId}/cancel` | graceful by default |
| Reschedule | `POST /api/executions/{workflowId}/reschedule` | deferred execution only |

`ExternalEvent` remains part of the execution contract but is usually a
system/integration path rather than a direct human button.

## 10. Submit Integration

The submit form at `/tasks/new` creates Temporal executions directly through the
control-plane API.

Rules:

- do not add a visible "Temporal runtime" option
- keep submit flows organized around task product shapes
- let the backend decide workflow type and execution details

### 10.1 Redirect rules

- **Immediate execution:** `/tasks/{taskId}?source=temporal`
- **Deferred one-time:** `/tasks/{taskId}?source=temporal`
- **Recurring schedule:** `/tasks/schedules/{definitionId}`

### 10.2 Inline scheduling

The submit form supports:

- **Run immediately**
- **Schedule for later**
- **Set up recurring schedule**

The dashboard always posts to `POST /api/executions`; recurring mode may be
implemented by delegating server-side to the recurring-tasks service while
preserving the same product form.

## 11. Artifact Integration

Temporal-managed flows remain artifact-first for large inputs and outputs.

Required UI behaviors:

- create artifact placeholders
- upload content directly or via presigned endpoints
- complete uploads
- fetch artifact metadata
- fetch execution-scoped artifact lists
- download via authorized artifact endpoints

Rules:

- treat artifacts as immutable references
- prefer previews when available
- respect access-control metadata
- default to latest-run artifact views unless a prior-run browser is explicitly
  added later

## 12. Compatibility Rules

### 12.1 Identifier policy

For Temporal-backed task views:

- `taskId == workflowId`
- `workflowId` is the durable execution identifier
- `temporalRunId` is detail/debug metadata
- route resolution should use canonical server-side metadata, not ID-shape
  guessing

### 12.2 Product-language rule

Mission Control stays task-oriented in the main UX while preserving Temporal
terms for operator/debug views.

## 13. Open Questions

1. Should Mission Control eventually expose explicit run-history browsing for a
   single `workflowId`, or keep latest-run detail as the default?
2. Does `awaiting_action` need a more granular operator-visible `waitKind`
   distinction?
3. How much debug metadata should be shown by default versus behind an operator
   panel?
