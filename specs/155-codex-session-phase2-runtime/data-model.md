# Data Model: Codex Session Phase 2 Runtime Behaviors

## Managed Session Workflow State

Represents the bounded workflow-visible state for a task-scoped Codex managed session.

Fields:

- `session_id`: Stable task-scoped managed session identifier.
- `session_epoch`: Current continuity interval. Increments on clear/reset.
- `container_id`: Active task-scoped session container identifier when runtime handles are attached.
- `thread_id`: Active Codex thread identifier for the current epoch.
- `active_turn_id`: Active Codex turn identifier when a turn is in flight.
- `status`: Workflow state such as active, clearing, terminating, or terminated.
- `last_control_action`: Latest canonical control action applied to the session.
- `last_control_reason`: Optional operator/system reason for the latest control action.
- `termination_requested`: Completion gate indicating successful termination cleanup has been requested and the workflow may complete.

Validation rules:

- Runtime mutators require attached runtime handles unless the action is explicitly safe without them.
- `interrupt_turn` and `steer_turn` require an active turn.
- Epoch-sensitive controls reject stale `session_epoch` values.
- Non-terminate mutators are rejected once termination is in progress.
- Duplicate terminate may return the current terminating/terminated state when it is the same logical termination path.

State transitions:

- `active` -> `clearing` -> `active` after clear/reset succeeds.
- `active` -> `active` after cancel stops active work without destroying the session.
- `active` -> `terminating` -> `terminated` after terminate cleanup succeeds and the workflow run completes.
- `active` -> `active` after successful steering when the active turn remains running.

## Managed Session Runtime State

Represents the container-local mapping between MoonMind session identity and Codex App Server session identity.

Fields:

- `session_id`
- `session_epoch`
- `logical_thread_id`
- `vendor_thread_id`
- `vendor_thread_path`
- `container_id`
- `active_turn_id`
- `last_control_action`
- `last_control_at`
- `last_turn_id`
- `last_turn_status`
- `last_turn_error`
- `last_assistant_text`

Validation rules:

- `session_id`, `session_epoch`, `container_id`, and logical thread identity must match the requested locator.
- `steer_turn` requires the requested turn to match `active_turn_id`.
- `interrupt_turn` requires the requested turn to match `active_turn_id` unless durable controller state proves the turn is already stopped.
- Runtime state persists after steering, interruption, clear/reset, and termination so the next activity invocation can recover state.

## Managed Session Supervision Record

Represents the operational recovery index used by the controller and supervisor.

Fields:

- `session_id`
- `session_epoch`
- `task_run_id`
- `container_id`
- `thread_id`
- `active_turn_id`
- `runtime_id`
- `image_ref`
- `control_url`
- `status`
- `workspace_path`
- `session_workspace_path`
- `artifact_spool_path`
- `latest_summary_ref`
- `latest_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`
- `error_message`
- `updated_at`

Validation rules:

- Duplicate launch is safe only when the record matches the requested session/task/workspace/image and the recorded container still exists.
- Duplicate clear is safe only when the record already reflects the requested next epoch/thread boundary and reset-boundary evidence exists.
- Duplicate interrupt is safe only when the record proves the active turn has already been stopped.
- Duplicate terminate is safe when the record is already terminal with status `terminated`.
- Stale or mismatched locators remain permanent failures, not successful duplicates.

## Session Control Request

Represents a typed control mutation against the managed session.

Control kinds:

- `CancelSession`
- `TerminateSession`
- `SteerTurn`
- `InterruptTurn`
- `ClearSession`
- `launch_session`

Common fields:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `turn_id` when controlling an active turn
- `instructions` when steering a turn
- `reason` or metadata when provided by an operator/system caller

Validation rules:

- Required identity fields must be non-blank.
- Turn controls require `turn_id`.
- Steering requires non-blank instructions.
- Clear/reset requires a new thread identifier that differs from the current thread.

## Session Control Result

Represents the normalized result returned to workflow/controller callers.

Fields:

- `session_state`: Current managed session state after the control action.
- `status`: Control result status.
- `turn_id`: Present for turn-scoped controls.
- `latest_summary_ref`
- `latest_checkpoint_ref`
- `latest_control_event_ref`
- `latest_reset_boundary_ref`
- `metadata`: Small diagnostic/control metadata, excluding secrets and large transcripts.

Validation rules:

- Result `session_id` must match the workflow binding.
- Result `session_epoch` updates workflow state only after the runtime/controller confirms the transition.
- Unknown status values fail explicitly rather than being silently normalized to success.
