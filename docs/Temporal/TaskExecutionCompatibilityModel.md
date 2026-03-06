# Task Execution Compatibility Model

Bridge contract between MoonMind's **task-oriented product surfaces** and **Temporal-backed workflow executions** during migration.

**Status:** Draft  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-06  
**Audience:** backend, dashboard, API, workflow authors

---

## 1. Purpose

This document defines the compatibility model that lets MoonMind keep a **task-first UI and API posture** while moving execution onto Temporal.

It answers four concrete questions:

- how a Temporal-backed execution appears inside current `/tasks/*` product flows
- which identifiers are canonical for list, detail, edit, cancel, rerun, approval, and callback behavior
- how Temporal execution state maps onto current dashboard task status categories
- how `/api/executions` relates to task-oriented compatibility routes during migration

This is a **bridge contract**, not a claim that the product is already fully Temporal-native.

---

## 2. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/TaskArchitecture.md`
- `docs/TaskUiArchitecture.md`
- `docs/Temporal/Visibility/VisibilityAndUiQueryModel.md` *(expected companion doc)*

---

## 3. Scope and non-goals

### 3.1 In scope

- compatibility between **task** terminology and **workflow execution** terminology
- identifier rules for `taskId`, `workflowId`, `temporalRunId`, and legacy `runId`
- normalized task row/detail payloads for Temporal-backed work
- status mapping from Temporal domain/runtime state into current dashboard task states
- action mapping from task operations into Temporal create/update/signal/cancel calls
- pagination and count behavior for Temporal-only and mixed-source task lists

### 3.2 Out of scope

- full replacement of `/tasks/*` with a Temporal-native operator UI
- redesign of queue, orchestrator, proposal, or schedule product modules
- exposing Temporal task queues as a user-facing queue model
- collapsing every execution source into a single physical database table
- deciding all long-term post-migration public API names

---

## 4. Current compatibility context

MoonMind is in an explicit transition state:

- the current dashboard remains **task-oriented** and source-oriented
- the current runtime config still centers on **queue**, **orchestrator**, **proposals**, **manifests**, and **schedules**
- Temporal-backed lifecycle APIs already exist under `/api/executions`
- a `TemporalExecutionRecord` projection already exists for lifecycle APIs, filtering, and compatibility behavior

That means compatibility must be **intentional and documented**.

We do **not** want ad hoc field translation where one screen treats a Temporal execution as a task, another screen treats it as a run, and a third screen treats it as a raw workflow handle.

---

## 5. Compatibility principles

1. **`task` remains the user-facing noun during migration.**  
   The product contract remains task-oriented even when the execution substrate is Temporal.

2. **`workflow execution` remains the runtime noun.**  
   Temporal implementation docs, worker code, and execution APIs should use Temporal language precisely.

3. **A Temporal-backed task is not a fake queue item.**  
   It may render in the task dashboard, but it must not inherit queue-order semantics or leasing semantics.

4. **Identifiers are opaque.**  
   Clients must not parse source, entry type, or lifecycle meaning from the textual shape of an ID.

5. **Compatibility adapters should translate task actions into Temporal controls.**  
   The UI should think in terms like edit, approve, rerun, pause, resume, and cancel, not raw Signal and Update names.

6. **Temporal state should not be flattened so aggressively that operators lose meaning.**  
   The dashboard can show normalized task statuses, but raw execution state and Temporal status must remain available.

7. **Temporal-managed list/detail truth moves toward Temporal visibility and workflow state.**  
   Transitional projections are allowed, but they must mirror the documented execution contract instead of inventing a parallel model.

---

## 6. Source model

### 6.1 Execution sources for the task dashboard

For task list/detail purposes, the execution sources are:

- `queue`
- `orchestrator`
- `temporal`

### 6.2 What is **not** an execution source in this document

These remain adjacent dashboard modules, not execution sources in this compatibility model:

- `proposals`
- `schedules`

### 6.3 Entry model inside the Temporal source

For Temporal-backed work, the **source** remains `temporal`, while the execution **entry** distinguishes the higher-level product shape:

- `entry = "run"` for `MoonMind.Run`
- `entry = "manifest"` for `MoonMind.ManifestIngest`

Rules:

- do **not** create a separate dashboard execution source called `manifest` for Temporal-backed work
- manifest pages may later become filtered task views over `source=temporal` + `entry=manifest`
- source answers **where the execution lives**; entry answers **what kind of product flow it represents**

---

## 7. Vocabulary and identifier model

### 7.1 Canonical terms

| Layer | Preferred term | Notes |
| --- | --- | --- |
| Current product UI | `task` | Current dashboard contract |
| Temporal runtime | `workflow execution` | Canonical runtime term |
| Legacy orchestrator compatibility | `run` | Transitional only |

### 7.2 Identifier fields

| Field | Meaning | Temporal-backed rule |
| --- | --- | --- |
| `taskId` | Product-facing handle used by `/tasks/*` | Must equal `workflowId` for Temporal-backed rows |
| `workflowId` | Canonical Temporal durable execution identifier | Required for Temporal-backed rows |
| `temporalRunId` | Current Temporal run instance identifier | Detail/debug only |
| `runId` | Legacy orchestrator compatibility identifier | Must **not** be reused to mean Temporal run ID in task-facing compatibility payloads |

### 7.3 Rules

- For `source=temporal`, **`taskId == workflowId`**.
- `workflowId` is the durable handle that survives Continue-As-New.
- `temporalRunId` may change during rerun or Continue-As-New and must never be used as the primary task route key.
- `runId` remains reserved for legacy orchestrator compatibility and should not be overloaded.
- Clients must treat all IDs as opaque strings.
- Path routing may use explicit `source` or a persisted source mapping; ID shape may be used as an optimization, but not as the compatibility contract.

### 7.4 Rerun identity rule

For Temporal-backed work:

- a rerun that uses **Continue-As-New** keeps the same `workflowId`
- the `temporalRunId` changes
- the compatibility `taskId` stays stable because it equals `workflowId`

This is the critical rule that keeps `/tasks/{taskId}` stable across reruns.

---

## 8. Normalized compatibility payload model

This section defines the **task-compatible shape** that adapters should materialize for Temporal-backed rows and details.

### 8.1 Task list row (normalized)

| Field | Type | Meaning | Temporal source mapping |
| --- | --- | --- | --- |
| `taskId` | string | Primary product handle | `workflowId` |
| `source` | string | Execution substrate | literal `"temporal"` |
| `entry` | string | Product entry shape | `searchAttributes.mm_entry` or derived from `workflowType` |
| `title` | string | Primary human label | `memo.title` |
| `summary` | string \| null | Secondary status text | `memo.summary` |
| `status` | string | Dashboard-normalized status | mapped from Temporal state model |
| `rawState` | string | Raw MoonMind workflow state | execution `state` |
| `temporalStatus` | string | Raw Temporal close/runtime status | execution `temporalStatus` |
| `workflowId` | string | Durable Temporal identity | execution `workflowId` |
| `workflowType` | string | Root workflow type | execution `workflowType` |
| `ownerId` | string \| null | Owning user | `searchAttributes.mm_owner_id` or projection owner |
| `createdAt` | datetime | Start time for task row sorting/display | execution `startedAt` |
| `updatedAt` | datetime | Most recent meaningful change | execution `updatedAt` |
| `closedAt` | datetime \| null | Terminal close time | execution `closedAt` |
| `artifactsCount` | integer | Optional row summary count | `len(artifactRefs)` |
| `detailHref` | string | Canonical detail route | `/tasks/{taskId}` |

Rules:

- `title` should always be present for Temporal-backed rows. If not explicitly set, the execution layer must provide a safe default.
- `summary` is allowed to be short and operational rather than user-marketing copy.
- `status` is the **dashboard display/filter status**. It does not replace `rawState` or `temporalStatus`.
- `createdAt` uses `startedAt` for Temporal-backed compatibility rows.

### 8.2 Task detail payload (normalized)

Task detail payloads for `source=temporal` should include the normalized row fields plus execution-specific detail fields.

Recommended required fields:

| Field | Meaning |
| --- | --- |
| `taskId` | Stable task handle; equals `workflowId` |
| `source` | `temporal` |
| `workflowId` | Canonical Temporal durable ID |
| `temporalRunId` | Current/latest run ID |
| `workflowType` | `MoonMind.Run` or `MoonMind.ManifestIngest` |
| `entry` | `run` or `manifest` |
| `title` | Display title |
| `summary` | Human-readable execution summary |
| `status` | Dashboard-normalized state |
| `rawState` | MoonMind execution state |
| `temporalStatus` | Temporal runtime/close view |
| `closeStatus` | Raw Temporal close status when present |
| `ownerId` | Owning user |
| `createdAt` | Start time |
| `updatedAt` | Last meaningful update |
| `closedAt` | Close time when terminal |
| `artifactRefs` | Linked execution artifacts |
| `searchAttributes` | Canonical indexed execution metadata |
| `memo` | Canonical display/debug metadata |

Recommended optional detail fields:

- `inputArtifactRef`
- `planArtifactRef`
- `manifestArtifactRef`
- `actions` block describing currently enabled controls
- `debug` block with `namespace`, `workflowType`, `workflowId`, `temporalRunId`, and close information

Rules:

- detail pages may show `temporalRunId`, but must still anchor the route to `taskId == workflowId`
- `searchAttributes` and `memo` should be available to operators even if the default UI renders only selected fields
- execution `parameters` should not be blindly surfaced; adapters should expose only reviewed, task-safe fields

### 8.3 Example normalized Temporal-backed row

```json
{
  "taskId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
  "source": "temporal",
  "entry": "run",
  "title": "Repo update run",
  "summary": "Execution resumed.",
  "status": "running",
  "rawState": "executing",
  "temporalStatus": "running",
  "workflowId": "mm:01JNX7SYH6A3K1V8Q2D7E9F4AB",
  "workflowType": "MoonMind.Run",
  "ownerId": "0f2d5802-0bd2-4d31-a618-6f7d3b0f09da",
  "createdAt": "2026-03-06T08:15:21Z",
  "updatedAt": "2026-03-06T08:18:04Z",
  "closedAt": null,
  "artifactsCount": 2,
  "detailHref": "/tasks/mm:01JNX7SYH6A3K1V8Q2D7E9F4AB"
}
```

---

## 9. Status compatibility model

### 9.1 Dashboard-normalized task statuses

The current dashboard status family remains:

- `queued`
- `running`
- `awaiting_action`
- `succeeded`
- `failed`
- `cancelled`

### 9.2 Temporal state to dashboard status mapping

| Temporal-side value | Normalized task status | Notes |
| --- | --- | --- |
| `initializing` | `queued` | Workflow exists but has not reached meaningful work yet |
| `planning` | `running` | Active execution work, not queue wait |
| `executing` | `running` | Active execution |
| `awaiting_external` | `awaiting_action` | Compatibility collapse for approval/pause/external wait |
| `finalizing` | `running` | Still active until close |
| `succeeded` | `succeeded` | Terminal success |
| `failed` | `failed` | Terminal failure |
| `canceled` | `cancelled` | UI spelling remains `cancelled` |

### 9.3 Raw values that must remain available

The adapter must preserve:

- `rawState` = MoonMind workflow state (`initializing`, `planning`, etc.)
- `temporalStatus` = `running`, `completed`, `failed`, or `canceled`
- `closeStatus` when present

### 9.4 Important compatibility note

`awaiting_external` is broader than human approval.

For the current dashboard contract, it is acceptable to normalize this to `awaiting_action`, but the detail page should retain enough context in summary/debug fields to distinguish:

- waiting on approval
- paused by operator
- waiting on external callback
- waiting on integration completion

If the UI needs sharper distinction later, add an explicit `waitKind` field rather than overloading `status`.

---

## 10. Action mapping

Task-facing operations should be translated into Temporal-native controls by compatibility adapters.

### 10.1 Action translation table

| Task-facing action | Temporal action | Current adapter endpoint |
| --- | --- | --- |
| Create task | Start workflow execution | `POST /api/executions` |
| Edit task inputs | Update `UpdateInputs` | `POST /api/executions/{workflowId}/update` |
| Rename task | Update `SetTitle` | `POST /api/executions/{workflowId}/update` |
| Rerun task | Update `RequestRerun` | `POST /api/executions/{workflowId}/update` |
| Approve task | Signal `Approve` | `POST /api/executions/{workflowId}/signal` |
| Pause task | Signal `Pause` | `POST /api/executions/{workflowId}/signal` |
| Resume task | Signal `Resume` | `POST /api/executions/{workflowId}/signal` |
| Deliver callback / webhook event | Signal `ExternalEvent` | `POST /api/executions/{workflowId}/signal` |
| Cancel task | Cancel workflow | `POST /api/executions/{workflowId}/cancel` |

### 10.2 Update result semantics

Temporal-backed edit/rerun operations may return these compatibility outcomes:

- `immediate`
- `next_safe_point`
- `continue_as_new`

Rules:

- task UI must surface the **accepted/applied/message** result instead of pretending every edit happened immediately
- rerun is allowed to keep the same `taskId` while changing `temporalRunId`
- major reconfiguration may legitimately choose `continue_as_new`

### 10.3 Approval and callback rules

- Approval remains a **MoonMind product policy** decision, even when the transport is a Temporal Signal or Update.
- External callback delivery should use named product semantics in compatibility layers, not raw Signal names in user-facing UX.
- Compatibility layers should validate ownership and authorization before routing to Temporal controls.

### 10.4 Cancellation rules

- Normal user-facing cancellation should default to **graceful** cancellation.
- Forced termination behavior should be treated as an operator/admin path, not the default task UI action.
- Terminal executions should render cancellation as unavailable or as a no-op with explicit messaging.

---

## 11. Pagination, sorting, and count behavior

### 11.1 Temporal-only task queries

For views that are purely Temporal-backed:

- sorting should default to `updatedAt DESC`
- the adapter may pass through Temporal execution pagination semantics directly
- `nextPageToken` may remain Temporal-specific
- exact counts are allowed when cheaply available

### 11.2 Mixed-source task queries

For unified multi-source task lists:

- the compatibility layer must own the merged pagination contract
- do **not** leak a raw Temporal `nextPageToken` as the universal cursor for a mixed-source list
- global sorting should remain consistent across sources, typically by `updatedAt DESC`
- aggregated counts may be:
  - exact, when all participating sources can provide stable exact counts cheaply, or
  - `estimated_or_unknown` / omitted when exact aggregation would be misleading or expensive

### 11.3 Count mode contract

Where counts are returned, adapters should expose:

- `count`
- `countMode`

Allowed `countMode` values:

- `exact`
- `estimated_or_unknown`

Rules:

- `countMode=exact` is allowed for current `/api/executions` responses
- unified `/tasks/*` compatibility views must not claim exact counts unless they truly have them

---

## 12. Data ownership and source of truth

### 12.1 Near-term implementation posture

Current implementation may use a `TemporalExecutionRecord` projection and a lifecycle service facade to power compatibility APIs.

That is acceptable during migration.

### 12.2 Long-term truth model

For Temporal-managed work, the intended truth model is:

1. **Temporal workflow execution state/history** is authoritative for execution lifecycle
2. **Temporal visibility metadata** is authoritative for list/filter/query semantics
3. **MoonMind projection rows** may remain as read models, auth-index helpers, or compatibility caches

### 12.3 Required canonical metadata for Temporal-backed compatibility

Adapters should treat these fields as canonical for Temporal-backed rows:

#### Search Attributes

- `mm_owner_id`
- `mm_state`
- `mm_updated_at`
- `mm_entry`
- optional bounded fields such as `mm_repo` or `mm_integration`

#### Memo

- `title`
- `summary`
- optional safe refs such as `input_ref` or `manifest_ref`

Rules:

- compatibility adapters should prefer canonical execution metadata over inventing source-specific shadow fields
- projection tables must not drift from the documented execution lifecycle contract

---

## 13. UI integration contract

### 13.1 Dashboard source addition

The dashboard runtime source model should gain a `temporal` execution source for task list/detail behavior.

This does **not** require proposal or schedule pages to join the same source taxonomy.

### 13.2 Canonical routes

Preferred product routes remain:

- `/tasks/list`
- `/tasks/{taskId}`

Rules:

- Temporal-backed task details should render inside the same unified task shell used for other task sources
- the canonical task detail handle for Temporal-backed work is `taskId == workflowId`
- introducing `/tasks/temporal/{workflowId}` is optional compatibility sugar, not a requirement

### 13.3 Manifest pages

Manifest experiences may stay on their existing routes while the product is in transition.

When Temporal-backed manifest flows join the task surface, they should prefer:

- `source=temporal`
- `entry=manifest`

instead of introducing a second execution source taxonomy.

### 13.4 Raw Temporal detail exposure

The standard task UI should not force every user to think in Temporal terms.

However, operator/debug views should be able to display:

- `workflowId`
- `temporalRunId`
- `workflowType`
- `closeStatus`
- selected search attributes and memo fields

---

## 14. API layering and public surface posture

### 14.1 Product-facing posture during migration

Active product flows may continue to use:

- `/tasks/*`
- active orchestrator compatibility routes where still required

### 14.2 Execution adapter posture

`/api/executions` should be treated as the Temporal execution lifecycle adapter surface during migration.

It is allowed to serve three roles:

- backend integration surface for compatibility adapters
- dashboard/operator surface for Temporal-backed controls
- eventual candidate for a more direct public execution API later

### 14.3 Naming rule

The existence of `/api/executions` does **not** mean the product must immediately rename all task concepts to execution concepts.

Public naming can lag runtime naming while compatibility remains explicit.

---

## 15. Migration checkpoints

### Phase A — adapter-ready

- Temporal-backed executions can be created, listed, described, edited, signaled, and canceled
- compatibility payload shape is documented and stable
- `taskId == workflowId` rule is in place for Temporal-backed rows

### Phase B — dashboard integration

- dashboard recognizes `source=temporal`
- Temporal-backed rows render in `/tasks/list`
- `/tasks/{taskId}` can resolve Temporal-backed details without inventing a second detail shell
- normalized status mapping is wired into the current dashboard status family

### Phase C — parity hardening

- edit, rerun, cancel, approve, pause, resume, and callback flows work end-to-end through task-oriented controls
- Continue-As-New does not break task detail routes
- exact vs estimated count behavior is explicit and truthful

### Phase D — compatibility retirement decisions

Only after parity and adoption are proven:

- decide whether `/api/executions` becomes more directly public
- decide whether any legacy `runId` compatibility is still needed
- decide whether multi-source `/tasks/list` remains permanent or becomes Temporal-first

---

## 16. Acceptance criteria for this model

This document is considered successfully implemented for a Temporal-backed flow when all of the following are true:

1. a Temporal-backed execution can appear in the current task list without pretending to be a queue lease
2. `/tasks/{taskId}` remains stable across rerun / Continue-As-New
3. task edit and rerun controls surface `accepted/applied/message` semantics honestly
4. dashboard filters and sorting can include Temporal-backed rows without leaking raw Temporal cursor semantics into mixed-source pagination
5. operators can see raw execution identifiers and status data when needed
6. the compatibility layer does not overload legacy orchestrator `runId` to mean Temporal run ID

---

## 17. Open decisions to lock next

1. Whether the task dashboard should eventually expose run history for a single `workflowId` or only the latest run by default
2. Whether `awaiting_external` needs a first-class `waitKind` field sooner rather than later
3. Whether Temporal-backed manifest views become simple task filters or keep a distinct route shell longer
4. When `/api/executions` should graduate from adapter-first surface to a more openly documented public API
5. How much execution debug metadata should be shown by default versus behind an operator panel

---

## 18. Summary

MoonMind needs a **clear compatibility layer**, not an accidental one.

The core rule is simple:

- the **product** can continue to speak in terms of **tasks**
- the **runtime** can move to **Temporal workflow executions**
- the bridge between them must keep identifiers, status mapping, action mapping, and pagination semantics explicit

For Temporal-backed work during migration:

- `taskId == workflowId`
- `temporalRunId` is detail/debug only
- `source=temporal`
- `entry` distinguishes `run` vs `manifest`
- task actions map to Temporal start/update/signal/cancel primitives through compatibility adapters

That is the compatibility model that lets MoonMind evolve the execution substrate without forcing a premature product-language rewrite.
