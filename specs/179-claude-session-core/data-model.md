# Data Model: Claude Session Core

## ClaudeManagedSession

Represents one canonical Claude Code run in the shared Managed Session Plane.

Fields:
- `session_id`: stable canonical session identifier.
- `runtime_family`: fixed value `claude_code`.
- `execution_owner`: one of `local_process`, `anthropic_cloud_vm`, or `sdk_host`.
- `state`: one documented session lifecycle value.
- `primary_surface`: one documented surface kind.
- `projection_mode`: one of `primary`, `remote_projection`, or `handoff`.
- `surface_bindings`: zero or more `ClaudeSurfaceBinding` records.
- `active_turn_id`: optional current turn identifier.
- `parent_session_id`, `fork_of_session_id`, `handoff_from_session_id`, `session_group_id`: optional lineage identifiers.
- `created_by`: one documented session creator kind.
- `created_at`, `updated_at`, `ended_at`: lifecycle timestamps.
- `extensions`: bounded Claude-specific metadata that is not required by the shared core schema.

Validation rules:
- Identifiers must be nonblank after trimming.
- `runtime_family` is always `claude_code`.
- Unknown fields are rejected.
- `threadId`, `thread_id`, `childThread`, and `child_thread` are not accepted.
- A remote projection surface cannot change execution owner.
- A cloud handoff destination must be a distinct session with `execution_owner = anthropic_cloud_vm` and `handoff_from_session_id` set.

## ClaudeSurfaceBinding

Represents one attached surface for a Claude managed session.

Fields:
- `surface_id`: stable surface identifier.
- `surface_kind`: terminal, vscode, jetbrains, desktop, web, mobile, scheduler, channel, or sdk.
- `projection_mode`: primary or remote_projection.
- `connection_state`: connected, disconnected, reconnecting, or detached.
- `interactive`: whether the surface can interact with the session.

Validation rules:
- Unknown fields are rejected.
- Identifiers must be nonblank.
- Remote Control surfaces use `projection_mode = remote_projection`.

## ClaudeManagedTurn

Represents one bounded input turn in a Claude managed session.

Fields:
- `turn_id`: stable turn identifier.
- `session_id`: canonical parent session identifier.
- `input_origin`: human, schedule, channel, sdk, or team_message.
- `state`: submitted, gathering_context, pending_decision, executing, verifying, interrupted, completed, or failed.
- `summary`: optional bounded summary text.
- `started_at`, `completed_at`: optional lifecycle timestamps.

Validation rules:
- Unknown fields are rejected.
- Identifiers must be nonblank.
- The record references `session_id`; no thread aliases are accepted.

## ClaudeManagedWorkItem

Represents one event-bearing unit emitted during a Claude turn.

Fields:
- `item_id`: stable work item identifier.
- `turn_id`: parent turn identifier.
- `session_id`: parent session identifier.
- `kind`: context_read, context_injection, tool_call, hook_call, approval_request, checkpoint, compaction, rewind, subagent, team_message, summary, or telemetry_flush.
- `status`: queued, in_progress, completed, failed, declined, or canceled.
- `payload`: bounded metadata object.
- `started_at`, `ended_at`: optional lifecycle timestamps.

Validation rules:
- Unknown fields are rejected.
- Identifiers must be nonblank.
- Payload metadata remains bounded and must not become a runtime transport envelope.

## State Transitions

This story validates allowed state values rather than implementing a persistence-backed transition engine.

Allowed session states:
- creating
- starting
- active
- waiting
- compacting
- rewinding
- archiving
- ended
- failed

Allowed turn states:
- submitted
- gathering_context
- pending_decision
- executing
- verifying
- interrupted
- completed
- failed

Allowed work-item statuses:
- queued
- in_progress
- completed
- failed
- declined
- canceled

Allowed surface connection states:
- connected
- disconnected
- reconnecting
- detached
