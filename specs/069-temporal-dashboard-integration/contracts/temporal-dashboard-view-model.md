# Contract: Temporal Dashboard View Model

## Purpose

Define the runtime config, route-resolution, list/detail normalization, action, submit, and artifact presentation contracts required for Temporal-backed work inside the existing task dashboard.

## 1. Runtime Config Contract

`build_runtime_config(initial_path)` must expose Temporal dashboard settings under the existing dashboard config payload.

### 1.1 `sources.temporal`

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
      "artifacts": "/api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts",
      "artifactCreate": "/api/artifacts",
      "artifactMetadata": "/api/artifacts/{artifactId}",
      "artifactPresignDownload": "/api/artifacts/{artifactId}/presign-download",
      "artifactDownload": "/api/artifacts/{artifactId}/download"
    }
  }
}
```

Rules:

1. Endpoint templates are authoritative and server-generated.
2. The browser must not call Temporal services directly.
3. Artifact access must stay inside MoonMind authorization/download flows.

### 1.2 `statusMaps.temporal`

```json
{
  "statusMaps": {
    "temporal": {
      "initializing": "queued",
      "planning": "queued",
      "executing": "running",
      "awaiting_external": "awaiting_action",
      "finalizing": "running",
      "succeeded": "succeeded",
      "failed": "failed",
      "canceled": "cancelled"
    }
  }
}
```

Rules:

1. Normalized status is for dashboard grouping and badges only.
2. Raw lifecycle fields such as `rawState`, `temporalStatus`, and `closeStatus` must remain available to the UI.

### 1.3 `features.temporalDashboard`

```json
{
  "features": {
    "temporalDashboard": {
      "enabled": true,
      "listEnabled": true,
      "detailEnabled": true,
      "actionsEnabled": false,
      "submitEnabled": false,
      "debugFieldsEnabled": false
    }
  }
}
```

Rules:

1. These values are runtime settings, not hardcoded client defaults.
2. `actionsEnabled`, `submitEnabled`, and `debugFieldsEnabled` are rollout gates.
3. `enabled=false` disables all Temporal dashboard behavior.

### 1.4 `system.taskSourceResolver`

```json
{
  "system": {
    "taskSourceResolver": "/api/tasks/{taskId}/source"
  }
}
```

Rules:

1. The source resolver is the canonical contract for `/tasks/:taskId` source discovery.
2. `?source=temporal` remains an explicit override/fallback and should not replace server-side resolution as the documented contract.

## 2. Source Resolution Contract

### Endpoint

`GET /api/tasks/{taskId}/source`

### Response Shape

```json
{
  "taskId": "mm:workflow-123",
  "source": "temporal",
  "sourceLabel": "Temporal",
  "detailPath": "/tasks/mm:workflow-123?source=temporal"
}
```

Rules:

1. For Temporal-backed records, `taskId` equals `workflowId`.
2. Source resolution is ownership-aware and may return `404` for inaccessible resources.
3. Canonical dashboard detail remains `/tasks/{taskId}`.

## 3. Temporal List Contract

### Authoritative Temporal-only list

When `source=temporal`, the dashboard calls:

`GET /api/executions?workflowType=&state=&entry=&ownerType=&ownerId=&repo=&integration=&pageSize=&nextPageToken=`

Rules:

1. `nextPageToken`, `count`, and `countMode` are rendered exactly as returned.
2. `ownerType` and `ownerId` are operator/admin-only UI controls unless policy says otherwise.
3. Sorting uses Temporal-aware recency semantics, not queue-order semantics.

### Temporal row shape used by the dashboard

Required normalized row fields:

- `id`
- `taskId`
- `workflowId`
- `temporalRunId`
- `source`
- `sourceLabel`
- `title`
- `summary`
- `workflowType`
- `entry`
- `status`
- `rawState`
- `temporalStatus`
- `closeStatus`
- `ownerType`
- `ownerId`
- `repository`
- `integration`
- `waitingReason`
- `attentionRequired`
- `startedAt`
- `updatedAt`
- `closedAt`
- `link`

Rules:

1. `taskId == workflowId` for Temporal-backed rows.
2. `link` points to `/tasks/{taskId}?source=temporal`.
3. Mixed-source mode may merge normalized rows from multiple sources, but must not imply authoritative cross-source pagination.

## 4. Temporal Detail Contract

### Fetch sequence

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
3. Optional artifact metadata/download calls only when the user opens or downloads an artifact

Rules:

1. Artifact fetch must use the latest run ID from step 1.
2. Route identity stays anchored to `taskId == workflowId`.
3. v1 detail synthesizes timeline entries from execution fields rather than exposing raw Temporal history.

### Required detail fields

- normalized status badge
- title
- summary
- workflow type
- source label
- latest run metadata
- started/updated/closed timestamps
- `waitingReason`
- `attentionRequired`
- artifacts for the latest run
- debug fields only when enabled

## 5. Temporal Action Contract

### API mapping

| Dashboard action | Endpoint | Request contract |
|---|---|---|
| Set title | `POST /api/executions/{workflowId}/update` | `{"updateName":"SetTitle","title":"..."}` |
| Update inputs | `POST /api/executions/{workflowId}/update` | `{"updateName":"UpdateInputs", ...}` |
| Rerun | `POST /api/executions/{workflowId}/update` | `{"updateName":"RequestRerun"}` |
| Approve | `POST /api/executions/{workflowId}/signal` | `{"signalName":"Approve","payload":{...}}` |
| Pause | `POST /api/executions/{workflowId}/signal` | `{"signalName":"Pause"}` |
| Resume | `POST /api/executions/{workflowId}/signal` | `{"signalName":"Resume"}` |
| Cancel | `POST /api/executions/{workflowId}/cancel` | `{"graceful":true}` |

Rules:

1. Action visibility depends on current execution state and `features.temporalDashboard.actionsEnabled`.
2. UI copy remains task-oriented.
3. Detail must refresh after action completion or failure.

## 6. Submit Contract

Rules:

1. The standard runtime picker must not include `temporal`.
2. Eligible task-shaped submits may be backend-routed to Temporal when `features.temporalDashboard.submitEnabled` is true.
3. The dashboard sends reviewed task-safe fields and artifact refs; raw workflow-start internals remain backend concerns.
4. Successful Temporal-backed creates redirect to `/tasks/{taskId}?source=temporal`.

## 7. Artifact Presentation Contract

Rules:

1. Temporal detail uses execution-scoped artifact list APIs keyed by `namespace`, `workflowId`, and latest `temporalRunId`.
2. Preview/default-read references are preferred over raw bytes when available.
3. Downloads go through MoonMind artifact metadata/presign/download endpoints.
4. Artifact edits produce new artifact refs; bytes are not mutated in place.

## 8. Validation Gate

The implementation is valid only when automated coverage exists for:

1. runtime config export of Temporal source + feature flags,
2. source resolution and Temporal-safe route handling,
3. Temporal row normalization and mixed-source behavior,
4. Temporal-only pagination/count semantics,
5. detail fetch sequencing and latest-run artifact scoping,
6. action visibility and payload mapping,
7. submit redirect behavior while keeping Temporal out of the runtime picker.
