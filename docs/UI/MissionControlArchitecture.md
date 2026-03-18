# Mission Control Architecture

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-03-18  

## 1. Purpose

Define the concrete implementation architecture for the MoonMind Mission Control UI: component tree, routing schema, source model, runtime config, Temporal integration, action mapping, artifact flows, and phased rollout.

The dashboard integrates securely over the Control Plane API, interpreting Temporal execution statuses alongside legacy queue and orchestrator sources.

## 2. Related Docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/Temporal/TaskExecutionCompatibilityModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/TaskArchitecture.md`
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

### 3.2 Build Commands

#### `package.json` scripts (current)

```json
{
  "devDependencies": {
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0"
  },
  "scripts": {
    "dashboard:css": "tailwindcss -i api_service/static/task_dashboard/dashboard.tailwind.css -o api_service/static/task_dashboard/dashboard.css",
    "dashboard:css:min": "tailwindcss -i api_service/static/task_dashboard/dashboard.tailwind.css -o api_service/static/task_dashboard/dashboard.css --minify",
    "dashboard:css:watch": "tailwindcss -i api_service/static/task_dashboard/dashboard.tailwind.css -o api_service/static/task_dashboard/dashboard.css --watch"
  }
}
```

#### Developer workflow

1. `npm install`
2. During CSS work: `npm run dashboard:css:watch`
3. Before commit: `npm run dashboard:css:min`

#### CI consistency check

1. `npm ci`
2. `npm run dashboard:css:min`
3. `git diff --exit-code -- api_service/static/task_dashboard/dashboard.css`

## 4. Route Map

| Route | Purpose |
| --- | --- |
| `/tasks/list` | Unified task list viewing workflow executions (Temporal Visibility) |
| `/tasks/new` | Unified submit page / Workflow form wizard |
| `/tasks/queue/new` | Alias to `/tasks/new`; prefill mode uses `?editJobId=<jobId>` |
| `/tasks/:taskId` | Unified task detail shell resolving workflow history |
| `/tasks/proposals` | Proposal queue list and triage actions |
| `/tasks/proposals/:proposalId` | Proposal detail, promote/dismiss/priority/snooze actions |

### 4.1 Query Parameters

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

- Standard user task pages should be implicitly scoped to the authenticated principal.
- The default end-user UI should not expose an arbitrary owner picker.
- `ownerType` / `ownerId` filters are operator/admin controls.

## 5. Source Model

The dashboard presents work from multiple backend sources through a unified UI.

### 5.1 Dashboard Sources

Runtime config (`build_runtime_config()`) currently exposes:

- `queue`
- `orchestrator`
- `proposals`
- `manifests`
- `schedules`
- `temporal` *(being integrated)*

### 5.2 Temporal as a Dashboard Source

Temporal-backed work is integrated as a **first-class source** alongside queue and orchestrator, not as a separate UI or a worker runtime.

Rules:

- Add a new source key: `temporal`.
- Keep the `/tasks*` shell as the primary navigation surface.
- Do not make the browser talk directly to Temporal Server or Temporal Web UI.
- Go through MoonMind REST APIs only.

### 5.3 Temporal Is Not a Worker Runtime

Temporal is an orchestration substrate, not a replacement for `codex`, `gemini`, `claude`, `jules`, or `universal` runtime choices.

Rules:

- Do **not** add `temporal` as a worker runtime option.
- Do **not** overload the existing runtime picker to mean "execution engine."
- Prefer invisible backend routing: the user submits a task-shaped request, and the backend decides whether the execution is queue-backed, orchestrator-backed, or Temporal-backed.

### 5.4 Task-Oriented Product Surface

During migration, the dashboard continues to present work primarily as **tasks**.

- Use **task** in the main dashboard UX.
- Use **workflow execution** in advanced/debug metadata and implementation-facing text.
- Do not expose Temporal Task Queues as a user-facing queue product.

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

Client-side fallback mapping (mirrors canonical mapping from `docs/Temporal/VisibilityAndUiQueryModel.md`):

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

### 6.3 Feature Flags

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

## 7. Detail View Lifecycle

When viewing `/tasks/:taskId`, the dashboard polls the API (which maps to Temporal's Execution history or Postgres index).

- Queue-backed fetches query standard `GET /api/queue/jobs/{jobId}` which derives status from the execution index.
- Operator actions (Approve, Resume, Pause) interact with task limits by submitting to standard API routes, which issue Signals to the `MoonMind.Run` handler.
- Terminal outputs manifest in a UI Finish Summary block, mapping standard events into human-readable outcome strings (like `NO_CHANGES` or `PUBLISHED_PR`).

### 7.1 Temporal Detail Routing

The current dashboard route shell assumes queue/orchestrator-era identifier patterns in places.

Required change:

- Route `/tasks/:taskId` through canonical server-side source resolution metadata rather than ID-shape probing.
- Accept safe Temporal-compatible task IDs in the route shell so canonical routes remain reachable.
- Keep any source-specific alias route optional and clearly secondary.

Recommendation:

- Keep `/tasks/:taskId` as the canonical product route.
- Use a persisted source mapping / global task index to resolve `taskId` to `queue`, `orchestrator`, or `temporal`.
- Widen the route allowlist to accept safe Temporal-compatible identifiers as an implementation detail.
- Optionally add `/tasks/executions/:workflowId` as an internal/debug alias that redirects to `/tasks/:taskId?source=temporal`.

### 7.2 Source Resolution Order

1. If `?source=temporal`, resolve against Temporal only.
2. Otherwise resolve via canonical server-side source mapping for `taskId`.
3. Keep source-probing heuristics, if any remain temporarily, as an implementation fallback.
4. When a row originated from the Temporal list, generated links may include `?source=temporal` until source mapping is fully in place.

### 7.3 Temporal Detail Fetch Sequence

Minimum fetch sequence for Temporal-backed detail:

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
3. Optional per-artifact metadata/download calls when the user expands or downloads an artifact.

Important rule:

- Artifact list fetch must use the **latest `temporalRunId` from the execution detail response**, not a stale run ID cached from an earlier list row.
- The detail route stays anchored to `taskId == workflowId` even when `temporalRunId` changes across rerun or Continue-As-New behavior.

### 7.4 Detail Header Model

Temporal-backed detail should render:

- title
- normalized status badge
- summary
- workflow type label
- runtime, model, and effort (when applicable to the execution)
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

### 7.5 Timeline / Event Model

V1 detail behavior for Temporal-backed work:

- Show a summary/timeline panel synthesized from execution fields and known state transitions.
- Surface `waitingReason` and `attentionRequired` when the execution is blocked externally.
- Show artifacts as the main durable evidence surface.
- Show update/signal/cancel actions when enabled.
- Defer raw Temporal event history browsing until a dedicated backend contract exists.

## 8. List Page Integration

### 8.1 Mixed-Source List Behavior

`/tasks/list` remains the main list surface.

When `source` is not pinned, the client may merge rows from:

- queue
- orchestrator
- temporal

Rules:

- Mixed-source list mode is a **product convenience view**, not an authoritative globally paginated dataset.
- The dashboard may fetch bounded slices per source and merge-sort client-side.
- Mixed-source totals are informational only and should not claim exact global counts.
- Exact Temporal pagination and counts should only be surfaced when the user filters to `source=temporal`.

### 8.2 Temporal-Only List Behavior

When `source=temporal`, the dashboard treats Temporal as the authoritative source.

Behavior:

- Call `GET /api/executions`.
- Pass `workflowType`, `state`, `entry`, `ownerType`, `ownerId`, `pageSize`, and `nextPageToken` where applicable.
- Use the returned `nextPageToken`, `count`, and `countMode` as-is.
- Avoid pretending that queue/orchestrator records are part of the same exact paginated result set.

### 8.3 Row Model for Temporal-Backed Items

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

- Primary sort: `mm_updated_at` when available.
- Fallback sort: `updatedAt`.
- Deterministic tie-breaker: `workflowId DESC`.
- Fallback of last resort: `startedAt`.

For mixed-source views:

- Sort on a normalized timestamp field shared across sources.
- Do not imply Temporal queue-order semantics.

## 9. Action Mapping

### 9.1 Supported Temporal Actions

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

### 9.2 Initial UI Action Matrix

| Temporal state | Allowed actions |
| --- | --- |
| `initializing` / `planning` | cancel, set title |
| `executing` | cancel, pause, set title |
| `awaiting_external` | cancel, pause, resume, approve when applicable |
| `finalizing` | cancel only if policy allows |
| terminal (`succeeded`, `failed`, `canceled`) | rerun, view/download artifacts |

### 9.3 Copy Guidance

- Prefer **Rerun** instead of "Continue-As-New".
- Prefer **Task title** instead of "workflow memo title".
- Prefer **Pause task** / **Resume task** instead of Temporal-internal terminology.
- Advanced/debug views may disclose the underlying Temporal terms.

## 10. Submit Integration

### 10.1 Default Posture

Initial Temporal dashboard integration should be **read-first**.

Phased order:

1. list/detail visibility
2. task actions on existing Temporal-backed executions
3. direct submit from `/tasks/new`

### 10.2 Submit UX Rule

- Do not add a visible "Temporal runtime" option to the standard runtime picker.
- Keep submit flows organized around current task product shapes.
- Let the backend decide whether the request starts a Temporal execution.

### 10.3 Backend Mapping for Temporal-Backed Submit

The dashboard submits task-shaped requests and artifact references. The backend compatibility layer maps those onto Temporal workflow starts.

Initial expected mappings:

- Run-shaped submit flows may start `MoonMind.Run`.
- Manifest-oriented submit flows may start `MoonMind.ManifestIngest`.
- Submit payloads should use `task.tool` / `step.tool` as canonical execution shape; `task.skill` / `step.skill` may be accepted only as compatibility aliases.
- Skills are a tool subtype (`tool.type = "skill"`), not a sibling to tools.
- `workflowType`, `idempotencyKey`, `failurePolicy`, and `initialParameters` are backend/API contract details, not primary user-facing dashboard concepts.

### 10.4 Redirect After Create

- **Immediate execution:** redirect to `/tasks/{taskId}?source=temporal`.
- **Deferred one-time (`schedule.mode=once`):** redirect to `/tasks/{taskId}?source=temporal`, detail page shows scheduled banner.
- **Recurring (`schedule.mode=recurring`):** redirect to `/tasks/schedules/{definitionId}`, the schedule detail page.
- For Temporal-backed records, `taskId` should equal `workflowId`.
- Internal state may additionally retain `temporalRunId`, but the canonical route should remain task-oriented and stable across reruns.

### 10.5 Inline Scheduling on Submit

The submit form at `/tasks/new` includes a **"When to run"** schedule panel that allows the user to choose between immediate, deferred one-time, and recurring execution — all from the same form.

#### Feature Flag

```json
{
  "featureFlags": {
    "temporalDashboard": {
      "submitScheduleEnabled": false
    }
  }
}
```

When `submitScheduleEnabled` is `false`, the schedule panel is hidden and all submissions are immediate (current behavior).

#### Schedule Panel Wireframe

The panel appears below the task fields (instructions, runtime, model, repo) and above the submit button:

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

#### Mode Behaviors

| Selection | Submit button | API `schedule` field | Redirect |
| --- | --- | --- | --- |
| **Run immediately** | "Submit" | Absent | `/tasks/{taskId}?source=temporal` |
| **Schedule for later** | "Schedule" | `{ mode: "once", scheduledFor: "..." }` | `/tasks/{taskId}?source=temporal` |
| **Set up recurring schedule** | "Create Schedule" | `{ mode: "recurring", cron: "...", ... }` | `/tasks/schedules/{definitionId}` |

#### Deferred One-Time Fields

- **Date picker** — calendar date selector
- **Time picker** — hour/minute selector
- **Timezone** — dropdown, defaults to browser timezone via `Intl.DateTimeFormat().resolvedOptions().timeZone`
- Combined values produce an ISO 8601 `scheduledFor` timestamp

#### Recurring Schedule Fields

- **Schedule name** — text input, auto-populated from the task title, editable
- **Cron expression** — text input with inline validation against 5-field POSIX cron
- **Cron preview** — human-readable label derived from the cron expression (e.g., "Every weekday at 9:00 AM Pacific")
- **Timezone** — dropdown, defaults to `UTC`
- **Enabled** — toggle, defaults to on
- Advanced policy options (overlap, catchup, jitter) are omitted from the submit form to keep it simple. Users can configure these from the schedule detail page after creation.

#### UX Considerations

- The schedule panel defaults to "Run immediately" — the existing behavior.
- Validation prevents scheduling in the past for deferred one-time mode.
- For recurring mode, the cron preview should update live as the user types.
- Error states from the backend (e.g., invalid cron) should render inline under the field.
- The submit button label changes dynamically based on the selected mode.

#### Backend API Contract

The same `POST /api/executions` or `POST /api/queue/jobs` endpoint is used. The dashboard adds the optional `schedule` object to the existing create payload:

```json
{
  "type": "task",
  "payload": {
    "task": { "instructions": "...", "runtime": { ... } },
    "repository": "..."
  },
  "schedule": {
    "mode": "once",
    "scheduledFor": "2026-03-19T02:00:00Z"
  }
}
```

See [WorkflowSchedulingGuide.md § 4.4](file:///Users/nsticco/MoonMind/docs/Temporal/WorkflowSchedulingGuide.md) for the full `schedule` object schema and backend behavior.

#### Scheduled Execution Detail Banner

When a deferred one-time execution (`schedule.mode=once`) is viewed on the detail page before its start time:

- Show a **"Scheduled"** status badge (mapped from `mm_state=scheduled`).
- Show a banner: "This task is scheduled to run at {scheduledFor} ({timezone}). [Cancel]".
- The Cancel action cancels the Temporal workflow (which also cancels the deferred start).
- Once the start time passes, the execution transitions to `initializing` and the detail page renders normally.

## 11. Artifact Integration

### 11.1 General Posture

Temporal-managed flows should remain artifact-first for large inputs and outputs.

Dashboard implications:

- Large user inputs should upload as artifacts before create or update operations when needed.
- Task detail should show execution-linked artifacts as the main durable output surface.
- Downloads should go through MoonMind artifact authorization and grant flows.

### 11.2 Required Artifact Behaviors

The dashboard should support:

- Create artifact placeholder
- Upload content directly or through presigned part upload
- Complete upload
- Fetch artifact metadata
- Fetch execution-scoped artifact lists
- Download through presigned or direct endpoints

### 11.3 Presentation Rules

- Render artifact metadata and labels from execution linkage when available.
- Prefer preview flows when `preview_artifact_ref` exists.
- Respect `raw_access_allowed` and `default_read_ref`.
- Do not assume all artifacts are safe for inline display.
- Treat artifacts as immutable references; editing an input means producing a new artifact and updating references.

### 11.4 Run Scoping

Execution-scoped artifact listing is keyed by `namespace`, `workflowId`, and `temporalRunId`.

Default to showing artifacts for the **latest run**. If prior-run artifact browsing is needed later, it should become an explicit detail feature rather than an implicit mixed-run view.

## 12. Compatibility Rules

### 12.1 Vocabulary

- User-facing primary term: **task**
- Advanced/debug term: **workflow execution**
- Never present Temporal Task Queues as the UI meaning of "queue"

### 12.2 Identifier Policy

During migration, a Temporal-backed record may carry:

- `taskId`
- `workflowId`
- latest `temporalRunId`

Rules:

- For `source=temporal`, `taskId` should equal `workflowId`.
- `taskId` remains the main dashboard route handle during migration.
- `workflowId` is the durable Temporal identity.
- `temporalRunId` is detail/debug metadata, not the main user-facing identifier.
- `runId` remains reserved for legacy orchestrator compatibility and should not be reused for Temporal-backed task payloads.

### 12.3 Mixed-Source List Caveat

A mixed-source `/tasks/list` page is a product convenience view, not a universal durable source of truth.

- Queue-backed rows remain sourced from queue APIs.
- Orchestrator-backed rows remain sourced from orchestrator APIs.
- Temporal-backed rows remain sourced from Temporal lifecycle APIs.
- No shared global pagination promise across all three systems.

## 13. Rollout Plan

### Phase 1: Temporal Read Integration

- Add `temporal` source to runtime config.
- Add Temporal row normalization.
- Add source-filtered Temporal list mode.
- Add Temporal detail rendering.
- Add canonical server-side source resolution for Temporal-backed `taskId`.
- Widen task detail route handling for Temporal-safe identifiers.

### Phase 2: Temporal Action Integration

- Enable cancel.
- Enable set title / update inputs.
- Enable pause / resume / approve where supported.
- Enable rerun.

### Phase 3: Temporal Artifact-First Submit Integration

- Add artifact upload helper flows in submit pages where required.
- Enable backend-routed Temporal create for run-shaped submits from `/tasks/new`.
- Enable backend-routed Temporal create for manifest-oriented submits.

### Phase 3.5: Scheduling Integration

- Add `submitScheduleEnabled` feature flag.
- Implement schedule panel on `/tasks/new` submit form.
- Add deferred one-time support via Temporal `start_delay` on `POST /api/executions`.
- Add inline recurring schedule creation via delegation to `RecurringTasksService`.
- Add `scheduled` state rendering on detail page with countdown banner.
- Verify redirect flows for both deferred and recurring submit.

### Phase 4: Compatibility Refinement

- Tighten multi-source route semantics.
- Decide whether a dedicated Temporal-first list view is needed.
- Retire temporary fallbacks that are no longer justified.

## 14. Implementation Checklist

### Backend/UI Boundary

- [ ] Add `sources.temporal` to `build_runtime_config()`
- [ ] Add `statusMaps.temporal`
- [ ] Add feature flags for Temporal list/detail/actions/submit
- [ ] Add canonical source resolution metadata/path for `/tasks/:taskId`
- [ ] Ensure dashboard shell route allowlist accepts Temporal-safe task identifiers

### List Page

- [ ] Add Temporal fetch client for `GET /api/executions`
- [ ] Add Temporal row normalization
- [ ] Add `source=temporal` filter option
- [ ] Add `entry` and operator-only ownership filters only where API policy allows
- [ ] Add Temporal-only pagination token handling
- [ ] Document mixed-source total limitations in UI copy if needed

### Detail Page

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

### Scheduling

- [ ] Add `submitScheduleEnabled` feature flag to `build_runtime_config()`
- [ ] Add schedule panel UI component to submit form
- [ ] Implement "Run immediately" / "Schedule for later" / "Recurring" radio toggle
- [ ] Add date/time/timezone picker for deferred one-time mode
- [ ] Add cron expression input with live preview for recurring mode
- [ ] Add `schedule` field to `CreateExecutionRequest` and `CreateJobRequest` models
- [ ] Implement `schedule.mode=once` via Temporal `start_delay`
- [ ] Implement `schedule.mode=recurring` via `RecurringTasksService` delegation
- [ ] Add `scheduled` state to `TemporalExecutionRecord` and dashboard status maps
- [ ] Add scheduled banner rendering on detail page
- [ ] Redirect recurring creation to `/tasks/schedules/{definitionId}`

## 15. Open Questions

1. Should `/tasks/:taskId` remain the only canonical Temporal detail route, or should `/tasks/executions/:workflowId` exist as a first-class compatibility alias?
2. Is the current `awaiting_action` compatibility grouping sufficient once `waitingReason` and `attentionRequired` are exposed, or do we eventually want a sharper dashboard distinction for approval versus external wait states?
3. Should direct Temporal-backed create be hidden entirely behind backend routing, or exposed as a feature-flagged advanced submit path during rollout?
4. Do we need explicit prior-run artifact browsing once Continue-As-New becomes common?
5. When a queue- or orchestrator-backed flow migrates to Temporal, should the dashboard preserve the previous source label for user continuity, or show `Temporal` directly?
