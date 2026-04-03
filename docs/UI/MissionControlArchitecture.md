# Mission Control Architecture

Status: Active  
Owners: MoonMind Engineering  
Last updated: 2026-03-30

**Implementation tracking:** [`docs/tmp/remaining-work/UI-MissionControlArchitecture.md`](../tmp/remaining-work/UI-MissionControlArchitecture.md)

## 1. Purpose

Define the concrete architecture for the MoonMind Mission Control UI: route model, source model, runtime config, Temporal integration, action mapping, artifact flows, and task-oriented presentation rules.

Mission Control presents MoonMind primarily as a **Temporal-backed task console**. The product surface remains task-oriented, while the durable substrate is workflow-oriented.

This document covers:

- canonical routes and page responsibilities
- how task-oriented UI maps to Temporal-backed executions
- list/detail field and action posture
- runtime config and feature-flag expectations
- artifact interaction patterns
- skill-selection and execution-context presentation rules
- status and waiting-state presentation requirements

Detailed backend contracts live in the Temporal docs. This document defines the UI architecture that consumes them.

---

## 2. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/ManagedAgents/LiveLogs.md` — canonical design for artifact-first logs, MoonMind-owned observability APIs, SSE live follow, and the non-terminal log viewer UI
- `docs/Tasks/AgentSkillSystem.md`
- `docs/UI/MissionControlStyleGuide.md`
- `docs/UI/TypeScriptSystem.md`

---

## 3. Product stance

Mission Control is the operator and user console for MoonMind work.

The primary UX posture is:

- present work as **tasks**
- use Temporal-backed executions as the durable source of truth
- keep provider/runtime internals mostly out of the main scanning experience
- expose exact execution state without forcing users to think in raw Temporal or provider-native terms unless they choose advanced/debug detail surfaces

Important distinctions:

- **task** is the primary product term
- **workflow execution** is the implementation/debug term
- **runtime** is the agent or execution target choice
- **agent skills** are instruction bundles, not runtimes
- **Temporal** is the orchestration substrate, not a selectable runtime

---

## 4. Implementation snapshot

Mission Control is a **server-hosted React/Vite UI**:

- FastAPI serves the HTML shell and owns canonical routes
- a frontend-owned shared stylesheet is emitted through the Vite build
- page behavior is implemented through Vite-built React entrypoints
- runtime config is generated server-side
- REST APIs remain the only supported browser/backend boundary

Representative pieces:

- HTML shell: `api_service/templates/react_dashboard.html`
- navigation partials: `api_service/templates/_navigation.html`
- page entrypoints: `frontend/src/entrypoints/`
- shared stylesheet source: `frontend/src/styles/mission-control.css`
- generated JS/CSS bundles: `api_service/static/task_dashboard/dist/`
- React/Vite build output: `api_service/static/task_dashboard/dist/`
- runtime config builder: `api_service/api/routers/task_dashboard_view_model.py`
- route shell: `api_service/api/routers/task_dashboard.py`

---

## 5. Static asset and CSS invariants

## 5.1 Tailwind content strategy

Tailwind must scan all sources that can contain utility classes, including:

- `api_service/templates/react_dashboard.html`
- `api_service/templates/_navigation.html`
- `frontend/src/**/*.{js,jsx,ts,tsx}`

`frontend/src` must be included because production-aligned builds generate CSS before Vite output exists. Scanning only the built `dist` files is not sufficient for Docker correctness.

## 5.2 Build posture

Mission Control relies on two generated outputs:

- Vite `dist/` bundles (JS plus extracted CSS)
- `frontend/src/generated/openapi.ts`

The shared Mission Control stylesheet is emitted as part of the Vite build from `frontend/src/styles/mission-control.css`. Frontend-consumed API types remain a second checked-in generated artifact.

The canonical local generation path is:

1. `npm run generate`

`npm run generate` is responsible for:

- rebuilding `api_service/static/task_dashboard/dist/`
- regenerating `frontend/src/generated/openapi.ts`

The canonical CI drift gate is `npm run generate:check`, which reruns that generation path and fails on diffs in the checked-in generated files. OpenAPI generation now writes its intermediate schema to a temporary file instead of dirtying a tracked repo-root `openapi.json`.

Representative workflow:

1. install packages
2. run `npm run generate`
3. run Vite build
4. verify manifest/static output as needed

## 5.3 Common failure symptom

If a React route renders structurally correct HTML but spacing, grids, or layout styling are missing, the likely cause is stale or incomplete Vite-emitted CSS, usually because the Tailwind content configuration did not scan React source files.

---

## 6. Canonical route map

| Route | Purpose |
| --- | --- |
| `/tasks/list` | Primary task list backed by Temporal execution semantics |
| `/tasks/new` | Unified submit page for new tasks |
| `/tasks/queue/new` | Compatibility alias to `/tasks/new` |
| `/tasks/:taskId` | Primary task detail route |
| `/tasks/proposals` | Proposal queue list |
| `/tasks/proposals/:proposalId` | Proposal detail |
| `/tasks/schedules/:definitionId` | Recurring schedule detail when schedule creation is enabled |

Rules:

- `/tasks/list` and `/tasks/:taskId` are the primary product routes
- task detail should not require users to understand source-specific route families
- any execution/debug aliases should remain secondary and redirect or resolve back to task-oriented canonical routes where possible

---

## 7. Route query parameters

Supported query parameters for task list/detail routing include:

| Query parameter | Meaning |
| --- | --- |
| `source=temporal` | Debug/override source resolution hint |
| `workflowType=` | Filter by workflow type |
| `state=` | Filter by exact `mm_state` |
| `entry=` | Filter by `mm_entry` |
| `ownerType=` | Admin/operator owner-class filter |
| `ownerId=` | Admin/operator owner filter |
| `nextPageToken=` | Opaque pagination token |
| `limit=` | Page size / results-per-page state |
| `repo=` | Optional repo filter |
| `integration=` | Optional integration filter |

Rules:

- normal end-user task pages should be implicitly scoped to the authenticated principal
- normal end-user UI should not expose arbitrary owner pickers
- `source=temporal` is an implementation/debug affordance, not a first-class user concept
- `limit` is pagination/view state, not a primary filter
- `entry` should stay secondary unless product evidence shows it is independently meaningful to users

---

## 8. Source model

Mission Control navigation may still span multiple product areas, but the main task list/detail flow should behave as a **Temporal-native task surface**.

## 8.1 Runtime-config sources

Runtime config may still expose sources such as:

- `queue`
- `system`
- `proposals`
- `manifests`
- `schedules`
- `temporal`

That does not mean the user-facing task UI should give all of those equal primary visibility.

## 8.2 Task surface posture

`/tasks/list` and `/tasks/:taskId` should treat Temporal-backed executions as the default task experience.

Rules:

- keep `/tasks*` as the primary navigation surface
- do not make browser clients talk directly to Temporal Server or Temporal Web UI
- always go through MoonMind REST APIs
- keep `source` routing/query state as an implementation detail or temporary compatibility bridge
- avoid wasting primary UI space on a `Source` filter or a constant `Type = Temporal` column when Temporal is the main live task source

## 8.3 Temporal is not a runtime

Temporal is orchestration, not a worker runtime.

Rules:

- do **not** add `temporal` as a runtime option
- do **not** overload runtime selection to mean execution engine or skill selection
- do **not** present skill sets as workflow source types
- keep runtime choice and agent-skill choice as separate UX concepts

---

## 9. Task-oriented information hierarchy

Mission Control should use a strong hierarchy between list and detail.

## 9.1 List pages are for scanning

The list page should focus on the minimum fields needed to compare rows quickly.

High-value list fields:

- title
- normalized status
- workflow label
- runtime
- start time
- duration or updated time
- compact task ID

Secondary or overflow metadata should move to detail or row expansion.

## 9.2 Detail pages are for evidence and control

The detail page is the right place for:

- exact execution state
- workflow ID and run ID
- namespace
- repository
- integration
- owner
- waiting reason
- attention requirement
- execution-context metadata
- artifact evidence
- managed-run observability (artifact-backed logs, diagnostics — not terminal embeds)
- action surfaces

## 9.3 Advanced/debug information stays secondary

Advanced metadata may be shown in a dedicated facts rail, metadata drawer, or debug section, but it should not dominate the normal operator-facing layout.

Examples:

- exact `temporalStatus`
- exact `closeStatus`
- raw `rawState`
- provider/integration-specific metadata
- latest `temporalRunId`

---

## 10. Runtime config contract

## 10.1 `sources.temporal`

The runtime config should expose a `sources.temporal` block for Mission Control.

Representative shape:

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

* URLs should remain MoonMind API endpoints
* clients should not construct direct Temporal calls
* clients should not embed business logic about queue families or worker topology

## 10.2 Status mapping contract

The preferred UI contract for Temporal-backed rows/details is:

* `dashboardStatus` for compatibility grouping
* `rawState` for exact MoonMind state
* `temporalStatus` and `closeStatus` for advanced/detail views
* `waitingReason` and `attentionRequired` for blocked states

The client may contain a fallback compatibility state map, but server-supplied normalized top-level fields are preferred.

## 10.3 Feature flags

Mission Control should use feature flags to stage rollout.

Representative areas:

* Temporal list/detail
* Temporal actions
* Temporal submit flows
* debug fields
* agent skill selection
* agent skill detail presentation
* scheduling on submit

Rollout order should remain read-first:

1. list/detail visibility
2. actions
3. submit flows
4. optional debug surfaces

---

## 11. Detail route and source resolution

## 11.1 Canonical detail route

The canonical product detail route is:

* `/tasks/:taskId`

For Temporal-backed tasks:

* `taskId == workflowId`

This keeps product routing stable while preserving the correct durable identifier.

## 11.2 Source resolution order

Mission Control should resolve detail routes in this order:

1. canonical server-side source mapping / task index
2. explicit `?source=temporal` override when present
3. temporary fallback heuristics only as an implementation safety net

The long-term goal is server-side canonical source resolution, not route-level guesswork.

## 11.3 Detail fetch sequence

Minimum fetch sequence for a Temporal-backed detail page:

1. `GET /api/executions/{workflowId}`
2. `GET /api/executions/{namespace}/{workflowId}/{temporalRunId}/artifacts`
3. optional per-artifact metadata/download requests as the user expands or downloads artifacts

Rules:

* artifact fetch must use the latest `temporalRunId` returned by the detail response
* detail routing remains anchored to `taskId == workflowId`
* reruns or Continue-As-New should not force a route identity change

## 11.4 Observability fetch sequence

For the task detail observability area (Live Logs panel), the correct fetch sequence is:

1. `GET /api/task-runs/{id}/observability-summary` — fetch observability summary (run status, artifact refs, live stream availability)
2. `GET /api/task-runs/{id}/logs/merged` — fetch initial merged log tail; **initial content must be visible before any SSE connection is attempted**
3. If the run is active and `supports_live_streaming: true`, attach to `GET /api/task-runs/{id}/logs/stream`
4. If the stream connection fails or is unavailable, remain in artifact-backed mode — do not leave the panel blank

Rules:

* ended runs must skip step 3 entirely; never attempt a live stream connection on a completed run
* step 2 must always happen first and must always produce visible content when artifacts exist
* stream failure at step 3 transitions the viewer to `error` state backed by artifact content
* this sequence replaces any legacy approach of connecting SSE first and loading content through the stream

---

## 12. Detail page architecture

## 12.1 Detail header model

Temporal-backed detail should render:

* primary summary header:

  * title
  * normalized status badge
  * concise summary
  * allowed actions

* compact execution facts row:

  * workflow label
  * runtime
  * model/effort when applicable
  * started time
  * updated time or duration
  * current run state

* secondary facts rail or metadata panel:

  * workflow ID
  * latest `temporalRunId`
  * namespace
  * repository
  * integration
  * owner
  * terminal timestamps
  * optional execution-context metadata

* durable evidence section:

  * artifacts
  * outcome summaries
  * timeline/state transitions

## 12.2 Observability section

The task detail page must include a dedicated **Observability** area for managed-run evidence. This is the canonical UI hierarchy:

* **Live Logs** — merged log stream viewer (artifact-backed by default; upgrades to live follow when active)
* **Stdout** — artifact-backed stdout viewer with tail, download, copy, and line-wrap
* **Stderr** — artifact-backed stderr viewer with tail, download, copy, and line-wrap
* **Diagnostics** — structured run metadata (exit code, failure class, duration, artifact refs, parsed errors)
* **Artifacts** — full execution artifact listing (consistent with the rest of Mission Control)

Rules:

* managed-run observability is artifact-backed and MoonMind-native — it does not use terminal embeds
* `xterm.js` must not appear in this area; it is reserved for OAuth sessions only
* the observability area follows the fetch sequence defined in §11.4

## 12.3 Log viewer state model

The Live Logs panel has five defined states:

| State | Meaning |
| --- | --- |
| `not_available` | No artifacts and no live stream; run may be pre-launch or starting |
| `starting` | Observability summary or initial tail is loading |
| `live` | Connected to active live stream; receiving events from supervised runtime |
| `ended` | Run is terminal; showing final artifact-backed tail only |
| `error` | Live stream connection failed; viewer shows artifact-backed content |

Allowed state transitions:

* `not_available` → `starting`
* `starting` → `live` (run is active, stream available)
* `starting` → `ended` (run already terminal when panel is opened)
* `starting` → `error` (initial fetch failed)
* `live` → `ended` (run completes)
* `live` → `error` (stream connection fails)
* `error` → `live` (reconnect succeeds and run is still active)
* `error` → `ended` (run is terminal; reconnect not appropriate)

## 12.4 Panel lifecycle rules

Connection lifecycle is governed by panel open/close and tab visibility:

* **collapsed**: no active connection; no background streaming
* **open + active run**: fetch summary → fetch tail → connect stream
* **open + ended run**: fetch summary → fetch tail → no stream connection
* **collapse**: disconnect immediately
* **background tab**: disconnect or pause; reconnect on foreground only if panel is open and run is still active
* **stream error**: transition to `error` state; render artifact-backed content; do not leave panel blank
* **ended run at any point**: never initiate a stream connection

## 12.5 Degraded mode and fallback behavior

* artifact-backed tail is the default baseline; it must always work
* live follow is an optional enhancement layered on top
* stream errors must not erase visible logs; the panel always shows the last artifact-backed state on error
* ended runs are fully usable through artifact-backed views — operators do not need a live connection to inspect completed work
* if `supports_live_streaming: false`, the panel shows artifact-backed content with no stream connection attempt

## 12.6 Stream provenance requirements

Log records must carry per-line stream provenance:

* `stdout` — captured from the managed runtime's standard output
* `stderr` — captured from the managed runtime's standard error
* `system` — MoonMind supervisor annotations, reconnect notices, truncation warnings

Stream provenance is required for correct line rendering and for the merged view. The API must include `stream` on every record.

## 12.7 Feature flags for observability rollout

* `logStreamingEnabled` — operator-visible disable switch for the observability panel and live-follow behavior; default `true` via `MOONMIND_LOG_STREAMING_ENABLED`
* a separate UI flag for the new observability panel layout may be used if a side-by-side rollout with the legacy task-detail view is required

Rollout posture: default-on with an explicit disable path. The read-first fetch sequence still applies: artifact-backed tail before live follow.

## 12.8 Success criteria for the UI layer

* Opening the Live Logs panel shows recent artifact-backed tail content within 2 seconds
* Active runs can follow live output from the supervised runtime through the panel
* Collapsing the panel or backgrounding the tab stops live stream connections within a few seconds
* Completed runs remain inspectable through artifact-backed views without any live connection
* Stream errors degrade gracefully to artifact-backed content; the panel is never left blank

## 12.9 Timeline/event model

Temporal-backed detail should show a synthesized execution timeline based on:

* exact execution state
* waiting metadata
* notable action results
* artifact evidence
* proposal/finalization transitions
* task-level outcome summaries

Raw Temporal event history browsing is out of scope unless a dedicated backend contract exists.

## 12.10 Waiting-state presentation

When an execution is blocked:

* show the exact `rawState`
* show `waitingReason`
* show whether `attentionRequired` is true
* avoid implying user action is required when the block is merely external/provider/system waiting

---

## 13. List page architecture

## 13.1 Product posture

`/tasks/list` is the primary Mission Control task console.

Rules:

* treat Temporal executions as the authoritative dataset for the main task list UX
* do not expose `Source` as a first-order normal task filter
* keep `source=temporal` as a debug/implementation detail
* avoid queue-era framing like a constant `Type = Temporal` column

## 13.2 Filter model

Primary list controls should stay narrow and high-signal:

* **Workflow** (mapped internally to `workflowType`)
* **Status** (mapped to normalized/exact execution state filters)
* **Runtime**
* Search, if/when supported by the actual backend contract

Advanced filters may include:

* `repo`
* `integration`
* `ownerType`
* `ownerId`

Rules:

* do not expose both `Workflow Type` and `Entry` when they are not meaningfully distinct to users
* keep `entry` secondary unless a future workflow class makes it independently useful
* place page size near pagination/view controls, not in the primary filter bar
* do not add full skill-set or provider-debug columns to the default list table

## 13.3 Layout and density

The list page should use a two-width strategy:

* masthead/navigation/filter surfaces remain visually constrained and polished
* the results region expands into a wide console surface on desktop
* desktop remains table-first
* narrow/mobile layouts may collapse into cards
* density should come from hierarchy and clarity, not nested chrome

## 13.4 Row model for Temporal-backed items

| Dashboard field     | Temporal source                                |
| ------------------- | ---------------------------------------------- |
| `id` / `taskId`     | `workflowId`                                   |
| `title`             | `memo.title` or fallback                       |
| `summary`           | `memo.summary`                                 |
| `workflowType`      | `workflowType`                                 |
| `runtime`           | runtime target from execution fields           |
| `entry`             | `searchAttributes.mm_entry`                    |
| `status`            | `dashboardStatus`                              |
| `rawState`          | exact `state`                                  |
| `temporalStatus`    | `temporalStatus`                               |
| `closeStatus`       | `closeStatus`                                  |
| `ownerType`         | `searchAttributes.mm_owner_type`               |
| `ownerId`           | `searchAttributes.mm_owner_id`                 |
| `repository`        | `searchAttributes.mm_repo`                     |
| `integration`       | `searchAttributes.mm_integration`              |
| `waitingReason`     | bounded waiting reason                         |
| `attentionRequired` | whether current user/operator action is needed |
| `startedAt`         | `startedAt`                                    |
| `updatedAt`         | `updatedAt`                                    |
| `closedAt`          | `closedAt`                                     |
| `duration`          | derived                                        |
| `workflowId`        | `workflowId`                                   |
| `temporalRunId`     | latest run instance ID                         |

Recommended desktop priorities:

* primary column: `title`
* strong supporting columns: `status`, `workflowType`, `runtime`, `startedAt`, `duration` or `updatedAt`
* compact secondary column: `id` / `taskId`

Move these to detail or expansion surfaces:

* namespace
* run ID
* entry
* repository
* integration
* owner
* exact finished time
* deeper execution-context metadata

## 13.5 Sorting and pagination

For Temporal-backed rows:

* primary sort: `mm_updated_at`
* fallback sort: `updatedAt`
* deterministic tie-breaker: `workflowId DESC`

Pagination rules:

* use `GET /api/executions`
* pass canonical supported filters only
* treat `nextPageToken` as opaque
* use returned `count` and `countMode` as-is
* present page size near pagination or view controls, not as a primary filter

---

## 14. Action mapping

## 14.1 Supported Temporal actions

| Dashboard action | Temporal API                               | Contract                   |
| ---------------- | ------------------------------------------ | -------------------------- |
| Create execution | `POST /api/executions`                     | Start workflow             |
| Edit inputs      | `POST /api/executions/{workflowId}/update` | `UpdateInputs`             |
| Rename / retitle | `POST /api/executions/{workflowId}/update` | `SetTitle`                 |
| Rerun            | `POST /api/executions/{workflowId}/update` | `RequestRerun`             |
| Approve          | `POST /api/executions/{workflowId}/signal` | `Approve`                  |
| Pause            | `POST /api/executions/{workflowId}/signal` | `Pause`                    |
| Resume           | `POST /api/executions/{workflowId}/signal` | `Resume`                   |
| Cancel           | `POST /api/executions/{workflowId}/cancel` | Graceful cancel by default |

`ExternalEvent` is part of the execution contract, but normally appears as a system/integration path rather than a direct user-facing button.

## 14.2 Initial UI action matrix

| Temporal state                                                                          | Allowed actions                                |
| --------------------------------------------------------------------------------------- | ---------------------------------------------- |
| `scheduled` / `initializing` / `waiting_on_dependencies` / `awaiting_slot` / `planning` | cancel, set title                              |
| `executing` / `proposals`                                                               | cancel, pause, set title                       |
| `awaiting_external`                                                                     | cancel, pause, resume, approve when applicable |
| `finalizing`                                                                            | cancel only if policy allows                   |
| terminal (`completed`, `failed`, `canceled`)                                            | rerun, view/download artifacts                 |

## 14.3 Copy guidance

Prefer product language:

* **Rerun** instead of Continue-As-New
* **Task title** instead of workflow memo title
* **Pause task** / **Resume task** instead of raw Temporal jargon

Advanced/debug views may disclose the underlying implementation terms.

---

## 15. Submit integration

## 15.1 Default posture

Temporal dashboard integration should remain **read-first**.

Rollout order:

1. list/detail visibility
2. task actions on existing Temporal-backed executions
3. direct submit from `/tasks/new`

## 15.2 Submit UX rules

* runtime selection and skill selection are distinct concerns
* do not overload the runtime picker to represent skill sets
* do not expose raw source-precedence logic directly to ordinary users
* keep submit flows organized around task-shaped product language
* let the backend decide the workflow type and execution routing

## 15.3 Backend mapping for submit

The dashboard submits task-shaped requests plus task-level intent such as:

* instructions
* runtime choice
* repository/workspace context
* artifacts
* optional scheduling intent
* skill-selection intent

The backend resolves those into workflow start inputs and immutable runtime context.

Representative mappings:

* run-shaped submit flows → `MoonMind.Run`
* manifest-oriented submit flows → `MoonMind.ManifestIngest`

The UI should submit **selection intent**, not full mutable skill bodies inline.

## 15.4 Redirect after create

* immediate execution → `/tasks/{taskId}?source=temporal`
* deferred one-time execution → `/tasks/{taskId}?source=temporal`
* recurring schedule creation → `/tasks/schedules/{definitionId}`

For Temporal-backed records:

* `taskId == workflowId`

The route should remain stable across reruns and new Temporal runs.

## 15.5 Inline scheduling on submit

`/tasks/new` may include an inline schedule panel allowing:

* run immediately
* schedule for later
* recurring schedule

This should remain feature-flagged and staged behind a read-first rollout.

The schedule UI should stay simple:

* no deep policy matrix on initial submit
* advanced scheduling policy can move to schedule detail pages later

---

## 16. Agent skill UX posture

## 16.1 Core rule

Agent skills are instruction bundles, not runtimes, workflow sources, or queue types.

## 16.2 Submit UX

Preferred submit-time posture:

* named skill sets over overwhelming granular controls
* concise summary of inherited/default skill context
* optional advanced drawer for include/exclude behavior and policy-constrained advanced controls
* no giant query-string serialization of skill selections by default

## 16.3 Detail UX

Detail views may show compact execution-context information such as:

* resolved skill snapshot summary
* execution-context constraints
* limited provenance/debug metadata when policy allows

The default list table should not carry heavy skill-set columns.

---

## 17. Artifact integration

## 17.1 General posture

Temporal-managed flows remain artifact-first for large inputs and outputs.

Dashboard implications:

* large user inputs upload as artifacts before create/update when needed
* task detail shows execution-linked artifacts as the durable evidence surface
* downloads go through MoonMind artifact authorization/grant flows

## 17.2 Required artifact behaviors

Mission Control should support:

* create artifact placeholder
* upload content directly or through presigned multipart flows
* complete upload
* fetch artifact metadata
* fetch execution-scoped artifact lists
* download through presigned or direct endpoints

## 17.3 Presentation rules

* render artifact labels and metadata from execution linkage when available
* prefer preview flows when preview refs exist
* respect preview/raw access policy signals
* do not assume all artifacts are safe for inline display
* treat artifacts as immutable references

## 17.4 Run scoping

Execution-scoped artifact listing is keyed by:

* `namespace`
* `workflowId`
* `temporalRunId`

Default to showing artifacts for the **latest run**. Prior-run browsing, if added later, should be an explicit detail feature.

---

## 18. Compatibility rules

## 18.1 Vocabulary

* primary user-facing term: **task**
* advanced/debug term: **workflow execution**
* use **agent skill** or **skill set** for instruction bundles
* never present Temporal Task Queues as the UI meaning of “queue”

## 18.2 Identifier policy

For Temporal-backed task surfaces:

* `taskId`
* `workflowId`
* latest `temporalRunId`

Rules:

* `taskId == workflowId`
* `taskId` remains the main dashboard route handle
* `workflowId` is the durable Temporal identity
* `temporalRunId` is detail/debug metadata, not the main identifier
* do not reuse old queue/system `runId` concepts for Temporal-backed task payloads

## 18.3 Source visibility policy

* do not require normal operators to reason about `source` for the main task list/detail flow
* keep source-specific routing/query compatibility behind the scenes unless another live task source becomes product-relevant again

---

## 19. Open questions

1. Should `/tasks/:taskId` remain the only canonical Temporal detail route, or should `/tasks/executions/:workflowId` exist as a first-class secondary alias?
2. Is `awaiting_action` still sufficient as a compatibility grouping once `waitingReason` and `attentionRequired` are fully exposed?
3. Should direct Temporal-backed create remain fully hidden behind backend routing, or ever be exposed as an advanced feature-flagged path?
4. Do we need explicit prior-run artifact browsing once Continue-As-New becomes common in real usage?
5. Should submit show deployment-default skill sets as read-only chips unless the user expands advanced controls?
6. Is there any proven list-page benefit to a compact skill-set badge, or should that remain detail-only?
