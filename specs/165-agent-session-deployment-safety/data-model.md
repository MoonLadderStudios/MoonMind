# Data Model: Agent Session Deployment Safety

## ManagedSessionBinding

Represents the durable identity that ties a task-scoped Codex managed session to Temporal and runtime supervision.

**Fields**:

- `task_run_id`: Bounded task run identifier.
- `runtime_id`: Runtime identifier for the managed Codex runtime.
- `workflow_id`: Temporal session workflow identifier.
- `session_id`: Logical managed session identifier.
- `session_epoch`: Monotonic epoch incremented when the session is cleared/reset.
- `execution_profile_ref`: Optional compact ref to execution profile metadata.

**Rules**:

- `session_id` remains stable across clear/reset.
- `session_epoch` must be present on mutating requests that need stale-request protection.
- Binding data can be carried across Continue-As-New.

## RuntimeLocator

Bounded handle set needed to address the active runtime instance.

**Fields**:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

**Rules**:

- Runtime-bound controls require attached locator data before side effects execute.
- `thread_id` can change on clear/reset.
- `active_turn_id` is required for turn-specific controls such as steer and interrupt.
- Locator values are compact identifiers and may appear in bounded metadata when safe.

## ControlRequest

Represents a production session mutation.

**Actions**:

- `start_session`
- `resume_session`
- `send_turn`
- `steer_turn`
- `interrupt_turn`
- `clear_session`
- `cancel_session`
- `terminate_session`

**Fields**:

- `request_id`: Stable idempotency key where supplied or derived.
- `action`: Canonical control action.
- `session_epoch`: Expected epoch for stale-request rejection.
- `reason`: Optional compact operator/system reason.
- `turn_id`: Required for turn-specific controls when not inferable from state.
- `message_ref` or compact prompt metadata: Ref only; raw prompt content must not enter workflow visibility.

**Rules**:

- Invalid requests are rejected before workflow state mutation.
- Duplicate requests resolve to the same intended effect or a deterministic no-op.
- Mutators are rejected after termination begins.

## SessionSnapshot

Compact query and carry-forward view of the workflow state.

**Fields**:

- `binding`: `ManagedSessionBinding`
- `locator`: Current `RuntimeLocator` when attached.
- `status`: Current bounded status.
- `is_degraded`: Boolean degradation flag.
- `last_control_action`
- `last_control_reason`
- `continuity_refs`: Latest summary/checkpoint/control/reset/artifact refs.
- `request_tracking_state`: Bounded dedupe or idempotency state.
- `termination_requested`: Boolean terminal-progress flag.

**Forbidden content**:

- Prompts
- Transcripts
- Terminal scrollback
- Raw logs
- Credentials or secrets
- Unbounded provider output

## OperationalRecoveryRecord

Managed session store record used by the controller and recurring reconcile.

**Fields**:

- Session and task binding identifiers.
- Known container and runtime locator identifiers.
- Supervision status and timestamps.
- Latest bounded artifact refs.
- Degradation and recovery markers.

**Rules**:

- This record is the operational recovery index, not the operator/audit truth.
- Reconcile may reattach, finalize, mark degraded, or report bounded recovery outcomes from this record.
- Missing or stale runtime state must not cause unbounded provider data to be copied into the record.

## ControlArtifactSet

Artifact refs produced through the managed session controller/supervisor path.

**Fields**:

- `summary_ref`
- `checkpoint_ref`
- `control_ref`
- `reset_ref`
- `diagnostics_ref`

**Rules**:

- Artifacts plus bounded workflow metadata form the operator/audit truth.
- Container-local artifact helpers are fallback-only and must not be the production publisher path.
- Workflow metadata stores refs, not artifact contents.

## ContinueAsNewPayload

Bounded state carried from one session workflow run to the next.

**Fields**:

- Managed session binding.
- Current session epoch.
- Current runtime locator.
- Last control action and compact reason.
- Latest continuity refs.
- Request tracking or dedupe state needed to avoid duplicate side effects.
- Degradation status.

**Rules**:

- Continue-As-New is initiated from the main workflow execution path.
- Accepted async handlers must finish before handoff.
- Payload must remain compact and replay-safe.

## ReplayDeploymentGate

Validation evidence required before deploying workflow-shape changes.

**Fields**:

- `change_class`: Handler shape, payload shape, state structure, visibility/search attribute, or lifecycle semantic change.
- `replay_safe_rollout_required`: Boolean.
- `patch_ids`: Scoped patch identifiers when a bridge is needed.
- `history_set`: Representative histories used for replay.
- `replay_result`: Pass/fail with bounded diagnostics.
- `cutover_playbook_ref`: Ref to rollout/removal guidance.

**Rules**:

- Replay success is a deployment gate for workflow-shape changes.
- Patch bridges require explicit removal conditions.
- Replay fixtures must not contain sensitive or unbounded runtime content.

## ReconcileOutcome

Bounded result from recurring managed-session reconciliation.

**Fields**:

- `session_id`
- `runtime_id`
- `session_epoch`
- `outcome`: `reattached`, `marked_degraded`, `finalized`, `orphan_reported`, or `unchanged`
- `artifact_refs`: Optional bounded refs.
- `diagnostic_code`: Compact non-secret diagnostic.

**Rules**:

- Reconcile must handle missing containers, orphaned runtime state, stale degraded sessions, and supervision drift.
- Outcomes are safe for workflow summaries, logs, and operator views.

## State Transitions

```text
launching
  -> idle
  -> active_turn
  -> interrupted
  -> idle
  -> clearing
  -> idle (session_epoch incremented)
  -> canceling
  -> idle or degraded
  -> terminating
  -> terminated

Any non-terminal state
  -> degraded
  -> idle, active_turn, terminating, or terminated after reconcile/control
```

Terminal rule: once `terminating` begins, further mutators are rejected except deterministic duplicate terminate handling.
