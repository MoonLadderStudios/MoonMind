# Visibility and UI Query Model

**Status:** Draft  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-06  
**Audience:** backend, dashboard, API, workflow authors

---

## 1. Purpose

Define the canonical **Visibility-backed query model** for **Temporal-managed executions** in MoonMind.

This document turns the higher-level decisions in the Temporal foundation and lifecycle docs into an implementation-facing contract for:

- required **Search Attributes**,
- allowed **list/detail filters**,
- **Memo** fields used by UI projections,
- default **sorting** and **recency** behavior,
- **pagination token** and **count** semantics,
- compatibility mapping from Temporal execution data into the current **task-oriented UI**.

This document is intentionally the bridge between:

- the target rule that **Temporal Visibility** is the source of truth for Temporal-managed list/query/count, and
- the current implementation reality that MoonMind still has task-oriented `/tasks/*` product surfaces and an adapter-style `/api/executions` lifecycle API.

---

## 2. Related docs

- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/TaskUiArchitecture.md`

This document narrows several open items from the lifecycle doc so frontend and backend work can proceed without inventing ad hoc query behavior.

---

## 3. Scope and non-goals

### 3.1 In scope

- Search Attribute registry for Temporal-managed executions.
- Memo registry for list/detail presentation.
- Canonical list/detail field model for Temporal-backed rows.
- Allowed filters, sort order, recency rules, pagination, and counts.
- Compatibility mapping into current Mission Control status/grouping semantics.
- Migration rules for adapter APIs and unified task surfaces.

### 3.2 Out of scope

- Event history rendering or Temporal Web UI parity.
- Artifact storage, upload, or ACL design.
- Worker topology and task queue routing.
- Full dashboard route redesign.
- Direct Temporal server query syntax details.

---

## 4. Current implementation reality and target contract

### 4.1 Current implementation reality

MoonMind already has a Temporal execution adapter surface:

- `POST /api/executions`
- `GET /api/executions`
- `GET /api/executions/{workflowId}`
- `POST /api/executions/{workflowId}/update`
- `POST /api/executions/{workflowId}/signal`
- `POST /api/executions/{workflowId}/cancel`

Today, that API is backed by `TemporalExecutionService` and a `TemporalExecutionRecord` projection row in the app database.

Current implemented behavior:

- filter inputs: `workflowType`, `state`, `ownerId`
- page inputs: `pageSize`, `nextPageToken`
- count behavior: exact count, returned as `countMode="exact"`
- current ordering: `updated_at DESC`, then `workflow_id DESC`
- current token implementation: opaque base64-encoded JSON carrying an offset

The current dashboard runtime config and route shell still only model explicit sources such as `queue`, `orchestrator`, `proposals`, `manifests`, and `schedules`; there is not yet a first-class `temporal` dashboard source.

### 4.2 Target contract

For **Temporal-managed work**, **Temporal Visibility** is the source of truth for:

- list,
- query/filter,
- pagination,
- count.

The app DB projection may continue to exist during migration, but it is an **adapter/cache/reconciliation layer**, not the semantic owner of query behavior.

Contractually:

1. Any adapter API for Temporal-backed list/detail data must preserve **Visibility semantics**.
2. Unified `/tasks/*` surfaces may reshape Temporal rows into task-oriented payloads, but they may **not invent conflicting state, sort, or pagination rules**.
3. If a projection disagrees with Temporal-backed canonical fields, **Temporal wins** for query semantics.

---

## 5. Canonical query entity model

### 5.1 Canonical entity

The primary durable query entity for Temporal-managed work is a **Workflow Execution**.

MoonMind may still use the word **task** in compatibility APIs and UI surfaces, but those rows map to Workflow Executions when the runtime is Temporal-backed.

### 5.2 Canonical identifiers

- `workflowId`: the durable canonical identifier for Temporal-managed work.
- `runId`: the current/latest Temporal run instance identifier; detail/debug oriented.
- `taskId`: optional compatibility alias for task-oriented APIs during migration.

Rules:

- `workflowId` is the stable identity used for links, cache keys, and adapter lookups.
- `runId` must not replace `workflowId` as the primary product handle.
- UI detail pages may show `runId`, but should label it as run/debug metadata.

### 5.2.1 Compatibility identifier bridge

MoonMind's current dashboard contract is still task-oriented (`/tasks/list`, `/tasks/:taskId`), so this document makes the identifier bridge explicit.

Rules:

- for any Temporal-backed row exposed through a task-oriented compatibility surface, `taskId` **must equal** `workflowId`;
- task-oriented APIs may return both `taskId` and `workflowId` during migration, but they must carry the same durable value for Temporal-backed work;
- compatibility adapters must not mint a second opaque identifier for the same Temporal execution;
- if MoonMind temporarily accepts a legacy alias, the server must resolve it and redirect/canonicalize to the `workflowId`-based route;
- `runId` must never be used as the route identifier for task-oriented detail pages.

This keeps the current task UI stable without introducing an alias table that would become another migration dependency.

### 5.3 Canonical list row fields

A Temporal-backed list row should be expressible with the following stable fields:

- `workflowId`
- `taskId?` (required only on task-oriented compatibility surfaces; equal to `workflowId`)
- `workflowType`
- `entry` (`run` or `manifest`)
- `state` (`mm_state` exact value)
- `temporalStatus` (`running | completed | failed | canceled`)
- `title`
- `summary`
- `ownerType`
- `ownerId`
- `updatedAt`
- `startedAt`
- `closedAt?`
- `waitingReason?`
- `attentionRequired?`
- `dashboardStatus` (compatibility grouping only)

Adapter APIs may still return raw `searchAttributes` and `memo` blobs, but UI consumers should prefer stable top-level fields when available.

### 5.4 Canonical detail fields

A Temporal-backed detail model may additionally include:

- `runId`
- `closeStatus`
- `artifactRefs[]`
- `waitingReason?`
- `attentionRequired?`
- raw `searchAttributes`
- raw `memo`
- optional debug metadata or reconciliation metadata

Detail views should show the exact Temporal-facing state (`state`, `temporalStatus`, `closeStatus`) even if list pages group them into broader task-style statuses.

---

## 6. Search Attribute registry

### 6.1 Naming rules

All MoonMind-owned Search Attributes for this query model follow these rules:

- prefix: `mm_`
- lowercase snake_case
- bounded values only in v1
- no secrets
- no large free text
- no user-generated text intended only for display

### 6.2 Required Search Attributes

| Name | Type | Required | Lifecycle owner | Update rule | Notes |
| --- | --- | --- | --- | --- | --- |
| `mm_owner_type` | keyword | Yes | API/workflow start path | Set at start; treat as immutable for v1 | `user`, `system`, or `service`. Distinguishes end-user-owned runs from operator/platform-owned runs. |
| `mm_owner_id` | keyword | Yes | API/workflow start path | Set at start; treat as immutable for v1 | Principal identifier. For `user`, use the user UUID/string. For `system`, use the reserved value `system`. For `service`, use a stable bounded service name. |
| `mm_state` | keyword | Yes | Workflow lifecycle logic | Update on every domain state transition | Exact MoonMind lifecycle state. |
| `mm_updated_at` | datetime | Yes | Workflow lifecycle logic | Update on every meaningful user-visible mutation | Default recency and sort key. |
| `mm_entry` | keyword | Yes | Workflow start path | Set at start; immutable | `run` or `manifest`. |

### 6.3 Optional Search Attributes

| Name | Type | Required | When to set | Notes |
| --- | --- | --- | --- | --- |
| `mm_repo` | keyword | No | When execution is repo-scoped and product needs filtering | Must be bounded/stable, e.g. `owner/repo`. |
| `mm_integration` | keyword | No | When execution is primarily tied to an external integration | Examples: `github`, `jules`. Keep bounded. |

### 6.4 Deferred Search Attributes

These are intentionally **not** required in v1:

- `mm_stage`
- `mm_error_category`
- free-text or text-search attributes
- unbounded tag arrays
- child-workflow/activity-level search attributes

If any deferred field becomes necessary for filtering, it should be introduced by updating this document first rather than being added ad hoc in code.

### 6.5 Required value sets

#### `mm_state`

Allowed values for v1:

- `initializing`
- `planning`
- `executing`
- `awaiting_external`
- `finalizing`
- `succeeded`
- `failed`
- `canceled`

Rules:

- `mm_state` must be set immediately on workflow start.
- `mm_state` must transition to a terminal value on close.
- terminal mapping must remain consistent with close status:
  - completed -> `succeeded`
  - failed / terminated / timed out -> `failed`
  - canceled -> `canceled`

#### `mm_owner_type`

Allowed values for v1:

- `user`
- `system`
- `service`

Rules:

- `mm_owner_type` and `mm_owner_id` must always be populated together.
- `unknown` is not an allowed target-state owner identifier.
- standard end-user list/detail surfaces must only expose executions where `mm_owner_type = user` and `mm_owner_id` matches the authenticated principal, unless an explicit operator/admin path says otherwise.
- operator/admin surfaces may list `system` and `service` executions, but must preserve exact owner metadata rather than collapsing those rows into user-owned views.

### 6.6 Stronger v1 decision on `mm_entry`

Earlier drafts described `mm_entry` as optional. MoonMind now treats it as **required for v1** because:

- the current implementation already sets it,
- the UI needs a stable way to distinguish `run` versus `manifest` without parsing type names,
- it avoids coupling list filters to raw workflow type strings.

---

## 7. Memo registry

### 7.1 Required Memo fields

| Field | Required | Purpose | Rules |
| --- | --- | --- | --- |
| `title` | Yes | Human-readable execution title | Small, display-safe, mutable via update. |
| `summary` | Yes | Small current summary for list/detail surfaces | Human-readable, mutable over time. |

### 7.2 Optional Memo fields

| Field | Required | Purpose | Rules |
| --- | --- | --- | --- |
| `input_ref` | No | Safe reference to input artifact | Reference only; no large embedded content. |
| `manifest_ref` | No | Safe reference for manifest-driven workflows | Reference only. |
| `error_category` | No | Debug/detail classification for failures | Display/debug only until promoted to Search Attribute. |

### 7.3 Memo rules

- Keep Memo small and human-readable.
- Never store secrets, full prompts, manifests, or large payloads in Memo.
- Memo is for **display metadata**, not for filtering.
- List views should rely on `title` and `summary`, not raw artifact payloads.

---

## 8. Allowed filters and UI query model

### 8.1 Exact filters for Temporal-backed queries

The allowed exact-match filter set for Temporal-managed list queries is:

- `workflowType`
- `ownerType`
- `ownerId`
- `state`
- `entry`
- `repo` (optional when `mm_repo` exists)
- `integration` (optional when `mm_integration` exists)

### 8.2 v1 filter priorities

#### Required now

- `workflowType`
- `ownerId`
- `state`

These already exist in the current `/api/executions` adapter surface.

#### Required next

- `entry`
- `ownerType`

This should be added before the dashboard starts depending on Temporal-backed list filtering in earnest.

#### Optional later

- `repo`
- `integration`

These should ship only when a real UI filter or API consumer needs them.

### 8.3 Not in v1

The following are intentionally out of scope for v1 query behavior:

- free-text search
- fuzzy title search
- arbitrary multi-field OR composition
- arbitrary date-range filtering
- child-workflow filtering
- activity-level filtering

### 8.4 Ownership scoping rules

Ownership is not just a filter; it is part of authorization.

Rules:

- standard users are implicitly scoped to their own executions,
- standard users must not see `system` or `service` executions on user-facing task pages unless a product surface is explicitly designed for that purpose,
- non-admin callers must not query another user’s executions,
- admin/operator callers may filter by `ownerType`, `ownerId`, or omit them.

The UI should not offer an arbitrary owner picker to normal end users.

---

## 9. Sorting, recency, and stable ordering

### 9.1 Default ordering

The canonical default order for Temporal-managed list rows is:

1. `mm_updated_at DESC`
2. `workflowId DESC` as a deterministic tie-breaker

This matches current adapter behavior and should remain the default even after the backing implementation moves from the app DB projection to Temporal Visibility.

### 9.2 No queue semantics

Temporal-backed lists must not imply:

- FIFO queue ordering,
- worker queue position,
- queue depth semantics,
- “next to run” promises.

Task queues are plumbing, not a user-visible ordering model.

### 9.3 What moves `mm_updated_at`

This document resolves the lifecycle open question for v1.

`mm_updated_at` should move on **meaningful user-visible mutations**, including:

- domain state transitions,
- accepted edits/updates,
- signal handling that changes visible workflow state,
- pause/resume/cancel/rerun actions,
- terminal success/failure transitions,
- persisted progress checkpoints that should affect recency ordering,
- title/summary changes that materially change what the user sees in list/detail views.

`mm_updated_at` should **not** be driven by every low-level heartbeat, log line, or internal activity retry.

Implementation guidance:

- if progress is recorded at high frequency, it must be checkpointed/bounded before it updates `mm_updated_at`.
- `record_progress` style calls should represent meaningful UI-facing progress, not telemetry spam.

---

## 10. Compatibility status mapping for the current Mission Control

The current dashboard uses broad normalized task statuses such as:

- `queued`
- `running`
- `awaiting_action`
- `succeeded`
- `failed`
- `cancelled`

Temporal-backed rows should preserve exact Temporal/MoonMind state **and** provide a compatibility grouping for current task-oriented pages.

### 10.1 Exact versus compatibility status

- **Exact fields:** `state`, `temporalStatus`, `closeStatus`
- **Compatibility field:** `dashboardStatus`

### 10.2 v1 mapping

| Exact Temporal-backed state | Compatibility dashboard status | Notes |
| --- | --- | --- |
| `initializing` | `queued` | Not yet materially executing user work. |
| `planning` | `queued` | Still pre-execution from a Mission Control perspective. |
| `executing` | `running` | Active execution. |
| `awaiting_external` | `awaiting_action` | Compatibility grouping only; detail must still show exact `awaiting_external`. |
| `finalizing` | `running` | Still in-flight, not terminal. |
| `succeeded` | `succeeded` | Terminal success. |
| `failed` | `failed` | Terminal failure. |
| `canceled` | `cancelled` | Compatibility spelling bridge for current dashboard vocabulary. |

### 10.3 Important note on `awaiting_external`

`awaiting_external` does **not** always mean the current user must take action.

For current Mission Control compatibility surfaces, it maps to `awaiting_action` because that is the nearest existing grouped status. Exact detail views must still show `awaiting_external` so the product can later distinguish:

- approval required,
- webhook wait,
- external completion wait,
- paused by operator.

### 10.4 Required wait metadata

To avoid misleading users, Temporal-backed rows in `awaiting_external` should also expose:

- `waitingReason`: bounded value describing why progress is blocked
- `attentionRequired`: boolean indicating whether the current operator/user is expected to act

Allowed `waitingReason` values for v1:

- `approval_required`
- `external_callback`
- `external_completion`
- `operator_paused`
- `retry_backoff`
- `unknown_external`

Rules:

- `waitingReason` should be set whenever `state = awaiting_external`;
- `attentionRequired = true` only when progress is blocked on a human/operator action exposed by the current product surface;
- compatibility dashboards may keep the broad `awaiting_action` bucket temporarily, but they must not imply user action is required when `attentionRequired = false`.

---

## 11. Pagination and count strategy

### 11.1 Pagination inputs

Temporal-backed list APIs use:

- `pageSize`
- `nextPageToken`

Rules:

- `nextPageToken` is **opaque** to clients.
- clients must only echo the token back to the same endpoint with the same filter/sort scope.
- changing filters invalidates the existing token.

### 11.2 Current adapter behavior

The current `/api/executions` implementation uses a base64-encoded JSON payload carrying an offset.

This is an implementation detail, not a frontend parsing contract.

### 11.3 Target behavior

When list/query/count moves to Temporal Visibility proper, the API should pass through Temporal-backed pagination semantics without forcing the UI to know Temporal internals.

### 11.4 Count behavior

List responses may include:

- `count`
- `countMode`

Allowed `countMode` values:

- `exact`
- `estimated_or_unknown`

Current adapter rule:

- `/api/executions` returns exact count today.

Target rule:

- pure Temporal-backed list endpoints should return exact count when it is available and operationally acceptable,
- otherwise they may return `estimated_or_unknown` or omit `count`.

### 11.5 Unified multi-source pages

A unified `/tasks/list` page must **not** pretend that queue rows, orchestrator rows, and Temporal-backed rows share a single native cursor/count model.

During migration, a unified page should do one of the following:

1. keep **separate source cursors/tokens** internally, or
2. use a backend aggregator that owns the merged pagination contract.

It must not reuse Temporal page tokens as if they were universal task-list tokens.

### 11.6 Freshness and degraded-read behavior

During migration, MoonMind should assume list surfaces can be temporarily less fresh than the detail/action response that just mutated one execution.

Rules:

- the response from a successful update, signal, cancel, or rerun action is the authoritative immediate view for that execution;
- list pages should patch the acted-on row from the action response when possible, then trigger a background refetch for the active query;
- clients must not infer that unrelated rows were refreshed just because one action completed;
- if `countMode != exact`, the UI must not present a precise total or page count as authoritative;
- when `count` is omitted, pagination should rely only on `nextPageToken` and visible rows, not on synthetic totals;
- list/detail views should expose a visible refresh timestamp or equivalent stale-state indicator on operator-facing surfaces.

---

## 12. Canonical adapter projection for UI consumers

### 12.1 Preferred top-level fields

When an adapter API exposes Temporal-backed data to UI consumers, it should promote these values to stable top-level fields instead of forcing clients to read raw maps:

- `workflowId`
- `runId`
- `taskId`
- `workflowType`
- `entry`
- `ownerType`
- `ownerId`
- `state`
- `temporalStatus`
- `closeStatus`
- `title`
- `summary`
- `waitingReason`
- `attentionRequired`
- `updatedAt`
- `startedAt`
- `closedAt`
- `dashboardStatus`

### 12.2 Raw blobs still allowed

The raw `searchAttributes` and `memo` objects may still be returned for:

- debugging,
- admin views,
- migration comparisons,
- adapter parity checks.

But those raw maps are not the long-term primary UI contract.

### 12.3 Artifact references

`artifactRefs[]` belongs on detail responses and on list responses only when the payload size remains reasonable.

List views should not require artifact hydration to render basic rows.

---

## 13. UI integration requirements

### 13.1 Current dashboard constraint

The current dashboard runtime config and route shell do not yet expose a first-class `temporal` source.

Therefore, Temporal-backed work must initially be shown in one of two ways:

1. through existing `/tasks/*` compatibility adapters, or
2. by explicitly extending the dashboard to understand a `temporal` source.

### 13.2 Minimum requirements for a first-class `temporal` dashboard source

If MoonMind adds a first-class `temporal` source to the dashboard, the minimum required changes are:

- add `temporal` endpoints to the dashboard runtime config,
- extend route/path allowlists to support Temporal-backed detail pages,
- add Temporal status normalization,
- add Temporal list/detail fetchers,
- add Temporal action handlers for:
  - update,
  - signal,
  - cancel,
  - optional rerun,
- display `workflowId` as the durable handle,
- label `runId` as run/debug metadata.

### 13.3 Minimum requirements for compatibility-only integration

If the dashboard continues to stay task-oriented for now, compatibility adapters must still preserve:

- `taskId == workflowId` for Temporal-backed rows,
- `workflowId` as a first-class field rather than only in debug metadata,
- exact Temporal-backed `state` and `temporalStatus`,
- the canonical `updatedAt` ordering semantics,
- `waitingReason` / `attentionRequired` when `state = awaiting_external`,
- the `dashboardStatus` mapping defined above.

---

## 14. Migration rules for source of truth and projections

### 14.1 Projection discipline

During migration, any app DB projection or adapter cache that mirrors Temporal-managed executions must mirror these canonical fields faithfully:

- `workflowId`
- `runId`
- `workflowType`
- `mm_owner_type`
- `mm_owner_id`
- `mm_state`
- `mm_updated_at`
- `mm_entry`
- Memo `title`
- Memo `summary`

### 14.2 What a projection may add

A projection may add helper fields for:

- authorization,
- joins,
- migration telemetry,
- dashboard performance,
- reconciliation status.

It may not redefine the meaning of canonical Visibility-backed fields.

### 14.3 Drift rule

If the projection and Temporal-backed canonical execution metadata drift, the system should repair the projection rather than redefining the query contract around projection drift.

---

## 15. Implementation status against this document

### 15.1 Implemented today in the current adapter/projection layer

- Search Attribute-style projection fields: `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`
- required Memo fields: `title`, `summary`
- optional Memo refs: `input_ref`, `manifest_ref`
- exact list filters: `workflowType`, `state`, `ownerId`
- opaque `nextPageToken`
- exact count in `/api/executions`
- stable default ordering by recency then `workflowId`
- update/signal/cancel adapter endpoints
- Continue-As-New behavior for rerun-style updates while preserving `workflowId`

### 15.2 Not yet implemented in true Temporal Visibility-backed query execution

- Visibility-native query/count as the actual backend for `/api/executions`
- `mm_owner_type` as a first-class Visibility field
- `ownerType` filter on the public adapter API
- `entry` filter on the public adapter API
- `repo` and `integration` filters
- first-class `temporal` dashboard source
- task-surface enforcement of `taskId == workflowId` for Temporal-backed rows
- canonical top-level `title` / `summary` / `entry` fields on adapter list rows
- canonical top-level `ownerType`, `waitingReason`, and `attentionRequired` fields

### 15.3 Transitional gaps to retire

- compatibility dashboards that collapse all waiting states into a generic action-required label
- any task-oriented payload that hides `workflowId` in raw/debug-only metadata

---

## 16. Acceptance criteria

This document is operationally done when:

1. Search Attribute names, types, and required/optional status are fixed.
2. Memo fields for list/detail presentation are fixed.
3. Default ordering and `mm_updated_at` mutation rules are fixed.
4. Compatibility status mapping is fixed.
5. Pagination token rules and count semantics are fixed.
6. Adapter/projection layers are explicitly bound to these semantics.
7. The identifier bridge for task-oriented surfaces is fixed (`taskId == workflowId` for Temporal-backed rows).
8. Owner semantics are fixed (`mm_owner_type` + `mm_owner_id`, no `unknown` in the target contract).
9. Waiting-state metadata is fixed (`waitingReason` + `attentionRequired`).
10. The UI path for Temporal-backed rows is chosen: compatibility-only for now, or first-class `temporal` source.

---

## 17. Open follow-ups

1. Add `entry` and `ownerType` filters to the public adapter API.
2. Add top-level `taskId`, `ownerType`, `waitingReason`, and `attentionRequired` fields to adapter payloads.
3. Decide when `mm_repo` and `mm_integration` become required for specific workflow families.
4. Decide whether `/api/executions` remains an adapter surface or evolves into a stable public product API after task compatibility layers retire.
