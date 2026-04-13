# Contract: Agent Session Deployment Safety

## Scope

This contract covers the production Codex managed session runtime path. It defines the workflow mutation surface, activity/runtime side-effect expectations, reconciliation behavior, observability limits, and deployment gates required to satisfy the feature spec.

Out of scope:

- Delayed standalone managed-session image delivery.
- Generic runtime marketplace behavior.
- Claude or Gemini managed session parity.
- Provider-credential live verification as a required gate.

## Workflow Public Surface

### Updates

Production mutating controls are request/response-visible workflow Updates.

| Update | Purpose | Required preconditions |
|--------|---------|------------------------|
| `SendFollowUp` or `SendTurn` | Submit a new turn to the active managed session | Runtime handles attached; matching epoch unless intentionally starting/resuming |
| `SteerTurn` | Steer an active turn through runtime support | Runtime handles attached; matching epoch; active turn present; runtime supports steering |
| `InterruptTurn` | Interrupt an active turn through runtime support | Runtime handles attached; matching epoch; active turn present |
| `ClearSession` | Reset the session thread while preserving the logical session | Runtime handles attached; matching epoch; not already clearing; not terminating |
| `CancelSession` | Stop active work without destroying the session | Matching epoch; not terminating; active work exists or duplicate/no-op state is deterministic |
| `TerminateSession` | Destroy/finalize runtime and supervision state, then complete workflow | Runtime handles attached or deterministic already-finalized state; matching epoch; not already terminal except duplicate/no-op handling |

### Signals

Signals are reserved for fire-and-forget state propagation and attachment events. `attach_runtime_handles` may be signal-based because it updates locator state from launch/runtime setup and is not an operator mutation with a control result.

### Queries

`get_status` returns a bounded `SessionSnapshot` view. It may expose compact identity, epoch, status, degradation state, active turn identifier, and artifact refs. It must not expose prompts, transcripts, scrollback, raw logs, credentials, secrets, or unbounded provider output.

### Legacy bridge behavior

A generic `control_action` signal is not the production mutation contract. If present for replay or an intermediate cutover, it must be explicitly scoped as a replay/version bridge, covered by replay tests, and assigned a removal condition.

## Workflow Validation Rules

| Condition | Required behavior |
|-----------|-------------------|
| Missing runtime handles for runtime-bound control | Wait deterministically if the update was accepted before attachment and the action is allowed to wait; otherwise reject before mutation |
| Stale `session_epoch` | Reject before mutation |
| Turn-specific control with no active turn | Reject before mutation |
| Clear while already clearing | Reject or deterministic duplicate handling before second mutation |
| Mutator after termination begins | Reject, except duplicate terminate may return the prior terminal outcome |
| Unsupported runtime steering | Return explicit permanent failure rather than transient retry |
| Duplicate idempotent request | Return previous result or deterministic no-op without repeating unsafe side effects |

## Runtime Activity Contract

Runtime activities and controller calls perform side effects. Workflow code must not directly manipulate containers, filesystem runtime cache, or external runtime processes.

Required side-effect operations:

- Launch or resume managed session runtime.
- Send/follow-up turn.
- Steer active turn.
- Interrupt active turn.
- Clear session and increment epoch/thread continuity.
- Cancel active work without destroying the session.
- Terminate session by cleaning runtime container/process state, finalizing supervision, publishing terminal refs, and updating the operational recovery record.
- Reconcile stale/degraded/orphaned runtime state.

Activity requirements:

- Activities are safe under at-least-once execution.
- Stable idempotency keys or state-derived dedupe are used where external side effects can repeat.
- Permanent invalid-state or unsupported-runtime failures are explicit non-retryable application errors.
- Meaningfully blocking operations heartbeat often enough for cancellation to be delivered.
- Activity summaries are compact and operator-readable.

## Clear Session Invariants

After a successful clear:

- `session_id` remains unchanged.
- `container_id` remains unchanged unless runtime recovery forces a documented degraded/recovered state.
- `session_epoch` increments.
- `thread_id` changes to the new continuity scope.
- `active_turn_id` is cleared.
- Reset/control artifacts are published through the managed session controller/supervisor path.
- Query state and bounded metadata reflect the new epoch.

## Cancel vs Terminate

### CancelSession

Cancel stops active turn or in-flight work and returns the session to idle or degraded recoverable state. It does not remove the container as its primary purpose and must preserve the intended session continuity.

### TerminateSession

Terminate removes or finalizes runtime container/process state, finalizes supervision, records terminal operational state, updates bounded workflow/operator metadata, waits for cleanup completion, and only then allows the session workflow to complete.

## Continue-As-New Contract

Continue-As-New is triggered from the workflow main run path when policy or Temporal suggestion requires it.

Carry forward:

- Binding/session identity.
- Current epoch.
- Runtime locator.
- Last control action and compact reason.
- Latest continuity refs.
- Degradation status.
- Request-tracking or dedupe state needed to avoid repeating external side effects.

Before handoff:

- Accepted async handlers must finish.
- No unbounded prompt/log/transcript content may be included.
- Replay tests must cover the payload shape when it changes.

## Observability Contract

Allowed bounded surfaces:

- Workflow static summary/details.
- Workflow current details.
- Search Attributes: `TaskRunId`, `RuntimeId`, `SessionId`, `SessionEpoch`, `SessionStatus`, `IsDegraded`.
- Activity summaries.
- Reconcile schedule metadata.
- Logs, metrics, and traces using compact correlation identifiers.

Forbidden in all bounded surfaces:

- Prompts.
- Transcripts.
- Terminal scrollback.
- Raw logs.
- Credentials.
- Secrets.
- Unbounded provider output.

## Reconcile Contract

Recurring reconcile must inspect managed-session records and runtime state for:

- Stale degraded sessions.
- Missing runtime containers.
- Orphaned runtime containers or state.
- Supervision drift.

Valid outcomes:

- Reattach supervision.
- Mark degraded.
- Finalize terminal sessions.
- Report orphaned runtime state with bounded diagnostics.
- Leave healthy records unchanged.

## Deployment Safety Contract

The following changes are workflow-shape or deployment-sensitive:

- Workflow handler name/signature changes.
- Update/signal/query payload changes.
- Continue-As-New payload changes.
- Persisted workflow state structure changes.
- Cancel/terminate lifecycle semantic changes.
- Search Attribute or current-details schema changes.
- Patch insertion or removal.

Required gates:

- Worker Versioning, workflow patching, or explicit versioned cutover before incompatible rollout.
- Replay validation for representative open and closed managed-session histories.
- Fault-injected lifecycle validation for termination cleanup, cancel semantics, race/idempotency, and reconcile.
- Cutover playbooks for enabling steering, enabling Continue-As-New, changing cancel/terminate semantics, and introducing new visibility metadata.

## Validation Mapping

| Requirement area | Required validation |
|------------------|---------------------|
| Control API parity | Workflow-boundary tests for each typed update and invalid request |
| Runtime steering/interruption | Runtime/controller tests and workflow end-to-end state checks |
| Terminate cleanup | Tests proving container cleanup/finalization and no orphaned record |
| Cancel semantics | Tests proving cancel differs from terminate and preserves recoverable state |
| Idempotency/races | Duplicate request, stale epoch, early update, and shutdown-race tests |
| Continue-As-New | Shortened-history carry-forward tests |
| Observability safety | Bounded metadata/search attribute/summary assertions and forbidden-content scan |
| Reconcile | Scheduled/client and controller reconcile tests |
| Deployment safety | Replay tests plus Worker Versioning or cutover assertions |
