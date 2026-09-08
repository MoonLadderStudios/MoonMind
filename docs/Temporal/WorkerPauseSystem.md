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
`unsupported`, or `superseded` evidence. Each observed transition is committed
before that target proceeds. Up to ten targets reconcile concurrently; progress
callbacks serialize state changes and commits on the
service session. A persistence failure cancels and joins the remaining dispatch
workers before committed evidence is reloaded. Concurrent observers lock the audit
row and merge observations without overwriting terminal target outcomes
(`safe_point`, `resumed`, `failed`, `already_terminal`, `unsupported`,
`superseded`). A
failed audit write reloads committed evidence before any result is displayed. A new service instance can continue
that request on a snapshot read or an idempotent resubmission. The current
request is selected by its persisted request ID, independently of timestamp
resolution. The state authority serializes idempotency checks, generation allocation,
and audit insertion, including simultaneous first commands. Duplicate keys with
different commands are rejected.

Update acceptance only establishes `accepted`. Completion is followed by a
`control_state` query of the same pinned run. `MoonMind.UserWorkflow` confirms a pause
only while suspended at its safe boundary, with no active agent child and no
Pause/Resume transition in progress. Child forwarding failures retain rollback
behavior. An unavailable RPC or a failed/absent lookup leaves explicit
unknown/pending evidence unless positive evidence establishes a better
disposition. Other targets' successful confirmations are retained.

Target dispositions decode as follows:

- `already_terminal`: the pinned run observably closed (describe shows a
  non-running status). No further action is needed for the scoped operation,
  but this is never equated with verified physical host cleanup or lease
  release; per-target evidence keeps the distinction visible.
- `unsupported`: the run provably does not implement the control/query
  protocol (an explicit unknown/unregistered-query error), so no
  safe-point confirmation is possible. A generic NOT_FOUND can mean the
  pinned execution or namespace is transiently unavailable, so it stays
  retryable (`unknown`). Requires operator attention.
- `superseded`: the pinned run identity was replaced (Continue-As-New, reset,
  or a later execution sharing the workflow id reports a valid, nonblank,
  differing run id) or a newer control generation owns the run. Malformed or
  blank control evidence (non-object, missing run id) stays retryable
  (`unknown`); it never parks the target as superseded. The target keeps its pinned
  identity; a successor never silently inherits the old request. Requires
  operator attention under the current command.
- `unknown`/`pending`: query or Update transport unavailable. Requires retry
  or attention; never silently promoted.

`safe_point`/`resumed` plus `already_terminal` satisfy the scoped operation;
`failed`, `unknown`, `unsupported`, and `superseded` require attention.
Historical operation results live in their own per-command audit rows; a newer
generation never overwrites an older request's evidence, and older
observations never overwrite the current requested state.

## Bounded, resumable enumeration

Enumeration scans Temporal Visibility for running `MoonMind.UserWorkflow`
executions on the configured task queues. The scan is a point-in-time,
non-atomic selection recorded as `enumerationPolicy`; it is not an atomic
system snapshot. Discovery checkpoints partial target lists with an explicit
progress marker (`enumerationCursor`) every 100 targets, deduplicates against
already-persisted target identities, and resumes from persisted progress after
a restart or retry instead of repeating the full scan. A per-request budget of
1000 targets caps memory, response size, duration, and write volume; the budget
applies to the accumulated total, so a resumed pass already at the cap stays
truncated without growing the payload one run per read. Beyond the
budget the request stays unenumerated with `control_enumeration_truncated`.
A failed later page checkpoints its partial list with
`control_visibility_unavailable`. Incomplete enumeration (an
`enumerationError` is present) can never report a fully confirmed result.
An enumerated request with zero eligible targets reports `empty`, displayed as
"No eligible runs found", never as proof that every worker, host, or excluded
workflow is quiescent.

## Recovery and observer behavior

Progress requires a read (`GET` snapshot reconciliation) or an idempotent
resubmission of the same recorded request, reusing the stable per-target
Update ID before issuing another effect; there is no autonomous background
reconciler or always-on service. Terminal batches (`succeeded`, `empty`) do
not re-enter fan-out on reads. Closing the dashboard page is not cancellation
of the committed intent.

## Admission coverage and scope

`check_system_paused` (`TemporalExecutionService`, owned pause-state key
`operations.workers.pause_state`) is the API guard preventing new workflow
submissions while a pause is active. Work enumerated for quiesce is the
running-`UserWorkflow` point-in-time set; new concurrent work outside the
original enumeration follows the admission policy at submission time rather
than joining the old batch. Shared-queue operator and manifest workflows are
excluded and stay operational where intended. Agent teardown and blocked
enumeration surface as pending attention, not as confirmed quiescence.

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

The dashboard displays **Workers quiesced** only when enumeration finished with
at least one target and every target confirmed `safe_point` or
`already_terminal`. An enumerated
request with no eligible targets displays **No eligible runs found; admission
pause still applies** with an explicit scope note (running UserWorkflow
executions only; not proof of host-wide quiescence). Blocked enumeration
displays its error with confirmation pending. Pending and partial confirmations remain
visible, with per-target state available in an expandable list. Resume admission
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
  long-lived outcome records beyond the audit row) continues in #3953. This
  document describes today's fan-out and its partial/unknown limits:
  enumeration covers only running `MoonMind.UserWorkflow` executions, server
  Batch Operations are not used, and Update acceptance never proves a safe
  paused state.
- The per-workflow-type Pause/Resume/Query protocol table lives in
  `WorkflowTypeCatalogAndLifecycle.md` §6.2.
- Queue/worker inventory derivation lives in
  `ActivityCatalogAndWorkerTopology.md` §4 (generated catalogs in #3959).
