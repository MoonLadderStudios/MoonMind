# Task Execution Compatibility Model

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-TaskExecutionCompatibilityModel.md`](../tmp/remaining-work/Temporal-TaskExecutionCompatibilityModel.md)

**Status:** Active  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-27  
**Audience:** backend, dashboard, API, workflow authors

---

## 1. Purpose

This document defines how MoonMind keeps a **task-first product surface** while
runtime execution is fully **Temporal-backed**.

It answers four concrete questions:

- how a Temporal execution appears inside `/tasks/*` product flows
- which identifiers are canonical for list, detail, edit, cancel, rerun, and
  approval behavior
- how Temporal execution state maps onto dashboard task status categories
- how `/api/executions` relates to task-oriented compatibility routes

---

## 2. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Tasks/TaskArchitecture.md`
- `docs/UI/MissionControlArchitecture.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`

---

## 3. Compatibility principles

1. **`task` remains the primary product noun.**  
   Mission Control and task-oriented routes continue to present work as tasks.

2. **`workflow execution` remains the runtime noun.**  
   Temporal implementation docs, worker code, and execution APIs should use
   Temporal language precisely.

3. **There is one live execution source.**  
   Task execution compatibility adapts product routes onto Temporal; it does not
   multiplex across queue or system backends.

4. **Identifiers are opaque.**  
   Clients must not parse source or lifecycle meaning from ID shape.

5. **Compatibility adapters translate product actions into Temporal controls.**  
   The UI thinks in terms like edit, approve, rerun, pause, resume, and cancel,
   while the backend maps those actions to start/update/signal/cancel behavior.

6. **Temporal truth is preserved.**  
   Normalized dashboard fields may simplify state, but raw execution metadata
   must remain available for operator/debug views.

---

## 4. Source model

### 4.1 Execution source

For task list/detail purposes, the execution source is:

- `temporal`

### 4.2 Adjacent modules

These remain adjacent product modules, not execution sources:

- `proposals`
- `schedules`

### 4.3 Entry model inside the Temporal source

For Temporal-backed work, the source remains `temporal`, while `entry`
distinguishes the higher-level flow:

- `entry = "run"` for `MoonMind.Run`
- `entry = "manifest"` for `MoonMind.ManifestIngest`

---

## 5. Vocabulary and identifiers

### 5.1 Canonical terms

| Layer | Preferred term |
| --- | --- |
| Product UI | `task` |
| Runtime | `workflow execution` |

### 5.2 Identifier fields

| Field | Meaning | Rule |
| --- | --- | --- |
| `taskId` | Product-facing handle used by `/tasks/*` | Must equal `workflowId` |
| `workflowId` | Canonical Temporal durable execution identifier | Required |
| `temporalRunId` | Current/latest run instance | Detail/debug only |

### 5.3 Rules

- for task views, **`taskId == workflowId`**
- `workflowId` is the durable handle across Continue-As-New and rerun chains
- `temporalRunId` may change and must never be the primary route key
- clients must treat all IDs as opaque strings

### 5.4 `/tasks/{taskId}` resolution

Task detail routing should resolve through canonical server-side execution
metadata, not backend probing or source guessing from text shape.

---

## 6. Normalized compatibility payload model

### 6.1 Task list row

| Field | Meaning | Temporal mapping |
| --- | --- | --- |
| `taskId` | Primary product handle | `workflowId` |
| `source` | Execution substrate | `"temporal"` |
| `entry` | Product entry shape | `mm_entry` |
| `title` | Display label | `memo.title` |
| `summary` | Secondary summary | `memo.summary` |
| `status` | Dashboard-normalized status | mapped from `rawState` |
| `rawState` | Exact MoonMind workflow state | execution `state` |
| `temporalStatus` | Runtime/close status | execution `temporalStatus` |
| `workflowId` | Durable execution ID | execution `workflowId` |
| `workflowType` | Root workflow type | execution `workflowType` |
| `ownerType` | Owner class | `mm_owner_type` |
| `ownerId` | Owner identifier | `mm_owner_id` |
| `createdAt` | Start time | `startedAt` |
| `updatedAt` | Most recent change | `updatedAt` |
| `closedAt` | Terminal close time | `closedAt` |
| `detailHref` | Canonical detail route | `/tasks/{taskId}` |

### 6.2 Task detail payload

Task detail should include the normalized row fields plus:

- `temporalRunId`
- `closeStatus`
- `artifactRefs`
- `searchAttributes`
- `memo`
- optional action-capability metadata
- optional debug fields such as `namespace`

Rules:

- detail views may show `temporalRunId`, but the route stays anchored to
  `taskId == workflowId`
- execution parameters should be surfaced selectively rather than dumped blindly
- Search Attributes and Memo must remain bounded and secret-safe

---

## 7. Status compatibility model

### 7.1 Dashboard-normalized statuses

The current dashboard status family is:

- `queued`
- `waiting`
- `running`
- `awaiting_action`
- `completed`
- `failed`
- `canceled`

### 7.2 State mapping

| `mm_state` value | Normalized status |
| --- | --- |
| `scheduled` | `queued` |
| `initializing` | `queued` |
| `waiting_on_dependencies` | `waiting` |
| `planning` | `running` |
| `awaiting_slot` | `queued` |
| `executing` | `running` |
| `proposals` | `running` |
| `awaiting_external` | `awaiting_action` |
| `finalizing` | `running` |
| `completed` | `completed` |
| `failed` | `failed` |
| `canceled` | `canceled` |

### 7.3 Raw values that must remain available

Adapters must preserve:

- `rawState`
- `temporalStatus`
- `closeStatus`
- `waitingReason`
- `attentionRequired`

---

## 8. Action mapping

| Task-facing action | Temporal action | Adapter endpoint |
| --- | --- | --- |
| Create task | Start workflow execution | `POST /api/executions` |
| Edit inputs | Update `UpdateInputs` | `POST /api/executions/{workflowId}/update` |
| Rename task | Update `SetTitle` | `POST /api/executions/{workflowId}/update` |
| Rerun task | Update `RequestRerun` | `POST /api/executions/{workflowId}/update` |
| Approve task | Signal `Approve` | `POST /api/executions/{workflowId}/signal` |
| Pause task | Signal `Pause` | `POST /api/executions/{workflowId}/signal` |
| Resume task | Signal `Resume` | `POST /api/executions/{workflowId}/signal` |
| Deliver callback | Signal `ExternalEvent` | `POST /api/executions/{workflowId}/signal` |
| Cancel task | Cancel workflow | `POST /api/executions/{workflowId}/cancel` |
| Reschedule deferred task | Reschedule signal/update path | `POST /api/executions/{workflowId}/reschedule` |

Rules:

- task UIs should surface accepted/applied/message semantics honestly
- rerun may keep the same `taskId` while changing `temporalRunId`
- cancellation defaults to graceful cancellation in normal product flows

---

## 9. Pagination and counts

For task views backed by Temporal:

- sorting should default to `updatedAt DESC` or `mm_updated_at`
- `nextPageToken` may remain Temporal-specific
- `count` and `countMode` should reflect the actual route behavior

Allowed `countMode` values:

- `exact`
- `estimated_or_unknown`

---

## 10. Source of truth

For Temporal-managed work:

1. **Temporal workflow execution state/history** is authoritative for lifecycle
2. **Temporal Visibility** is authoritative for list/filter/query semantics
3. **MoonMind projection rows** may remain read models and compatibility caches

Compatibility adapters should prefer canonical execution metadata over ad hoc
shadow fields.

---

## 11. UI integration contract

Preferred routes remain:

- `/tasks/list`
- `/tasks/{taskId}`

Rules:

- Temporal-backed task details render inside the standard task shell
- the canonical task detail handle is `taskId == workflowId`
- optional execution-specific aliases are secondary to the canonical task route

---

## 12. Summary

MoonMind's compatibility layer is now simple:

- the product still speaks in terms of **tasks**
- the runtime speaks in terms of **Temporal workflow executions**
- the bridge between them is explicit, Temporal-only, and identifier-stable

That keeps product language stable without preserving the removed legacy
execution backends.
