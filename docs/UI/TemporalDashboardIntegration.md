# Temporal Dashboard Integration

Status: Draft  
Owners: MoonMind Engineering, MoonMind Platform  
Last Updated: 2026-03-06

## 1. Purpose

Define how the existing MoonMind Mission Control integrates **Temporal-managed executions** and related artifact flows without replacing the current task-oriented product surface in one step.

This document is intentionally **UI- and integration-focused**. It does not redefine the full Temporal architecture, worker topology, artifact storage internals, or the general Mission Control architecture. Instead, it defines:

- how Temporal-backed work appears inside `/tasks*`
- which routes, runtime config entries, and source adapters the dashboard needs
- how Temporal execution APIs map onto current dashboard list/detail/action patterns
- how Temporal-backed artifacts appear in task detail pages
- how the UI should evolve in phases while queue and orchestrator sources still exist

## 2. Related Docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/TaskExecutionCompatibilityModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/TaskArchitecture.md`
- `docs/TaskUiArchitecture.md`

This document is intentionally downstream of the Temporal compatibility and Visibility docs. Those documents own canonical identifier, status, Search Attribute, filter, token, and count semantics. This document only defines how the dashboard consumes those contracts.

## 3. Current Baseline

The current dashboard is a thin, server-hosted web app:

- HTML shell: `api_service/templates/task_dashboard.html`
- client app: `api_service/static/task_dashboard/dashboard.js`
- runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`
- route shell: `api_service/api/routers/task_dashboard.py`

Today, runtime config exposes dashboard sources for:

- `queue`
- `orchestrator`
- `proposals`
- `manifests`
- `schedules`

The current canonical routes remain:

- `/tasks/list`
- `/tasks/new`
- `/tasks/:taskId`

The current unified detail flow is source-aware but only supports queue and orchestrator detail behavior. The current Mission Control architecture also explicitly keeps task compatibility surfaces as the active product contract during migration.

Separately, the repo now already exposes Temporal-facing API surfaces:

- `/api/executions`
- `/api/executions/{workflow_id}`
- `/api/executions/{workflow_id}/update`
- `/api/executions/{workflow_id}/signal`
- `/api/executions/{workflow_id}/cancel`
- `/api/artifacts/*`
- `/api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

This document defines how the dashboard should consume those surfaces.

## 4. Selected Strategy

### 4.1 Keep the product surface task-oriented

During migration, the dashboard continues to present work primarily as **tasks**, even when the underlying execution substrate is Temporal.

Rules:

- Use **task** in the main dashboard UX.
- Use **workflow execution** in advanced/debug metadata and implementation-facing text.
- Do not expose Temporal Task Queues as a user-facing queue product.

### 4.2 Treat Temporal as another dashboard source

The dashboard should integrate Temporal-backed work as a **first-class source** alongside queue and orchestrator, rather than as a separate standalone UI.

Rules:

- Add a new source key: `temporal`.
- Keep the `/tasks*` shell as the primary navigation surface.
- Do not make the browser talk directly to Temporal Server or Temporal Web UI.
- Go through MoonMind REST APIs only.

### 4.3 Do not model Temporal as a worker runtime

Temporal is an orchestration substrate, not a replacement for `codex`, `gemini`, `claude`, `jules`, or `universal` runtime choices.

Rules:

- Do **not** add `temporal` as a worker runtime option.
- Do **not** overload the existing runtime picker to mean “execution engine.”
- If explicit engine selection is ever needed, use a distinct field such as `engine` or a feature-flagged advanced control.
- Prefer invisible backend routing where possible: the user submits a task-shaped request, and the backend decides whether the execution is queue-backed, orchestrator-backed, or Temporal-backed.

## 5. Scope and Non-Goals

### 5.1 In scope

- Runtime config additions for Temporal-backed list/detail/actions.
- Route handling changes required for Temporal-backed task detail.
- Temporal list/detail normalization into shared dashboard row/detail models.
- Mapping dashboard controls onto Temporal create/update/signal/cancel behavior.
- Temporal artifact presentation in task detail pages.
- Phased migration behavior for mixed-source dashboards.

### 5.2 Out of scope

- Direct embedding of Temporal Web UI.
- Direct browser access to Temporal Server APIs.
- Worker topology, versioning, or queue segmentation details.
- Artifact storage backend internals.
- Full recurring schedule UI integration with Temporal Schedule objects.
- Raw Temporal event-history browsing in the dashboard v1.

## 6. Runtime Config Contract

`build_runtime_config()` should add a new source entry for Temporal-backed executions. If the dashboard still relies on client-side compatibility mapping during migration, it may also expose a transitional Temporal status map, but adapter-supplied normalized fields remain preferred.

### 6.1 New `sources.temporal` block

Proposed runtime config contract:

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

### 6.2 Transitional `statusMaps.temporal`

Preferred UI contract for Temporal-backed rows/details:

- `dashboardStatus` is the compatibility status used for list badges and broad filters.
- `rawState` preserves the exact MoonMind workflow state.
- `temporalStatus` and `closeStatus` remain available for advanced/detail views.
- `waitingReason` and `attentionRequired` remain available whenever `rawState=awaiting_external`.

If the dashboard still needs a client-side fallback mapping during migration, it should mirror the canonical compatibility mapping owned by `docs/Temporal/VisibilityAndUiQueryModel.md`:

```json
{
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
```

Notes:

- Prefer server-supplied normalized fields over recreating dashboard semantics in `dashboard.js`.
- The dashboard must preserve `rawState`, `temporalStatus`, and `closeStatus` even when it renders a broader `dashboardStatus`.
- `awaiting_external` does not automatically mean the current user must act; pair the compatibility status with `waitingReason` and `attentionRequired` so the UI does not mislead operators.

### 6.3 Feature flags

Recommended runtime flags:

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

Recommended rollout order:

1. list/detail read-only
2. actions
3. submit flows
4. optional debug metadata

## 7. Route Model

### 7.1 Keep existing canonical routes

Temporal integration should **not** introduce a separate main dashboard namespace.

Canonical routes remain:

| Route | Purpose |
| --- | --- |
| `/tasks/list` | Unified mixed-source list including Temporal-backed rows when enabled |
| `/tasks/new` | Main task submit page |
| `/tasks/:taskId` | Unified task detail shell |

### 7.2 Temporal detail routing requirement

The current dashboard route shell still assumes queue/orchestrator-era identifier patterns in a few places.

That is a problem for Temporal-backed detail because canonical task handles for Temporal-backed work are `taskId == workflowId`, and Workflow IDs are opaque identifiers that may use `mm:`-style values.

Required change:

- route `/tasks/:taskId` through canonical server-side source resolution metadata rather than ID-shape probing
- accept safe Temporal-compatible task IDs in the route shell so canonical routes remain reachable
- keep any source-specific alias route optional and clearly secondary

Recommendation:

- keep `/tasks/:taskId` as the canonical product route
- use a persisted source mapping / global task index to resolve `taskId` to `queue`, `orchestrator`, or `temporal`
- widen the route allowlist to accept safe Temporal-compatible identifiers as an implementation detail, not as the compatibility contract
- optionally add `/tasks/executions/:workflowId` as an internal/debug alias that redirects to `/tasks/:taskId?source=temporal`

### 7.3 Query parameters

Supported query parameters for Temporal integration:

| Query parameter | Meaning |
| --- | --- |
| `source=temporal` | Force Temporal source resolution for list/detail |
| `workflowType=` | Filter Temporal list by workflow type when source is Temporal |
| `state=` | Filter Temporal list by `mm_state` |
| `entry=` | Filter Temporal list by `mm_entry` (`run`, `manifest`) |
| `ownerType=` | Operator/admin-only owner class filter |
| `ownerId=` | Admin-only filter passthrough when allowed by API policy |
| `nextPageToken=` | Temporal-only pagination token |
| `repo=` | Optional repo-scoped filter when exposed by API policy |
| `integration=` | Optional integration filter when exposed by API policy |

Ownership rules:

- standard user task pages should be implicitly scoped to the authenticated principal
- the default end-user UI should not expose an arbitrary owner picker
- `ownerType` / `ownerId` filters are operator/admin controls, not general-purpose user-facing filters

## 8. List Page Integration

### 8.1 Mixed-source list behavior

`/tasks/list` remains the main list surface.

When `source` is not pinned, the client may merge rows from:

- queue
- orchestrator
- temporal

Rules:

- mixed-source list mode is a **product convenience view**, not an authoritative globally paginated dataset
- the dashboard may fetch bounded slices per source and merge-sort client-side
- mixed-source totals are informational only and should not claim exact global counts
- exact Temporal pagination and counts should only be surfaced when the user filters to `source=temporal`

### 8.2 Temporal-only list behavior

When `source=temporal`, the dashboard should treat Temporal as the authoritative source for that view.

Behavior:

- call `GET /api/executions`
- pass `workflowType`, `state`, `entry`, `ownerType`, `ownerId`, `pageSize`, and `nextPageToken` where applicable
- use the returned `nextPageToken`, `count`, and `countMode` as-is
- avoid pretending that queue/orchestrator records are part of the same exact paginated result set

### 8.3 Row model for Temporal-backed items

Temporal rows normalized into the dashboard table should provide at least:

| Dashboard field | Temporal source |
| --- | --- |
| `id` / `taskId` | `workflowId` |
| `source` | `temporal` |
| `sourceLabel` | `Temporal` |
| `title` | `memo.title` or fallback from workflow type |
| `summary` | `memo.summary` |
| `workflowType` | `workflowType` |
| `entry` | `searchAttributes.mm_entry` if present |
| `status` | `dashboardStatus` |
| `rawState` | exact `state` |
| `temporalStatus` | `temporalStatus` |
| `closeStatus` | `closeStatus` when present |
| `ownerType` | `searchAttributes.mm_owner_type` |
| `ownerId` | `searchAttributes.mm_owner_id` |
| `repository` | `searchAttributes.mm_repo` when present |
| `integration` | `searchAttributes.mm_integration` when present |
| `waitingReason` | bounded wait reason when `rawState=awaiting_external` |
| `attentionRequired` | whether current product surface expects operator/user action |
| `startedAt` | `startedAt` |
| `updatedAt` | `updatedAt` or `searchAttributes.mm_updated_at` |
| `closedAt` | `closedAt` |
| `workflowId` | `workflowId` |
| `temporalRunId` | latest Temporal run instance ID |

### 8.4 Sorting

For Temporal-backed rows:

- primary sort should be `mm_updated_at` when available
- fallback sort should be `updatedAt`
- deterministic tie-breaker should be `workflowId DESC`
- fallback of last resort is `startedAt`

For mixed-source views:

- sort on a normalized timestamp field shared across sources
- do not imply Temporal queue-order semantics

## 9. Detail Page Integration

### 9.1 Source resolution

Unified detail remains `/tasks/:taskId`.

Resolution order:

1. if `?source=temporal`, resolve against Temporal only
2. otherwise resolve via canonical server-side source mapping for `taskId`
3. keep source-probing heuristics, if any remain temporarily, as an implementation fallback rather than the documented contract
4. when a row originated from the Temporal list, generated links may continue including `?source=temporal` until source mapping is fully in place

### 9.2 Temporal detail fetch sequence

Minimum fetch sequence for Temporal-backed detail:

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
3. optional per-artifact metadata/download calls when the user expands or downloads an artifact

Important rule:

- artifact list fetch must use the **latest `temporalRunId` from the execution detail response**, not a stale run ID cached from an earlier list row
- the detail route stays anchored to `taskId == workflowId` even when `temporalRunId` changes across rerun or Continue-As-New behavior

### 9.3 Detail header model

Temporal-backed detail should render:

- title
- normalized status badge
- summary
- workflow type label
- latest run metadata
- source label (`Temporal`)
- started/updated/closed timestamps

Advanced/debug fields may optionally show:

- `workflowId`
- latest `temporalRunId`
- `namespace`
- raw `temporalStatus`
- raw `rawState`
- raw `closeStatus`
- `waitingReason`
- `attentionRequired`

### 9.4 Timeline / event model

The current dashboard has queue event transport and orchestrator timelines. The current Temporal API surface does **not** yet provide a dashboard-oriented event-history endpoint.

Therefore v1 detail behavior for Temporal-backed work should be:

- show a summary/timeline panel synthesized from execution fields and known state transitions
- surface `waitingReason` and `attentionRequired` when the execution is blocked externally
- show artifacts as the main durable evidence surface
- show update/signal/cancel actions when enabled
- defer raw Temporal event history browsing until a dedicated backend contract exists

Non-goal for v1:

- exposing raw Temporal history JSON directly in the dashboard

## 10. Action Mapping

### 10.1 Supported Temporal actions

Current API action mapping:

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

`ExternalEvent` is part of the execution contract but is normally a system/integration action rather than a direct human dashboard button.

### 10.2 Initial UI action matrix

Recommended v1 action exposure:

| Temporal state | Allowed actions |
| --- | --- |
| `initializing` / `planning` | cancel, set title |
| `executing` | cancel, pause, set title |
| `awaiting_external` | cancel, pause, resume, approve when applicable |
| `finalizing` | cancel only if policy allows |
| terminal (`succeeded`, `failed`, `canceled`) | rerun, view/download artifacts |

### 10.3 Copy guidance

Use UI copy that matches current task language:

- prefer **Rerun** instead of “Continue-As-New”
- prefer **Task title** instead of “workflow memo title”
- prefer **Pause task** / **Resume task** instead of Temporal-internal terminology

Advanced/debug views may disclose the underlying Temporal terms.

## 11. Submit Integration

### 11.1 Default posture

Initial Temporal dashboard integration should be **read-first**.

Phased order:

1. list/detail visibility
2. task actions on existing Temporal-backed executions
3. direct submit from `/tasks/new`

### 11.2 Submit UX rule

When submit support is added:

- do not add a visible “Temporal runtime” option to the standard runtime picker
- keep submit flows organized around current task product shapes rather than internal orchestration engine
- let the backend decide whether the request starts a Temporal execution

### 11.3 Backend mapping for initial Temporal-backed submit flows

The dashboard should continue to submit task-shaped requests and artifact references. The backend compatibility layer may then map those requests onto Temporal workflow starts.

Initial expected mappings:

- run-shaped submit flows may start `MoonMind.Run`
- manifest-oriented submit flows may start `MoonMind.ManifestIngest`
- `workflowType`, `idempotencyKey`, `failurePolicy`, and `initialParameters` are backend/API contract details, not primary user-facing dashboard concepts
- the dashboard should focus on reviewed task-safe fields and artifact references, not on exposing raw workflow-start payload structure

### 11.4 Redirect behavior after create

After a successful Temporal-backed create:

- redirect to `/tasks/{taskId}?source=temporal`
- for Temporal-backed records, `taskId` should equal `workflowId`
- internal state may additionally retain `temporalRunId`, but the canonical route should remain task-oriented and stable across reruns

## 12. Artifact Integration

### 12.1 General posture

Temporal-managed flows should remain artifact-first for large inputs and outputs.

Dashboard implications:

- large user inputs should upload as artifacts before create or update operations when needed
- task detail should show execution-linked artifacts as the main durable output surface
- downloads should go through MoonMind artifact authorization and grant flows

### 12.2 Required artifact behaviors

The dashboard should support:

- create artifact placeholder
- upload content directly or through presigned part upload
- complete upload
- fetch artifact metadata
- fetch execution-scoped artifact lists
- download through presigned or direct endpoints

### 12.3 Presentation rules

Artifact presentation on Temporal-backed detail should follow these rules:

- render artifact metadata and labels from execution linkage when available
- prefer preview flows when `preview_artifact_ref` exists
- respect `raw_access_allowed` and `default_read_ref`
- do not assume all artifacts are safe for inline display
- treat artifacts as immutable references; editing an input means producing a new artifact and updating references, not mutating bytes in place

### 12.4 Run scoping

Execution-scoped artifact listing is currently keyed by:

- `namespace`
- `workflowId`
- `temporalRunId`

That means Temporal detail should default to showing artifacts for the **latest run**. If prior-run artifact browsing is needed later, it should become an explicit detail feature rather than an implicit mixed-run view.

## 13. Compatibility Rules

### 13.1 Vocabulary

- user-facing primary term: **task**
- advanced/debug term: **workflow execution**
- never present Temporal Task Queues as the UI meaning of “queue”

### 13.2 Identifier policy

During migration, a Temporal-backed record may carry:

- `taskId`
- `workflowId`
- latest `temporalRunId`

Rules:

- for `source=temporal`, `taskId` should equal `workflowId`
- `taskId` remains the main dashboard route handle during migration when compatibility requires it
- `workflowId` is the durable Temporal identity
- `temporalRunId` is detail/debug metadata, not the main user-facing identifier
- `runId` remains reserved for legacy orchestrator compatibility and should not be reused for Temporal-backed task payloads

### 13.3 Mixed-source list caveat

A mixed-source `/tasks/list` page is a product convenience view, not a universal durable source of truth.

Rules:

- queue-backed rows remain sourced from queue APIs
- orchestrator-backed rows remain sourced from orchestrator APIs
- Temporal-backed rows remain sourced from Temporal lifecycle APIs
- no shared global pagination promise across all three systems

## 14. Rollout Plan

### Phase 1: Temporal read integration

- add `temporal` source to runtime config
- add Temporal row normalization
- add source-filtered Temporal list mode
- add Temporal detail rendering
- add canonical server-side source resolution for Temporal-backed `taskId`
- widen task detail route handling for Temporal-safe identifiers

### Phase 2: Temporal action integration

- enable cancel
- enable set title / update inputs
- enable pause / resume / approve where supported
- enable rerun

### Phase 3: Temporal artifact-first submit integration

- add artifact upload helper flows in submit pages where required
- enable backend-routed Temporal create for run-shaped submits from `/tasks/new`
- enable backend-routed Temporal create for manifest-oriented submits from `/tasks/manifests/new` or a unified replacement flow

### Phase 4: Compatibility refinement

- tighten multi-source route semantics
- decide whether a dedicated Temporal-first list view is needed
- retire temporary fallbacks that are no longer justified

## 15. Acceptance Criteria

This document is ready to implement when all of the following are true:

1. Runtime config additions for `temporal` are agreed.
2. Canonical source resolution and route-shell handling for non-UUID Temporal task identifiers are agreed.
3. Temporal list row normalization fields are fixed.
4. Temporal detail fetch sequence is fixed.
5. Action-to-endpoint mapping is fixed.
6. Artifact presentation rules are fixed enough for list/detail MVP.
7. Mixed-source versus Temporal-only pagination behavior is explicit.
8. The team agrees that Temporal is a dashboard **source**, not a runtime picker value.

## 16. Implementation Checklist

### Backend/UI boundary

- [ ] Add `sources.temporal` to `build_runtime_config()`
- [ ] Add `statusMaps.temporal`
- [ ] Add feature flags for Temporal list/detail/actions/submit
- [ ] Add canonical source resolution metadata/path for `/tasks/:taskId`
- [ ] Ensure dashboard shell route allowlist accepts Temporal-safe task identifiers

### List page

- [ ] Add Temporal fetch client for `GET /api/executions`
- [ ] Add Temporal row normalization
- [ ] Add `source=temporal` filter option
- [ ] Add `entry` and operator-only ownership filters only where API policy allows
- [ ] Add Temporal-only pagination token handling
- [ ] Document mixed-source total limitations in UI copy if needed

### Detail page

- [ ] Add Temporal detail resolver
- [ ] Add execution artifact list fetch
- [ ] Add source-aware metadata rendering for workflow ID / Temporal run ID
- [ ] Add wait metadata rendering (`waitingReason`, `attentionRequired`)
- [ ] Add Temporal artifact download flow
- [ ] Add v1 synthesized timeline panel

### Actions

- [ ] Add cancel action
- [ ] Add update action wrappers (`UpdateInputs`, `SetTitle`, `RequestRerun`)
- [ ] Add signal action wrappers (`Approve`, `Pause`, `Resume`)
- [ ] Add optimistic refresh / post-action reload behavior

### Submit

- [ ] Keep Temporal out of the runtime picker
- [ ] Add artifact-first submit helpers where needed
- [ ] Add backend-routed Temporal submit flow for run-shaped requests
- [ ] Add backend-routed Temporal submit flow for manifest-oriented requests
- [ ] Redirect new Temporal-backed executions to `/tasks/{taskId}?source=temporal`

## 17. Open Questions

1. Should `/tasks/:taskId` remain the only canonical Temporal detail route, or should `/tasks/executions/:workflowId` exist as a first-class compatibility alias?
2. Is the current `awaiting_action` compatibility grouping sufficient once `waitingReason` and `attentionRequired` are exposed, or do we eventually want a sharper dashboard distinction for approval versus external wait states?
3. Should direct Temporal-backed create be hidden entirely behind backend routing, or exposed as a feature-flagged advanced submit path during rollout?
4. Do we need explicit prior-run artifact browsing once Continue-As-New becomes common?
5. When a queue- or orchestrator-backed flow migrates to Temporal, should the dashboard preserve the previous source label for user continuity, or show `Temporal` directly?
