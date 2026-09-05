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
Quiesce mode also requests `Pause` Updates on running MoonMind workflow runs.
Resume requests `Resume` Updates. Targets are pinned to the actual workflow ID
and run ID returned by Temporal Visibility; the request has one stable Update ID
per target and action.

## Durable confirmation

`SystemOperationsService` commits intent before making Temporal calls. The audit
stores enumeration completion and each target's `requested`, `accepted`,
`pending`, `safe_point`, `resumed`, `failed`, or `unknown` evidence. Each observed
transition is committed before proceeding. Concurrent observers lock the audit
row and merge observations without overwriting confirmed target outcomes. A
failed audit write reloads committed evidence before any result is displayed. A new service instance can continue
that request on a snapshot read or an idempotent resubmission. The current
request is selected by its persisted request ID, independently of timestamp
resolution. Duplicate keys with different commands are rejected.

Update acceptance only establishes `accepted`. Completion is followed by a
`control_state` query of the same run. `MoonMind.UserWorkflow` confirms a pause
only while suspended at its safe boundary, with no active agent child and no
Pause/Resume transition in progress. Child forwarding failures retain rollback
behavior. An unsupported query, missing run, or unavailable RPC leaves explicit
unknown/pending evidence. Other targets' successful confirmations are retained.

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
every target confirmed `safe_point`. Pending and partial confirmations remain
visible, with per-target state available in an expandable list. Resume admission
and confirmed resumed workflows are separate evidence. Drain metrics report
unavailable when Temporal counts cannot be obtained.

These endpoints enforce `operations.read` and `operations.invoke` permissions.
Control evidence contains compact identities and reason codes, not provider
credentials or raw exception messages.
