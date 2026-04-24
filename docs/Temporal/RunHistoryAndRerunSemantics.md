# MoonMind Run History and Rerun Semantics

**Implementation tracking:** Rollout and backlog notes live in MoonSpec artifacts (`specs/<feature>/`), gitignored handoffs (for example `artifacts/`), or other local-only files—not as migration checklists in canonical `docs/`.

**Status:** Draft 
**Owner:** MoonMind Platform 
**Last updated:** 2026-04-04 
**Audience:** backend, dashboard, workflow authors, API owners

## 1. Purpose

This document fixes the v1 semantics for:

- how MoonMind identifies a Temporal-managed execution across multiple runs
- what the product and API mean by **run history**
- how `RequestRerun` behaves for Temporal-backed executions
- when MoonMind should use **Continue-As-New** versus starting a **fresh Workflow ID**
- how current task-oriented UI and compatibility routes should reference Temporal runs during migration

This is a focused lifecycle document. It does **not** redefine the broader Temporal architecture, workflow catalog, or artifact model.

## 2. Related docs

- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/Temporal/TaskExecutionCompatibilityModel.md`
- `docs/UI/MissionControlArchitecture.md`

## 3. Scope and non-goals

### 3.1 In scope

- Workflow ID vs run identity for Temporal-managed executions
- detail-page anchoring for task-oriented UI during migration
- `RequestRerun` semantics and lifecycle expectations
- automatic Continue-As-New triggers for history control
- current projection/API behavior for “latest run” versus historical runs

### 3.2 Out of scope

- Designing a full Temporal-native operator UI
- Replacing all existing `/tasks/*` or `/system/*` compatibility routes now
- Defining every Search Attribute and list filter (covered elsewhere)
- Building an immutable per-run audit/read model in this document

## 4. Current repo-aligned baseline

MoonMind’s current Temporal execution layer behaves as a **logical execution** keyed by `workflowId` with an in-place projection row for the latest run state.

Current implementation characteristics:

- `TemporalExecutionRecord` is keyed by `workflow_id` and stores the current `run_id`, workflow type, state, memo, search attributes, counters, and lifecycle timestamps.
- `RequestRerun` currently performs **Continue-As-New** semantics by preserving `workflow_id`, generating a new `run_id`, incrementing the current local Continue-As-New counter (`rerun_count`), resetting run-local counters/flags, and moving the execution back into an active state.
- other Continue-As-New paths also exist today for lifecycle rollover and major reconfiguration, and they currently increment that same local counter
- `/api/executions/{workflow_id}` returns the **current/latest** run view for that logical execution.
- the current projection keeps `started_at` as the logical execution start timestamp; it does not create a second primary row or a separate current-run start field on Continue-As-New
- There is no separate first-class MoonMind API or projection table yet that exposes a full immutable run-history list for a single Workflow ID.

This document makes those semantics explicit and treats them as the v1 default unless a later doc intentionally changes them.

## 5. Canonical identifiers

### 5.1 Workflow ID

`workflowId` is the canonical durable identifier for a Temporal-managed execution.

Rules:

- A single MoonMind task-oriented detail experience maps to one logical `workflowId`.
- Continue-As-New preserves the same `workflowId`.
- Links, bookmarks, task detail routes, and compatibility adapters should prefer the logical execution identity over a specific run instance.

### 5.2 Run ID

`runId` identifies a **specific run instance** of a Workflow Execution.

Rules:

- `runId` may change whenever Continue-As-New occurs.
- `runId` is valid for debugging, support, correlation, and Temporal-native inspection.
- `runId` is **not** the primary product handle for Temporal-backed work.

### 5.3 Task ID compatibility

During migration, user-facing routes may still be task-oriented.

Rules:

- `/tasks/:taskId` should resolve to the logical execution, not to a specific historical run.
- For Temporal-backed rows, `taskId` must equal `workflowId`.
- Product flows must not require end users to understand Continue-As-New or manually switch run instances just to keep following the same work item.

### 5.4 Naming caution during migration

The repo currently has both:

- legacy/transitional **system run** concepts in task/system docs, and
- Temporal **run IDs** in the Temporal execution API model.

To avoid confusion:

- product-facing documentation should treat `workflowId` as the durable logical execution handle
- Temporal run instance IDs should be described as **run IDs for the current Temporal run**
- if a compatibility surface needs explicit disambiguation later, it should use `temporalRunId` in external payloads or docs
- legacy `runId` naming from system-era contracts should not be reused to mean Temporal run ID in task-facing compatibility payloads

## 6. Run history model

### 6.1 v1 decision

For v1, MoonMind uses a **single detail page per Workflow ID** that always points to the **latest/current run** of that logical execution.

This is the primary decision this document locks.

Rationale:

- it matches the existing task-oriented product model
- it matches the current execution projection keyed by `workflowId`
- it avoids forcing the dashboard to become Temporal-native before the migration is complete
- it keeps rerun behavior intuitive: the “same task” remains the same task after rerun

### 6.2 What “run history” means in v1

Run history exists conceptually, but it is **not yet a first-class MoonMind product surface**.

In v1:

- the task/detail view shows the latest run state for a logical execution
- the current `runId` may be shown in a debug or metadata section
- artifact views for that detail page should use the latest `runId` resolved from execution detail rather than an older list-row snapshot
- `rerun_count` is internal lifecycle state and may be surfaced later, but in the current implementation it counts Continue-As-New transitions broadly, not only explicit user reruns
- `startedAt` in the current projection is the logical execution start, not a guaranteed start time for the latest run instance
- immutable per-run event history remains a Temporal concern unless MoonMind later adds a dedicated run-history API or projection

### 6.3 What is not promised in v1

MoonMind does **not** promise the following yet for Temporal-backed executions:

- a browsable per-run history list in the main dashboard
- immutable per-run snapshots in the application database
- user-facing route params that target an arbitrary historical run instance
- a guarantee that old run IDs remain first-class product routes

### 6.4 Future extension point

If operators later need a richer run-history experience, add it as a separate, explicit surface such as:

- a run-history drawer on execution detail
- a dedicated `/api/executions/{workflow_id}/runs` endpoint
- an ops-only Temporal inspection view

That future work should not change the v1 rule that the default user-facing detail route anchors on `workflowId`.

## 7. Rerun semantics

### 7.1 Meaning of `RequestRerun`

`RequestRerun` means:

> Re-execute the same logical MoonMind execution as a new Temporal run while preserving the same Workflow ID.

This is a **clean rerun** of the same logical work item, not the creation of a separate sibling task.

### 7.2 Required behavior

For v1, `RequestRerun` should use **Continue-As-New** semantics.

Behavior:

- preserve `workflowId`
- generate a new `runId`
- increment the current local Continue-As-New counter (`rerun_count`)
- clear terminal markers from the projection (`closed_at`, `close_status`)
- clear transient waiting/paused state
- reset run-local counters used for lifecycle thresholds
- retain logical execution metadata needed to continue the work
- update summary/memo to record that rerun occurred

### 7.3 Input changes allowed with rerun

A rerun request may also replace or patch execution inputs, including:

- `input_ref`
- `plan_ref`
- `parameters_patch`

Rules:

- replacing inputs as part of rerun is still considered the **same logical execution**
- input and plan refs that matter for audit/recovery should be stored as artifacts or artifact references
- a rerun request should remain idempotent when the caller supplies an idempotency key

### 7.4 State after rerun

After Continue-As-New for rerun:

- `MoonMind.Run` restarts in:
 - `planning` when no `plan_ref` is present
 - `executing` when a `plan_ref` is already available
- `MoonMind.ManifestIngest` restarts in `executing`

This state transition makes rerun behavior explicit for UI copy and downstream automation.

### 7.5 Terminal-state rule

A workflow in a terminal state should not accept ordinary updates.

Current repo-aligned note:

- the current `TemporalExecutionService` short-circuits updates once an execution is terminal **before** dispatching to `RequestRerun`
- that means rerun-from-terminal is **not yet implemented** as a special exception in the current lifecycle service
- the current API posture for that case is a non-applied update response, not a dedicated rerun restart flow

Draft target rule:

- if MoonMind wants “rerun closed execution” behavior for Temporal-backed tasks, it should implement that behavior **explicitly**, either by exempting `RequestRerun` from the generic terminal-state gate or by adding a distinct rerun/restart command surface
- until that change is made, the safe assumption is: `RequestRerun` applies to non-terminal executions, while terminal executions require an explicit new-start or a future dedicated rerun path

## 8. Continue-As-New outside manual rerun

Manual rerun is not the only reason to Continue-As-New.

Automatic Continue-As-New for history control or major reconfiguration is not the same product action as a user-requested rerun, even though both preserve `workflowId` and rotate `runId`.

Client and documentation rules:

- do not label every Continue-As-New as a user-visible "rerun"
- do not infer "the user clicked rerun" from `rerun_count` alone
- preserve stable task/detail routing across both manual rerun and automatic rollover

MoonMind should also Continue-As-New when:

- run history is growing beyond configured lifecycle thresholds
- a `MoonMind.Run` execution exceeds the configured step-count threshold
- a `MoonMind.ManifestIngest` execution exceeds the configured phase / wait-cycle threshold
- an input update is large enough to count as a major reconfiguration and is safer as a clean restart

Examples already aligned with the current service layer:

- replacing an existing `plan_ref` with a materially new plan
- an input patch that explicitly requests `request_continue_as_new=true`
- lifecycle progress updates that cross configured thresholds

## 9. When to start a fresh Workflow ID instead

`RequestRerun` is **not** the universal “start over” mechanism.

MoonMind should start a **fresh Workflow ID** instead when the user or system intends to create a **new logical execution**, such as:

- submit a new task rather than rerun the same task
- duplicate/copy a prior execution for side-by-side comparison
- change ownership or access semantics in a way that should not inherit the old execution identity
- intentionally separate retention, audit, or reporting lineage from the prior execution
- keep the previous logical execution closed while a new one proceeds independently

Rule of thumb:

- **same logical task** → `RequestRerun` / Continue-As-New / same `workflowId`
- **new logical task** → new workflow start / new `workflowId`

## 10. UI and API contract

### 10.1 Detail routing

Default user-facing detail routes should resolve to the logical execution:

- preferred anchor: `taskId == workflowId`
- compatibility payloads may return both `taskId` and `workflowId`, but they should carry the same durable value for Temporal-backed work
- not preferred as the route key: current Temporal `runId`

### 10.2 Detail rendering

The main detail page should present:

- task/execution title
- current domain state and Temporal close status
- current/latest run progress summary
- current/latest run step ledger
- workflow type / entry label
- artifact references and summaries
- current/latest `runId` in an advanced metadata or operator section

The main detail page should **not** require the user to choose among historical runs in v1.

Artifact implication:

- execution detail should fetch artifacts using the latest `runId` from the execution detail response
- the route stays anchored to `workflowId` even when the latest `runId` changes after Continue-As-New

Step implication:

- the default Steps panel shows the latest/current run only
- attempt counts are scoped to the current `runId`
- historical runs and cross-run step history remain future explicit surfaces

### 10.3 List rendering

List views should treat a Continue-As-New rerun as the same logical execution row.

Implications:

- the row identity remains stable
- the row may refresh with a new `runId`
- `updatedAt`, state, and summary reflect the latest run lifecycle
- the current projection's `startedAt` remains the logical execution start unless and until MoonMind adds a separate current-run start field
- list sorting remains based on logical execution recency, not per-run ancestry

### 10.4 Execution API posture

`/api/executions` is the Temporal execution lifecycle surface.

For this document’s purposes:

- `GET /api/executions/{workflow_id}` returns the latest/current materialized view of that execution
- `POST /api/executions/{workflow_id}/update` with `updateName="RequestRerun"` should return `applied="continue_as_new"` when accepted
- terminal executions currently return an update response indicating the workflow no longer accepts updates; callers should not assume rerun-from-terminal exists yet
- callers should treat a changed `runId` as a normal outcome of rerun or lifecycle rollover

## 11. Projection and audit implications

Because MoonMind currently projects Temporal execution state as one row per `workflowId`:

- the application database is a **latest-run projection**, not a full run-history store
- historical run inspection belongs to Temporal history and any future dedicated run-history surface
- immutable input/reconfiguration evidence should be preserved through artifacts and summaries, not by assuming the projection row itself is a per-run ledger

If MoonMind later needs immutable per-run read models, that should be introduced as a separate projection keyed by `workflowId` plus `runId`, not inferred from the current table.

## 12. Acceptance criteria

For Temporal-backed executions: **`workflowId`** is the canonical detail identity; **`taskId == workflowId`** where task-shaped IDs are used; **`RequestRerun`** is Continue-As-New for the same logical execution; detail views follow the **latest run**; manual vs automatic Continue-As-New is distinguished; v1 **does not** require a full per-run history product surface (that may come later).

## 13. Related documents and backlog

Aligns with `SourceOfTruthAndProjectionModel`, `VisibilityAndUiQueryModel`, `TaskExecutionCompatibilityModel`, and `MissionControlArchitecture`. Optional follow-ups (rerun counters in UI, ops-only history endpoints, URL stability under CAN, tests) are in .

## 14. Summary

MoonMind should treat rerun as a **new run of the same logical execution**, not as a new task identity.

That means:

- the logical handle is `workflowId`
- the default detail experience follows the latest run for that workflow
- `RequestRerun` uses Continue-As-New in v1
- automatic Continue-As-New rollover preserves the same logical execution but should not be conflated with a user-visible rerun
- full per-run history is a future explicit feature, not an implied v1 guarantee
