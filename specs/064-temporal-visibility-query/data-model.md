# Data Model: Temporal Visibility Query Model

## 1. Canonical query entity

### WorkflowExecutionQueryRow

Temporal-managed work is exposed as a Workflow Execution with a stable top-level adapter shape.

| Field | Type | Required | Source | Notes |
| --- | --- | --- | --- | --- |
| `workflowId` | string | Yes | Temporal workflow identity | Durable canonical identifier for links, lookups, and cache keys. |
| `taskId` | string | Compatibility-only | Adapter projection | Required on task-oriented surfaces and must equal `workflowId`. |
| `runId` | string | Detail/debug | Temporal run identity | Never the primary product handle. |
| `workflowType` | string | Yes | Temporal workflow type | Exact type such as `MoonMind.Run`. |
| `entry` | enum(`run`,`manifest`) | Yes | `mm_entry` | Exact entry classification used for filtering. |
| `state` | enum | Yes | `mm_state` | Exact MoonMind lifecycle state. |
| `temporalStatus` | enum(`running`,`completed`,`failed`,`canceled`) | Yes | Close-status adapter | Broad Temporal execution status. |
| `closeStatus` | enum or null | Detail | Temporal close status | Preserved for exact detail/debug semantics. |
| `title` | string | Yes | Memo | Display-safe title. |
| `summary` | string | Yes | Memo | Display-safe summary. |
| `ownerType` | enum(`user`,`system`,`service`) | Yes | `mm_owner_type` | Must be populated with `ownerId`. |
| `ownerId` | string | Yes | `mm_owner_id` | Scoped principal or bounded service/system identifier. |
| `updatedAt` | datetime | Yes | `mm_updated_at` | Default recency/sort field. |
| `startedAt` | datetime | Yes | Projection/runtime | Start timestamp. |
| `closedAt` | datetime or null | No | Projection/runtime | Terminal timestamp. |
| `waitingReason` | bounded enum or null | No | Adapter/runtime state | Present when `state = awaiting_external`. |
| `attentionRequired` | bool | Yes | Adapter/runtime state | Signals whether a human/operator action is actually required. |
| `dashboardStatus` | enum | Yes | Compatibility projection | Derived from exact `state`; never replaces it. |
| `artifactRefs` | string[] | Detail-only by default | Memo/artifact linkage | Omitted from list rows unless payload size remains small. |
| `searchAttributes` | object | Debug/admin | Visibility mirror | Raw map for parity and diagnosis. |
| `memo` | object | Debug/admin | Memo mirror | Raw map for parity and diagnosis. |

## 2. Search Attribute model

### VisibilitySearchMetadata

| Field | Type | Required | Update rule | Constraints |
| --- | --- | --- | --- | --- |
| `mm_owner_type` | keyword | Yes | Set at start; immutable in v1 | Allowed values: `user`, `system`, `service`. |
| `mm_owner_id` | keyword | Yes | Set at start; immutable in v1 | Must be populated with `mm_owner_type`. |
| `mm_state` | keyword | Yes | Update on domain state transitions | Allowed v1 states only. |
| `mm_updated_at` | datetime | Yes | Update on meaningful user-visible mutations only | Drives default ordering and recency. |
| `mm_entry` | keyword | Yes | Set at start; immutable | Allowed values: `run`, `manifest`. |
| `mm_repo` | keyword | No | Set only for bounded repo-scoped workflows | Optional bounded filter field. |
| `mm_integration` | keyword | No | Set only for bounded integration-scoped workflows | Optional bounded filter field. |

### Deferred attributes

The following remain out of scope for v1 unless the governing source doc changes first:

- `mm_stage`
- `mm_error_category`
- free-text search attributes
- unbounded tags
- child-workflow/activity-level filter fields

## 3. Memo model

### ExecutionMemoProjection

| Field | Type | Required | Purpose | Constraints |
| --- | --- | --- | --- | --- |
| `title` | string | Yes | Primary list/detail title | Small, display-safe, not used as a free-text filter channel. |
| `summary` | string | Yes | Short execution summary | Small, display-safe, bounded. |
| `input_ref` | string or null | No | Safe artifact reference | Optional detail/helper metadata only. |
| `manifest_ref` | string or null | No | Safe artifact reference | Optional detail/helper metadata only. |
| `artifactRefs[]` | string[] | No | Additional safe artifact references | Prefer detail-only exposure. |

Memo must remain display-safe, bounded, and free of secrets or large payloads.

## 4. Compatibility identifier bridge

### CompatibilityIdentifierBridge

| Rule | Invariant |
| --- | --- |
| Temporal-backed task rows | `taskId == workflowId` |
| Detail routing | `/tasks/:taskId` stays canonical for compatibility surfaces |
| Legacy aliases | May be accepted temporarily, but server canonicalizes back to `workflowId` routes |
| Debug metadata | `runId` may be shown in detail views, but never becomes the durable route key |

## 5. Status projection model

### DashboardStatusProjection

| Exact `state` | `dashboardStatus` | `temporalStatus` expectation | Waiting metadata rule |
| --- | --- | --- | --- |
| `initializing` | `queued` | `running` | No waiting metadata |
| `planning` | `queued` | `running` | No waiting metadata |
| `executing` | `running` | `running` | No waiting metadata |
| `awaiting_external` | `awaiting_action` | `running` | `waitingReason` required; `attentionRequired` indicates whether user/operator action is actually needed |
| `finalizing` | `running` | `running` | No waiting metadata |
| `succeeded` | `succeeded` | `completed` | Terminal |
| `failed` | `failed` | `failed` | Terminal |
| `canceled` | `cancelled` | `canceled` | Terminal |

## 6. Pagination and count contract

### PaginationContract

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `nextPageToken` | opaque string or null | No | Bound to one endpoint and one filter/sort scope. |
| `count` | integer or null | No | Present when backend can provide a meaningful total for the current contract. |
| `countMode` | enum(`exact`,`estimated_or_unknown`) or omitted | No | `exact` for pure Temporal-backed list queries; degraded/omitted for contracts that cannot guarantee exact totals. |
| `scopeFingerprint` | internal string | Internal | Used to reject stale token reuse after scope changes. |
| `staleState` | bool | Operator-facing UI concern | Indicates list/detail freshness degradation during migration or action patching. |

Rules:

- Default ordering is `mm_updated_at DESC`, then `workflowId DESC`.
- Tokens are opaque to clients.
- Tokens are invalid if filter or sort scope changes.
- Mixed-source pages must use separate source cursors or an aggregator-owned merged cursor contract.

## 7. Projection mirror model

### ProjectionMirrorRecord

The app database projection mirrors canonical Temporal-backed fields for fast adapter reads, compatibility, and reconciliation.

Key mirrored fields:

- `workflow_id`, `run_id`, `workflow_type`
- `owner_type`, `owner_id`
- `state`, `close_status`, `entry`
- `search_attributes`, `memo`
- `waiting_reason`, `attention_required`
- `started_at`, `updated_at`, `closed_at`

Rules:

- Projection data may add helper metadata for non-semantic concerns.
- Projection data must not redefine list/filter/pagination/count semantics.
- If projection fields drift from canonical Temporal-backed truth, repair in favor of Temporal semantics.

## 8. Compatibility adapter model

### TemporalCompatibilityAdapterContract

| Field | Type | Purpose |
| --- | --- | --- |
| `taskId` | string | Task-oriented compatibility identifier equal to `workflowId` |
| `workflowId` | string | Durable canonical Temporal execution identifier |
| `workflowType` | string | Root workflow catalog value |
| `entry` | string | Canonical `run` or `manifest` entry discriminator |
| `state` | string | Exact MoonMind lifecycle state |
| `dashboardStatus` | string | Compatibility grouping derived from the exact state |
| `waitingReason` | string/null | Bounded wait metadata for `awaiting_external` rows |
| `attentionRequired` | boolean | Indicates whether a human/operator action is required |

This compatibility contract is distinct from worker runtime selection and does not require a first-class `temporal` dashboard source for the current delivery.
