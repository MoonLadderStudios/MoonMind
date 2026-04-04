# Visibility and UI Query Model

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-VisibilityAndUiQueryModel.md`](../tmp/remaining-work/Temporal-VisibilityAndUiQueryModel.md)

**Status:** Draft  
**Owner:** MoonMind Platform  
**Last updated:** 2026-04-04  
**Audience:** backend, dashboard, API, workflow authors

---

## 1. Purpose

Define the canonical **Visibility-backed query model** for **Temporal-managed executions** in MoonMind.

This document turns the higher-level decisions in the architecture and lifecycle docs into an implementation-facing contract for:

- required **Search Attributes**
- allowed **list/detail filters**
- **Memo** fields used by UI projections
- default **sorting** and **recency** behavior
- **pagination token** and **count** semantics
- compatibility mapping from Temporal execution data into the current **task-oriented UI**
- exact vs compatibility status presentation rules
- waiting and attention metadata for blocked executions

This document is intentionally the bridge between:

- the target rule that **Temporal Visibility** is the source of truth for Temporal-managed list/query/count, and
- the current implementation reality that MoonMind still has task-oriented `/tasks/*` surfaces and adapter-style `/api/executions` APIs during migration

---

## 2. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`
- `docs/Temporal/WorkflowArtifactSystemDesign.md`
- `docs/UI/MissionControlArchitecture.md`

This document narrows several open items from those docs so frontend and backend work can proceed without inventing ad hoc query behavior.

---

## 3. Scope and non-goals

## 3.1 In scope

- Search Attribute registry for Temporal-managed executions
- Memo registry for list/detail presentation
- canonical list/detail field model for Temporal-backed rows
- allowed filters, sort order, recency rules, pagination, and counts
- compatibility mapping into Mission Control task-oriented grouping semantics
- migration rules for adapter APIs and unified task surfaces

## 3.2 Out of scope

- event-history rendering or Temporal Web UI parity
- artifact storage, upload, or ACL design
- worker topology and task queue routing
- full dashboard route redesign
- raw Temporal server query syntax details

---

## 4. Current implementation reality and target contract

## 4.1 Current implementation reality

MoonMind already has a Temporal execution adapter surface, including endpoints such as:

- `POST /api/executions`
- `GET /api/executions`
- `GET /api/executions/{workflowId}`
- `POST /api/executions/{workflowId}/update`
- `POST /api/executions/{workflowId}/signal`
- `POST /api/executions/{workflowId}/cancel`

During migration, some of that surface may still be backed by:

- `TemporalExecutionService`
- app DB projection rows
- adapter/cache/reconciliation layers

Current list behavior may already resemble the target shape, but the projection layer is not the semantic owner of query behavior.

## 4.2 Target contract

For **Temporal-managed work**, **Temporal Visibility** is the source of truth for:

- list
- filter/query
- pagination
- count semantics

The app DB projection may continue to exist during migration, but it is an **adapter/cache/reconciliation layer**, not the owner of canonical query semantics.

Contractually:

1. any adapter API for Temporal-backed list/detail data must preserve **Visibility semantics**
2. unified `/tasks/*` surfaces may reshape Temporal rows into task-oriented payloads, but they must **not invent conflicting state, sort, or pagination rules**
3. if a projection disagrees with Temporal-backed canonical fields, **Temporal wins**
4. provider-specific payloads and runtime-native details must not become ad hoc list/query semantics outside the canonical field model

---

## 5. Canonical query entity model

## 5.1 Canonical entity

The primary durable query entity for Temporal-managed work is a **Workflow Execution**.

MoonMind may still use the word **task** in compatibility APIs and UI surfaces, but those rows map to Workflow Executions when the runtime is Temporal-backed.

## 5.2 Canonical identifiers

- `workflowId`: durable canonical identifier for Temporal-managed work
- `runId`: current/latest Temporal run instance identifier; detail/debug oriented
- `taskId`: compatibility alias for task-oriented APIs during migration

Rules:

- `workflowId` is the stable identity used for links, cache keys, and adapter lookups
- `runId` must not replace `workflowId` as the primary product handle
- detail pages may show `runId`, but only as run/debug metadata

## 5.2.1 Compatibility identifier bridge

For any Temporal-backed row exposed through a task-oriented compatibility surface:

- `taskId` **must equal** `workflowId`
- APIs may return both `taskId` and `workflowId` during migration
- compatibility adapters must not mint a second opaque identifier for the same Temporal execution
- `runId` must never be used as the route identifier for task-oriented detail pages

This keeps the task UI stable without introducing another alias table or migration dependency.

## 5.3 Canonical list row fields

A Temporal-backed list row should be expressible with the following stable fields:

- `workflowId`
- `taskId?`
- `workflowType`
- `entry`
- `state`
- `temporalStatus`
- `title`
- `summary`
- `ownerType`
- `ownerId`
- `updatedAt`
- `startedAt`
- `closedAt?`
- `waitingReason?`
- `attentionRequired?`
- `dashboardStatus`

Optional bounded fields when available:

- `repo?`
- `integration?`

Rules:

- `state` is the exact `mm_state` value
- `dashboardStatus` is a compatibility grouping only
- list fields should not require artifact hydration to render a normal row
- provider-native raw payloads should not become top-level list fields

## 5.4 Canonical detail fields

A Temporal-backed detail model may additionally include:

- `runId`
- `closeStatus`
- `artifactRefs[]`
- `progress`
- `stepsHref`
- `waitingReason?`
- `attentionRequired?`
- `searchAttributes`
- `memo`
- optional debug or reconciliation metadata
- optional live query-derived fields such as active child state or current execution phase

Detail views should show the exact Temporal-facing state (`state`, `temporalStatus`, `closeStatus`) even when list pages group them into broader task-style statuses.

---

## 6. Search Attribute registry

## 6.1 Naming rules

All MoonMind-owned Search Attributes for this query model follow these rules:

- prefix `mm_`
- lowercase snake_case
- bounded values only
- no secrets
- no large free text
- no step-ledger rows or attempt history
- no display-only user prose

## 6.2 Required Search Attributes

| Name | Type | Required | Lifecycle owner | Update rule | Notes |
| --- | --- | --- | --- | --- | --- |
| `mm_owner_type` | keyword | Yes | API/workflow start path | Set at start; immutable in v1 | `user`, `system`, or `service` |
| `mm_owner_id` | keyword | Yes | API/workflow start path | Set at start; immutable in v1 | Principal identifier |
| `mm_state` | keyword | Yes | Workflow lifecycle logic | Update on every domain-state transition | Exact MoonMind lifecycle state |
| `mm_updated_at` | datetime | Yes | Workflow lifecycle logic | Update on meaningful user-visible mutation | Default recency/sort key |
| `mm_entry` | keyword | Yes | Workflow start path | Set at start; immutable | Execution category for UI/query surfaces |

## 6.3 Optional Search Attributes

| Name | Type | Required | When to set | Notes |
| --- | --- | --- | --- | --- |
| `mm_repo` | keyword | No | Repo-scoped executions when filtering is needed | Stable bounded repo identifier |
| `mm_integration` | keyword | No | Integration-centric execution where filtering is useful | Examples: `jules`, `github`, `openclaw` |
| `mm_scheduled_for` | datetime | No | Delayed start / schedule-backed execution | Queryable expected start time |

## 6.4 Deferred Search Attributes

These are intentionally **not** required in v1:

- `mm_stage`
- `mm_error_category`
- free-text search attributes
- unbounded tag arrays
- child-workflow/activity-level indexing fields

If one becomes necessary, update this document first rather than adding it ad hoc in code.

## 6.5 Required value sets

### `mm_state`

Allowed values for v1:

- `scheduled`
- `initializing`
- `waiting_on_dependencies`
- `planning`
- `awaiting_slot`
- `executing`
- `awaiting_external`
- `proposals`
- `finalizing`
- `completed`
- `failed`
- `canceled`

Rules:

- `mm_state` must be set immediately on workflow start
- terminal mapping must remain consistent with Temporal close status:
  - completed → `completed`
  - failed / terminated / timed out → `failed`
  - canceled → `canceled`

### `mm_owner_type`

Allowed values for v1:

- `user`
- `system`
- `service`

Rules:

- `mm_owner_type` and `mm_owner_id` must always be populated together
- `unknown` is not an allowed target-state value
- standard end-user views should only show executions where `mm_owner_type = user` and `mm_owner_id` matches the authenticated principal, unless a product surface explicitly says otherwise

### `mm_entry`

Allowed values for v1 (currently normalized by the executions API):

- `run`
- `manifest`
- `provider_profile`

*(Note: additional workflow types like `agent_run` and `oauth_session` are planned or used internally but are not currently normalized for top-level list filtering by the primary executions API.)*

Rules:

- `mm_entry` is required
- `mm_entry` should not be inferred by parsing raw workflow type strings in UI code
- compatibility surfaces may collapse multiple `entry` values into one broader product grouping, but the exact value must remain queryable

---

## 7. Memo registry

## 7.1 Required Memo fields

| Field | Required | Purpose | Rules |
| --- | --- | --- | --- |
| `title` | Yes | Human-readable execution title | small, display-safe, mutable |
| `summary` | Yes | Compact current summary for list/detail surfaces | small, display-safe, mutable |

## 7.2 Optional Memo fields

| Field | Required | Purpose | Rules |
| --- | --- | --- | --- |
| `input_ref` | No | Safe reference to input artifact | reference only |
| `manifest_ref` | No | Safe reference for manifest-driven workflows | reference only |
| `error_category` | No | Debug/detail classification for failures | display/debug only |
| `entry_label` | No | Optional human-friendly display label | small, bounded |
| `progress_hint` | No | Compact execution-level progress hint when useful | small, bounded, never a step ledger |

## 7.3 Memo rules

- keep Memo small and human-readable
- never store secrets, full prompts, manifests, or large payloads in Memo
- never store step-ledger rows, attempts, `checks[]`, or long error bodies in Memo
- Memo is for **display metadata**, not filtering
- list views should rely on `title` and `summary`, not raw artifact payloads

---

## 8. Allowed filters and query model

## 8.1 Exact filters for Temporal-backed queries

Allowed exact-match filters for Temporal-managed list queries:

- `workflowType`
- `ownerType`
- `ownerId`
- `state`
- `entry`
- `repo`
- `integration`

## 8.2 Filter priorities

### Required now

- `workflowType`
- `ownerId`
- `state`

### Required next

- `entry`
- `ownerType`

### Optional later

- `repo`
- `integration`

These should ship only when a real UI/API consumer needs them.

## 8.3 Not in v1

The following are intentionally out of scope for v1 query behavior:

- free-text search
- fuzzy title search
- arbitrary multi-field OR composition
- arbitrary date-range filtering
- child-workflow filtering
- activity-level filtering

## 8.4 Ownership scoping rules

Ownership is not just a filter; it is part of authorization.

Rules:

- standard users are implicitly scoped to their own executions
- standard users must not see `system` or `service` executions on user-facing task pages unless the product explicitly supports that
- non-admin callers must not query another user’s executions
- admin/operator callers may filter by `ownerType`, `ownerId`, or omit them

The UI should not offer arbitrary owner-picking to ordinary end users.

---

## 9. Sorting, recency, and stable ordering

## 9.1 Default ordering

The canonical default order for Temporal-managed list rows is:

1. `mm_updated_at DESC`
2. `workflowId DESC` as deterministic tie-breaker

This must remain true whether the backing implementation comes directly from Temporal Visibility or from a temporary adapter/projection layer.

## 9.2 No queue semantics

Temporal-backed lists must not imply:

- FIFO queue ordering
- worker queue position
- queue depth semantics
- “next to run” promises

Task queues are plumbing, not a user-visible ordering model.

## 9.3 What moves `mm_updated_at`

`mm_updated_at` should move on **meaningful user-visible mutations**, including:

- domain-state transitions
- accepted updates
- signal handling that changes visible workflow state
- pause/resume/cancel/rerun actions
- terminal success/failure transitions
- bounded progress checkpoints that materially affect UI recency
- step-ready, step-started, step-reviewing, step-succeeded, step-failed, step-skipped, and step-canceled transitions
- title/summary changes that materially alter visible list/detail presentation

`mm_updated_at` should **not** move on every:

- heartbeat
- log line
- low-level retry
- internal polling tick
- low-level retry/backoff detail inside one step attempt

Implementation guidance:

- progress updates must be bounded and meaningful before they affect `mm_updated_at`
- telemetry spam must not churn ordering

---

## 10. Compatibility status mapping for Mission Control

The current dashboard uses broad normalized task statuses such as:

- `queued`
- `running`
- `awaiting_action`
- `waiting`
- `completed`
- `failed`
- `canceled`

Temporal-backed rows should preserve exact Temporal/MoonMind state **and** provide a compatibility grouping.

## 10.1 Exact vs compatibility fields

- **Exact fields:** `state`, `temporalStatus`, `closeStatus`
- **Compatibility field:** `dashboardStatus`

## 10.2 v1 mapping

| Exact Temporal-backed state | Compatibility dashboard status | Notes |
| --- | --- | --- |
| `scheduled` | `queued` | waiting for deferred start |
| `initializing` | `queued` | not yet materially executing user work |
| `waiting_on_dependencies` | `waiting` | blocked on prerequisite work |
| `planning` | `running` | active pre-execution work |
| `awaiting_slot` | `queued` | waiting for a bounded runtime resource |
| `executing` | `running` | active execution |
| `awaiting_external` | `awaiting_action` | compatibility grouping only |
| `proposals` | `running` | still active, post-execution proposal phase |
| `finalizing` | `running` | still in-flight |
| `completed` | `completed` | terminal success |
| `failed` | `failed` | terminal failure |
| `canceled` | `canceled` | terminal cancellation |

## 10.3 Important note on `awaiting_external`

`awaiting_external` does **not** always mean the current user must take action.

For compatibility surfaces it maps to `awaiting_action` because that is the nearest existing grouped status, but exact detail views must still show `awaiting_external`.

That distinction matters because the actual cause may be:

- approval required
- provider callback wait
- provider completion wait
- operator pause
- retry backoff
- other non-user-blocking external wait

## 10.4 Required waiting metadata

To avoid misleading users, Temporal-backed rows in blocked states should expose:

- `waitingReason`
- `attentionRequired`

### Allowed `waitingReason` values for v1

- `approval_required`
- `external_callback`
- `external_completion`
- `operator_paused`
- `retry_backoff`
- `dependency_wait`
- `provider_profile_slot`
- `unknown_external`

Rules:

- `waitingReason` should be set whenever the execution is in a blocked or waiting state where the reason is knowable
- `attentionRequired = true` only when progress is blocked on human/operator action exposed by the current product surface
- compatibility dashboards may continue using broad grouped statuses, but they must not imply user action is required when `attentionRequired = false`

---

## 11. Pagination and count strategy

## 11.1 Pagination inputs

Temporal-backed list APIs use:

- `pageSize`
- `nextPageToken`

Rules:

- `nextPageToken` is opaque
- clients must only echo it back to the same endpoint with the same query scope
- changing filters invalidates the token

## 11.2 Current adapter behavior

Current implementations may still use opaque encoded offsets internally.

That is an implementation detail, not a client contract.

## 11.3 Target behavior

When list/query/count is fully backed by Temporal Visibility, the API should pass through Temporal-backed pagination semantics without forcing the UI to know Temporal internals.

## 11.4 Count behavior

List responses may include:

- `count`
- `countMode`

Allowed `countMode` values:

- `exact`
- `estimated_or_unknown`

Target rule:

- return exact count when operationally acceptable
- otherwise return `estimated_or_unknown` or omit `count`

## 11.5 Freshness and degraded-read behavior

In an eventually consistent system:

- the response from a successful update/signal/cancel/rerun action is the authoritative immediate view for that execution
- list pages should patch the acted-on row from that action response when possible, then background-refetch the active query
- clients must not infer that unrelated rows were refreshed
- when `countMode != exact`, the UI must not present a precise total as authoritative
- when `count` is omitted, pagination should rely on rows plus `nextPageToken`, not synthetic totals
- operator-facing surfaces should expose a refresh timestamp or equivalent stale-state indicator

---

## 12. Canonical adapter projection for UI consumers

## 12.1 Preferred top-level fields

When an adapter API exposes Temporal-backed data to UI consumers, it should promote these values to stable top-level fields:

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

Optional bounded top-level fields when available:

- `repo`
- `integration`

## 12.2 Raw blobs still allowed

Raw `searchAttributes` and `memo` objects may still be returned for:

- debugging
- admin/operator views
- migration parity checks
- adapter inspection

But those raw maps are not the preferred long-term UI contract.

## 12.3 Artifact references

`artifactRefs[]` belongs on detail responses and on list responses only when the payload remains small and useful.

List views should not require artifact hydration to render basic execution rows.

---

## 13. UI integration requirements

The dashboard runtime config and route shell should treat `temporal` as the primary execution source for Temporal-backed rows.

The dashboard should use:

- Temporal-backed list/detail endpoints
- `workflowId` as the durable handle
- `taskId == workflowId` on compatibility surfaces
- `runId` only as run/debug metadata
- exact state plus compatibility grouping
- waiting metadata (`waitingReason`, `attentionRequired`)
- Temporal action handlers for update, signal, cancel, and rerun semantics

Higher-level product actions may still be composed from those primitives.

---

## 14. Projection rules and source of truth

## 14.1 Projection discipline

Any app DB projection or adapter cache that mirrors Temporal-managed executions must mirror these canonical fields faithfully:

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

## 14.2 What a projection may add

A projection may add helper fields for:

- authorization
- joins
- migration telemetry
- dashboard performance
- reconciliation status

It may not redefine the meaning of canonical Visibility-backed fields.

## 14.3 Drift rule

If a projection and Temporal-backed canonical metadata drift, the system should repair the projection rather than redefining the query contract around projection drift.

---

## 15. Target contract vs implementation

This document specifies the **desired** Visibility, Memo, adapter list/detail, and UI query contract.

Current implementation gaps, Visibility-native query work, and items to retire after substrate cutover belong in:

- `docs/tmp/remaining-work/Temporal-VisibilityAndUiQueryModel.md`

This document should remain the normative semantics source rather than a live gap tracker.

---

## 16. Acceptance criteria

This document is operationally done when:

1. Search Attribute names, types, and required/optional status are fixed
2. Memo fields for list/detail presentation are fixed
3. default ordering and `mm_updated_at` mutation rules are fixed
4. compatibility status mapping is fixed
5. pagination token rules and count semantics are fixed
6. adapter/projection layers are explicitly bound to these semantics
7. the identifier bridge is fixed (`taskId == workflowId` for Temporal-backed rows)
8. owner semantics are fixed (`mm_owner_type` + `mm_owner_id`)
9. step-ledger truth is explicitly kept out of Visibility and Memo
9. waiting metadata is fixed (`waitingReason` + `attentionRequired`)
10. `mm_entry` values are fixed and cover the current workflow catalog
11. the UI path for Temporal-backed rows is implemented as a first-class source

---

## 17. Open follow-ups

1. Add `entry` and `ownerType` filters to public adapter APIs if not already present
2. Add top-level `taskId`, `ownerType`, `waitingReason`, and `attentionRequired` fields wherever adapter payloads still lack them
3. Decide when `mm_repo` and `mm_integration` become required for specific workflow families
4. Decide whether `/api/executions` remains a migration adapter surface or evolves into a stable public API after task compatibility layers retire
5. Decide whether any additional `mm_entry` values are needed beyond the current workflow catalog
