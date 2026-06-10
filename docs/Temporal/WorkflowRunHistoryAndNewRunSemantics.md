# MoonMind Workflow Run History and New Run Semantics

**Implementation tracking:** Rollout and backlog notes live in `docs/tmp/` or gitignored local-only paths (for example `artifacts/`), not as migration checklists in canonical `docs/`.

**Status:** Draft
**Owner:** MoonMind Platform
**Last updated:** 2026-06-10
**Audience:** backend, dashboard, workflow authors, API owners

## 1. Purpose

This document fixes the v1 semantics for:

- how MoonMind identifies a Temporal-managed Workflow Execution across multiple runs
- what the product and API mean by **run history**
- how `RequestRerun` (the live update name; renames to `RequestNewRun` in the hard switch) behaves for Temporal-backed executions
- how failed-step **Recover from Failed Step** differs from a full new run
- when MoonMind should use **Continue-As-New** versus starting a **fresh Workflow ID**
- how the workflow console references Temporal runs

This is a focused lifecycle document. It does **not** redefine the broader Temporal architecture, workflow catalog, or artifact model.

## 2. Related docs

- `docs/Temporal/WorkflowExecutionProductModel.md`
- `docs/Temporal/TemporalArchitecture.md`
- `docs/Temporal/TemporalPlatformFoundation.md`
- `docs/Temporal/WorkflowTypeCatalogAndLifecycle.md`
- `docs/Temporal/ActivityCatalogAndWorkerTopology.md`
- `docs/Temporal/SourceOfTruthAndProjectionModel.md`
- `docs/Temporal/VisibilityAndUiQueryModel.md`
- `docs/UI/WorkflowConsoleArchitecture.md`

## 3. Scope and non-goals

### 3.1 In scope

- Workflow ID vs run identity for Temporal-managed Workflow Executions
- detail-page anchoring for the workflow console
- `RequestRerun` semantics and lifecycle expectations
- failed-step recovery identity, checkpoint, and provenance expectations
- automatic Continue-As-New triggers for history control
- current projection/API behavior for “latest run” versus historical runs

### 3.2 Out of scope

- Designing a full Temporal-native operator UI
- Defining every Search Attribute and list filter (covered elsewhere)
- Building an immutable per-run audit/read model in this document

## 4. Current repo-aligned baseline

MoonMind’s current Temporal execution layer behaves as a **logical execution** keyed by `workflowId` with an in-place projection row for the latest run state.

Current implementation characteristics:

- `TemporalExecutionRecord` is keyed by `workflow_id` and stores the current `run_id`, workflow type, state, memo, search attributes, counters, and lifecycle timestamps.
- `RequestRerun` currently performs **Continue-As-New** semantics by preserving `workflow_id`, generating a new `run_id`, incrementing the current local Continue-As-New counter (`rerun_count`), resetting run-local counters/flags, and moving the execution back into an active state.
- failed-step recovery is not part of the generic `RequestRerun` contract; it requires separate checkpoint and provenance semantics and uses a dedicated command surface.
- other Continue-As-New paths also exist today for lifecycle rollover and major reconfiguration, and they currently increment that same local counter
- `/api/executions/{workflow_id}` returns the **current/latest** run view for that logical execution.
- the current projection keeps `started_at` as the logical execution start timestamp; it does not create a second primary row or a separate current-run start field on Continue-As-New
- There is no separate first-class MoonMind API or projection table yet that exposes a full immutable run-history list for a single Workflow ID.

This document makes those semantics explicit and treats them as the v1 default unless a later doc intentionally changes them.

## 5. Canonical identifiers

### 5.1 Workflow ID

`workflowId` is the canonical durable identifier for a Temporal-managed Workflow Execution and the product route key.

Rules:

- A single workflow detail experience maps to one logical `workflowId`.
- Continue-As-New preserves the same `workflowId`.
- Links, bookmarks, workflow detail routes, and adapters should prefer the logical execution identity over a specific run instance.

### 5.2 Run ID

`runId` identifies a **specific run instance** of a Workflow Execution.

Rules:

- `runId` may change whenever Continue-As-New occurs.
- `runId` is valid for debugging, support, correlation, and Temporal-native inspection.
- `runId` is **not** the primary product handle for Temporal-backed work.

### 5.3 Console routing identity

The workflow console routes detail pages by `workflowId`:

- `/workflows/{workflowId}` resolves to the logical execution, not to a specific historical run.
- Product flows must not require end users to understand Continue-As-New or manually switch run instances just to keep following the same Workflow Execution.

### 5.4 Naming caution

The repo currently has both:

- legacy/transitional **system run** concepts in older docs, and
- Temporal **run IDs** in the Temporal execution API model.

To avoid confusion:

- product-facing documentation should treat `workflowId` as the durable logical execution handle
- Temporal run instance IDs should be described as **run IDs for the current Temporal run**
- the canonical published field is `runId`; the `{temporalRunId}` token still appears in the live artifacts endpoint template (renames to `{runId}` in the hard switch)
- legacy `runId` naming from system-era contracts should not be reused to mean Temporal run ID in workflow-facing payloads

## 6. Run history model

### 6.1 v1 decision

For v1, MoonMind uses a **single detail page per Workflow ID** that always points to the **latest/current run** of that logical execution.

This is the primary decision this document locks.

Rationale:

- it matches the Workflow Execution product model
- it matches the current execution projection keyed by `workflowId`
- it keeps new-run behavior intuitive: the same Workflow Execution remains the same Workflow Execution after a new run

### 6.2 What “run history” means in v1

Run history exists conceptually, but it is **not yet a first-class MoonMind product surface**.

In v1:

- the workflow detail view shows the latest run state for a logical execution
- the current `runId` may be shown in a debug or metadata section
- artifact views for that detail page should use the latest `runId` resolved from execution detail rather than an older list-row snapshot
- `rerun_count` is internal lifecycle state and may be surfaced later, but in the current implementation it counts Continue-As-New transitions broadly, not only explicit user-requested new runs
- `startedAt` in the current projection is the logical execution start, not a guaranteed start time for the latest run instance
- immutable per-run event history remains a Temporal concern unless MoonMind later adds a dedicated run-history API or projection

### 6.3 What is not promised in v1

MoonMind does **not** promise the following yet for Temporal-backed executions:

- a browsable per-run history list in the main console
- immutable per-run snapshots in the application database
- user-facing route params that target an arbitrary historical run instance
- a guarantee that old run IDs remain first-class product routes

### 6.4 Future extension point

If operators later need a richer run-history experience, add it as a separate, explicit surface such as:

- a run-history drawer on execution detail
- a dedicated `/api/executions/{workflow_id}/runs` endpoint
- an ops-only Temporal inspection view

That future work should not change the v1 rule that the default user-facing detail route anchors on `workflowId`.

## 7. New run semantics

### 7.1 Meaning of `RequestRerun`

`RequestRerun` (the live update name; renames to `RequestNewRun` in the hard switch) means:

> Re-execute the same logical MoonMind Workflow Execution as a new Temporal run while preserving the same Workflow ID.

This is a **clean new run** of the same logical Workflow Execution, not the creation of a separate sibling execution.

`RequestRerun` is not the failed-step **Recover from Failed Step** action. Recovery means retrying the last failed step with prior completed work restored from durable checkpoints. That requires a distinct API/action surface because it imports execution progress rather than merely restarting the logical execution.

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
- update summary/memo to record that a new run was requested

### 7.3 Input changes allowed with a new run

A new-run request may also replace or patch execution inputs, including:

- `input_ref`
- `plan_ref`
- `parameters_patch`

Rules:

- replacing inputs as part of a new run is still considered the **same logical execution**
- input and plan refs that matter for audit/recovery should be stored as artifacts or artifact references
- a new-run request should remain idempotent when the caller supplies an idempotency key

### 7.4 State after a new run

After Continue-As-New for a requested new run:

- `MoonMind.Run` (renames to `MoonMind.UserWorkflow` in the hard switch) restarts in:
 - `planning` when no `plan_ref` is present
 - `executing` when a `plan_ref` is already available
- `MoonMind.ManifestIngest` restarts in `executing`

This state transition makes new-run behavior explicit for UI copy and downstream automation.

### 7.5 Terminal-state rule

A workflow in a terminal state should not accept ordinary updates.

Current repo-aligned note:

- the current `TemporalExecutionService` short-circuits updates once an execution is terminal **before** dispatching to `RequestRerun`
- that means new-run-from-terminal is **not yet implemented** as a special exception in the current lifecycle service
- the current API posture for that case is a non-applied update response, not a dedicated restart flow

Draft target rule:

- if MoonMind wants “new run for a closed execution” behavior, it should implement that behavior **explicitly**, either by exempting `RequestRerun` from the generic terminal-state gate or by adding a distinct restart command surface
- until that change is made, the safe assumption is: `RequestRerun` applies to non-terminal executions, while terminal executions require an explicit new workflow start or a future dedicated restart path

## 7A. Failed-step recovery semantics

### 7A.1 Meaning of Recover from Failed Step

**Recover from Failed Step** means:

> Start a linked follow-up Workflow Execution that reuses the original workflow input and the completed work before the last failed step, then retries that failed step.

Recovery is a product action, not a synonym for Continue-As-New and not a generic new run. It imports a bounded, durable progress checkpoint from a pinned source run. Plain “resume” is reserved for resuming paused workflows.

### 7A.2 Required behavior

For v1, failed-step recovery uses a distinct command surface:

```http
POST /api/executions/{workflow_id}/recover-from-failed-step
```

Behavior:

- require or resolve the source `workflowId`
- require or resolve and then pin the source `runId`
- require the source execution to be terminal failed, timed out, or otherwise explicitly recovery-eligible
- identify the last failed step from the source run's step ledger
- require an authoritative source workflow input snapshot
- require a source plan ref or digest when a plan exists
- require a recovery checkpoint that can restore completed prior work
- create a linked follow-up execution with its own `workflowId` and `runId` unless a future in-place continuation model is explicitly introduced
- leave the source execution unchanged
- record a relation from the recovered execution to the source execution with relationship type `recover_from_failed_step`

### 7A.3 Recovery input changes

Recovery does not allow workflow input changes in v1.

Rules:

- the recovered execution uses the original workflow input snapshot unchanged
- changing instructions, steps, attachments, runtime, publish mode, branch, presets, dependencies, or model settings requires an edited full retry instead
- the recovery request may carry operator metadata and an idempotency key, but it must not carry an edited workflow input payload
- the recovery request must pin the source `runId` so the restored progress cannot drift when the logical source execution later changes

### 7A.4 Recovery checkpoint requirements

Recovery must be backed by a durable checkpoint artifact or equivalent durable read model.

At minimum, the checkpoint must identify:

- source `workflowId`
- source `runId`
- source workflow input snapshot ref
- source plan ref or digest, when available
- failed logical step ID and attempt
- completed prior steps and their source attempts
- semantic output refs for completed prior steps
- prepared input refs reused by the recovered execution
- workspace, branch, commit, or equivalent state immediately before the failed step

Rules:

- a checkpoint is execution-state evidence, not authored workflow input
- a missing checkpoint means recovery is unavailable
- a corrupted, unauthorized, stale, or plan-mismatched checkpoint must fail before new step execution starts
- recovery must not silently degrade into a full new run

### 7A.5 Detail routing and related runs

The default workflow detail route remains anchored on `workflowId`. Because v1 recovery starts a linked follow-up execution, the source and recovered executions each have their own detail route.

Rules:

- source detail shows the recovered execution in Related runs
- recovered detail shows the source execution as the original failed run
- relationship labels should use `Recovered from failed step`
- preserved prior steps in the recovered detail view should be displayed as reused from the source run, not as freshly executed by the recovered run

## 8. Continue-As-New outside a requested new run

A user-requested new run is not the only reason to Continue-As-New.

Automatic Continue-As-New for history control or major reconfiguration is not the same product action as a user-requested new run, even though both preserve `workflowId` and rotate `runId`.

Client and documentation rules:

- do not label every Continue-As-New as a user-visible new run
- do not infer "the user requested a new run" from `rerun_count` alone
- preserve stable workflow detail routing across both requested new runs and automatic rollover

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

- start a new workflow rather than re-run the same Workflow Execution
- duplicate/copy a prior execution for side-by-side comparison
- change ownership or access semantics in a way that should not inherit the old execution identity
- intentionally separate retention, audit, or reporting lineage from the prior execution
- keep the previous logical execution closed while a new one proceeds independently
- recover a failed Workflow Execution from a failed step while keeping the original failed execution immutable

Rule of thumb:

- **same logical Workflow Execution** → `RequestRerun` / Continue-As-New / same `workflowId`
- **new logical Workflow Execution** → new workflow start / new `workflowId`
- **failed-step recovery** → linked follow-up execution with pinned source `workflowId` and `runId`, unless a future in-place continuation model is explicitly designed

## 10. UI and API contract

### 10.1 Detail routing

Default user-facing detail routes resolve to the logical execution:

- canonical anchor: `workflowId` at `/workflows/{workflowId}`
- not preferred as the route key: current Temporal `runId`

### 10.2 Detail rendering

The main detail page should present:

- Workflow Execution title
- current domain state and Temporal close status
- current/latest run progress summary
- current/latest run step ledger
- preserved-step provenance when viewing a recovered execution
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

List views should treat a Continue-As-New transition as the same logical execution row.

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
- terminal executions currently return an update response indicating the workflow no longer accepts updates; callers should not assume new-run-from-terminal exists yet
- failed-step recovery uses the dedicated command `POST /api/executions/{workflow_id}/recover-from-failed-step`, not `RequestRerun`
- callers should treat a changed `runId` as a normal outcome of a requested new run or lifecycle rollover

## 11. Projection and audit implications

Because MoonMind currently projects Temporal execution state as one row per `workflowId`:

- the application database is a **latest-run projection**, not a full run-history store
- historical run inspection belongs to Temporal history and any future dedicated run-history surface
- immutable input/reconfiguration evidence should be preserved through artifacts and summaries, not by assuming the projection row itself is a per-run ledger
- recovery checkpoint evidence should be preserved through artifacts or a dedicated durable read model keyed by source `workflowId`, source `runId`, logical step ID, and attempt

If MoonMind later needs immutable per-run read models, that should be introduced as a separate projection keyed by `workflowId` plus `runId`, not inferred from the current table.

## 12. Acceptance criteria

For Temporal-backed Workflow Executions: **`workflowId`** is the canonical detail identity and route key; **`RequestRerun`** is Continue-As-New for the same logical execution; failed-step **Recover from Failed Step** is a separate linked follow-up execution that pins source `workflowId` and `runId` and restores progress from durable checkpoints; detail views follow the **latest run** for a logical execution; requested vs automatic Continue-As-New is distinguished; v1 **does not** require a full per-run history product surface (that may come later).

## 13. Related documents and backlog

Aligns with `SourceOfTruthAndProjectionModel`, `VisibilityAndUiQueryModel`, `WorkflowExecutionProductModel`, and `WorkflowConsoleArchitecture`. Optional follow-ups (new-run counters in UI, ops-only history endpoints, URL stability under CAN, tests) are tracked in local-only backlog notes.

## 14. Summary

MoonMind should treat a requested new run as a **new run of the same logical Workflow Execution**, not as a new product identity.

That means:

- the logical handle is `workflowId`
- the default detail experience follows the latest run for that workflow
- `RequestRerun` uses Continue-As-New in v1
- failed-step recovery is separate from `RequestRerun` and requires durable progress checkpoints
- automatic Continue-As-New rollover preserves the same logical execution but should not be conflated with a user-requested new run
- full per-run history is a future explicit feature, not an implied v1 guarantee
