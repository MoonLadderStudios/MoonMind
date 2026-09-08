# System Pause and Control Confirmations

Status: **Normative contract**
Owners: **MoonMind Engineering**
Last Updated: **2026-09-05**

## Authority and scope

Temporal owns durable workflow execution. The settings service owns the requested
admission state and control audit in `SettingsOverride` and `SettingsAuditEvent`.
An admission pause prevents new submissions through the guarded API. It does not
stop worker processes or prove that an active agent has stopped.

Drain mode changes admission state. Infrastructure owners separately perform
worker graceful shutdown when maintenance requires Activity claims to stop.
Quiesce mode also requests `Pause` Updates on running `MoonMind.UserWorkflow`
runs, which implement the generation-aware Update and `control_state` protocol.
Shared-queue operator and manifest workflows are excluded from enumeration.
Resume requests `Resume` Updates. Targets are pinned to the actual workflow ID
and run ID returned by Temporal Visibility; the request has one stable Update ID
per target and action.

## Durable confirmation

`SystemOperationsService` commits intent before making Temporal calls. The audit
stores enumeration completion and each target's `requested`, `accepted`,
`pending`, `safe_point`, `resumed`, `failed`, `unknown`, `already_terminal`,
`unsupported`, or `superseded` evidence. Each observed
transition is committed before that target proceeds. Up to ten targets reconcile
concurrently; progress callbacks serialize state changes and commits on the
service session. A persistence failure cancels and joins the remaining dispatch
workers before committed evidence is reloaded. Concurrent observers lock the audit
row, reuse the stable per-target Update ID, and merge observations without overwriting confirmed target outcomes
(`safe_point`, `resumed`, `failed`, `already_terminal`, `unsupported`, `superseded`).
A failed audit write reloads committed evidence before any result is displayed. A new service instance can continue
that request on a snapshot read or an idempotent resubmission. The current
request is selected by its persisted request ID, independently of timestamp
resolution. The state authority serializes idempotency checks, generation allocation,
and audit insertion, including simultaneous first commands. Duplicate keys with
different commands are rejected.

Update acceptance only establishes `accepted`. Completion is followed by a
`control_state` query of the same pinned run. `MoonMind.UserWorkflow` confirms a pause
only while suspended at its safe boundary, with no active agent child and no
Pause/Resume transition in progress. Child forwarding failures retain rollback
behavior. A failed or absent lookup remains `unknown` unless positive evidence
establishes the disposition: a confirmed closed run is `already_terminal`
(which satisfies the scoped operation but never proves physical host cleanup or
lease release), an unimplemented Update/query is `unsupported`, and a run-ID or
generation mismatch is `superseded` (Continue-As-New, reset, or a newer command
replaced the pinned request). `already_terminal` satisfies the scoped operation;
`failed`, `unknown`, `unsupported`, and `superseded` require operator attention.
Other targets' successful confirmations are retained.

## Bounded paged enumeration

Enumeration is a paged Visibility selection, never an atomic system snapshot.
The batch records its `selection_policy` (Visibility query, page size, and
not-atomic disclaimer), an `enumeration_cursor` counting consumed Visibility
entries, and a bounded page size (100) with a total budget (1000). Targets are
deduplicated by pinned `(workflow_id, run_id)`; each page persists safe progress
before the next page is consumed. A later-page failure checkpoints the partial
target list, cursor, and `enumeration_error`; a retry resumes from the
checkpoint instead of losing the page. Incomplete enumeration (`enumerated=false`
or a set `enumeration_error`) cannot produce a fully confirmed result: batch
`status` degrades to `pending`/`unknown`, never `succeeded`.

An enumerated empty target list reports `empty`, not `succeeded`. The dashboard
explains that no eligible runs were found rather than inferring every
worker/host or excluded workflow is quiescent. Blocked enumeration is displayed
with its error code and bounded coverage disclaimer.

## Run transitions, newer commands, and observer behavior

Targets stay pinned to the exact `(workflow_id, run_id, update_id)`. A
Continue-As-New, reset, or later execution sharing the workflow ID never inherits
an old control request: its `control_state.runId` mismatch is recorded as
`superseded` through the existing control/admission policy, and only a new
request/generation may cover the successor. Enumerated target sets are immutable
once `enumerated=true`; older observations cannot overwrite the current requested
state, and a stale generation cannot report a newer generation confirmed.
Historical operation results remain in the append-only audit list separately from
the current state's control payload.

Snapshot reads reconcile only the already authorized stored command for the
current state's request ID; terminal batches (`succeeded`, `failed`, `empty`)
and superseded generations perform no Temporal work on read. Simultaneous readers
share stable Update IDs and the audit row lock, so duplicate effects are not
issued. Closing the page never cancels committed intent; after an API failure,
restart, browser disconnect, or lost Update/audit acknowledgment, progress
resumes on the next read or idempotent resubmission of the same recorded request.
No autonomous always-on reconciler is introduced; if one becomes required it must
attach to an existing durable reconciler/schedule.

System requests carry an increasing generation from the persisted state version.
A workflow ignores an older generation and confirms the requested generation in
its query. Old no-argument Updates remain consumable by histories. Historical
acceptance counts cannot be upgraded to safe-point evidence.

## API and dashboard

`GET /api/system/worker-pause` returns requested state, drain metrics, audit,
`signalStatus`, and the optional typed `control` batch. Reads also resume bounded
control reconciliation. `POST /api/system/worker-pause` accepts `action`, `reason`,
`idempotencyKey`, and pause `mode` (`drain` or `quiesce`). Pause requires
`confirmation`; forced resume also requires confirmation.

The dashboard displays **Workers quiesced** only when enumeration finished and
every target confirmed `safe_point` (or `already_terminal` for runs that closed
before observation). An enumerated empty scope displays **Admission paused; no
eligible running workflows found**, and blocked enumeration displays
**Admission paused; enumeration incomplete** with the error code. Pending and partial confirmations remain
visible, with per-target state available in an expandable list. The scope note
states the guarantee covers guarded API admission plus enumerated running
UserWorkflow safe points only, excluding shared-queue operator/manifest
workflows; a bounded snapshot never proves every machine process stopped. Resume admission
and confirmed resumed workflows are separate evidence. Resume remains available
until every target confirms resumption, so failed or partial batches can be retried
with a fresh request and generation. Drain metrics report
unavailable when Temporal counts cannot be obtained.

These endpoints enforce `operations.read` and `operations.invoke` permissions.
Control evidence contains compact identities and reason codes, not provider
credentials or raw exception messages.

## Operation-result durability and related contracts

Update acceptance only establishes `accepted`: request, acceptance,
safe-point completion, and unavailable/partial outcomes are distinct, and
acceptance counts must not be upgraded into safe-point evidence.

- Durable per-target operation-result tracking (retries across restarts,
  long-lived outcome records beyond the audit row) is implemented as bounded
  paged enumeration with checkpointed cursors, explicit
  `already_terminal`/`unsupported`/`superseded` dispositions, and terminal-batch
  read bounds described above:
  enumeration covers only running `MoonMind.UserWorkflow` executions, server
  Batch Operations are not used, and Update acceptance never proves a safe
  paused state.
- The per-workflow-type Pause/Resume/Query protocol table lives in
  `WorkflowTypeCatalogAndLifecycle.md` §6.2.
- Queue/worker inventory derivation lives in
  `ActivityCatalogAndWorkerTopology.md` §4 (generated catalogs in #3959).
