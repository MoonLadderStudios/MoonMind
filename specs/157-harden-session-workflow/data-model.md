# Data Model: Harden Session Workflow

## Managed Session Workflow Input

Represents the bounded payload used to start or continue one task-scoped Codex managed-session workflow run.

Fields:

- `task_run_id`: Stable task run that owns the session.
- `runtime_id`: Managed Codex runtime identifier.
- `execution_profile_ref`: Optional execution profile selection.
- `session_id`: Stable task-scoped session identifier. Defaults from task/run identity when absent.
- `session_epoch`: Current continuity interval. Starts at 1 and advances on clear/reset.
- `container_id`: Active session container identifier when handles are attached.
- `thread_id`: Active Codex thread identifier for the current epoch.
- `active_turn_id`: In-flight turn identifier when one exists.
- `last_control_action`: Latest canonical control action recorded by the workflow.
- `last_control_reason`: Optional operator/system reason for the latest control.
- `latest_summary_ref`: Latest durable session summary artifact reference.
- `latest_checkpoint_ref`: Latest durable checkpoint artifact reference.
- `latest_control_event_ref`: Latest durable control-event artifact reference.
- `latest_reset_boundary_ref`: Latest durable reset-boundary artifact reference.
- `continue_as_new_event_threshold`: Optional shortened-history threshold for validation.
- `request_tracking_state`: Compact identified-control metadata when stable request identity exists.

Validation rules:

- Runtime ID must normalize to the managed Codex runtime.
- Session identity, runtime locator fields, artifact refs, and reasons must be non-blank when present.
- The shortened-history threshold must be positive when present.
- Request tracking must stay compact and must not include prompts, transcripts, scrollback, or secrets.

## Managed Session Workflow State

Represents the workflow-owned queryable state for the active run.

Fields:

- `binding`: Workflow/session/task/runtime binding.
- `status`: Current workflow status such as active, clearing, terminating, or terminated.
- `container_id`
- `thread_id`
- `active_turn_id`
- `last_control_action`
- `last_control_reason`
- `latest_summary_ref`
- `latest_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`
- `termination_requested`
- `request_tracking_state`

Validation rules:

- Runtime-bound mutators require a locator before activity invocation; accepted updates may wait for handles.
- Turn-specific mutators require an active turn once runtime handles are attached.
- Epoch-sensitive controls reject stale epochs.
- Mutators are rejected after termination begins unless they are the same idempotent termination path.
- Continuity refs are updated only from successful summary/publication projections.

State transitions:

- `active` -> `active` after send, steer, interrupt, or cancel resolves.
- `active` -> `clearing` -> `active` after clear/reset resolves.
- `active` -> `terminating` -> `terminated` after terminate cleanup and handler drain.
- Active run -> new active run after Continue-As-New handoff with bounded state preserved.

## Runtime Readiness Gate

Represents the workflow condition required before runtime-bound controls can address the managed session.

Fields:

- `container_id`: Required for readiness.
- `thread_id`: Required for readiness.
- `active_turn_id`: Required only for turn-scoped controls.

Validation rules:

- Send and clear may wait for container/thread readiness.
- Interrupt and steer may wait for container/thread readiness, then require an active turn.
- Terminate may complete workflow-local termination even when no runtime handles were ever attached.

## Handoff Payload

Represents the bounded state carried across Continue-As-New.

Fields:

- Session binding fields: task run, runtime, session ID, epoch, execution profile.
- Runtime locator: container ID and thread ID when attached.
- Active turn ID when present.
- Last control action and reason.
- Latest continuity refs.
- Shortened-history test threshold when present.
- Compact request-tracking state when present.

Validation rules:

- Handoff payload must be sufficient to keep the same logical session addressable after handoff.
- Handoff payload must not include large runtime content, transcripts, prompts, or secret-bearing metadata.
- Handoff occurs only after accepted handlers finish.

## Request-Tracking State

Represents compact metadata for identified mutating controls.

Fields:

- `request_id`: Stable caller/workflow request identity when available.
- `action`: Canonical session control action.
- `session_epoch`: Epoch observed by the accepted request.
- `status`: Accepted, completed, failed, or superseded.
- `result_ref`: Optional compact reference to durable evidence, not inline transcript content.

Validation rules:

- Entries are carried only when stable identity exists.
- Entries must be bounded in size.
- Entries must not include prompt text, transcript text, terminal scrollback, secrets, or full artifact bodies.
