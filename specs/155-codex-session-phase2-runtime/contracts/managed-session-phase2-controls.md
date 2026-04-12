# Contract: Codex Managed Session Phase 2 Controls

## Scope

This contract covers Phase 2 runtime behavior for task-scoped Codex managed sessions. It is not a public HTTP API contract; it defines the workflow/activity/runtime control boundary that must be validated by automated tests.

## Control Outcomes

| Control | Required Outcome | Terminal? | Runtime Side Effect |
| --- | --- | --- | --- |
| `CancelSession` | Stop active in-flight work if present and leave session recoverable or idle | No | Interrupt active turn when one exists |
| `TerminateSession` | Remove or treat as already removed the container, finalize supervision, clear active turn state, and allow workflow completion only after cleanup succeeds | Yes | Destroy session container and finalize session record |
| `SteerTurn` | Send additional instructions to the active turn and preserve active-turn tracking while the turn remains active | No | Call runtime steering protocol for the active turn |
| `InterruptTurn` | Stop the active turn and record interrupted state | No | Call runtime interruption protocol unless durable state proves the turn is already stopped |
| `ClearSession` | Advance to a new epoch/thread inside the same container and clear active turn state | No | Start a new Codex thread and publish reset/control boundary evidence |
| `launch_session` | Start or reuse the task-scoped container and initial thread | No | Create container unless durable state proves the live container already exists |

## Workflow Update Contract

### `CancelSession`

Input:

- Optional `reason`

Required behavior:

- If `active_turn_id` exists, invoke the interruption activity with the active turn.
- Update workflow state from the returned session state.
- Set `last_control_action` to `cancel_session`.
- Preserve `termination_requested = false`.
- Preserve recoverable session identity and runtime handles.

Failure behavior:

- Missing runtime handles with no active turn is not a destructive error; the workflow records cancellation intent and remains non-terminal.
- Interruption failures propagate unless durable state proves the turn was already stopped.

### `TerminateSession`

Input:

- Optional `reason`

Required behavior:

- If runtime handles exist, invoke the termination activity.
- Wait for the activity to remove the container and finalize supervision.
- Apply returned session state and clear active turn state.
- Set `last_control_action` to `terminate_session`.
- Set `termination_requested = true` only after cleanup confirmation.

Failure behavior:

- Cleanup failure must not be swallowed as success.
- Duplicate termination after `termination_requested = true` may return current state without repeating cleanup.

### `SteerTurn`

Input:

- `sessionEpoch`
- `message` or steering instructions
- Optional `reason`

Required behavior:

- Validate current epoch.
- Require active turn.
- Invoke runtime steering activity with active `turnId`.
- Preserve `active_turn_id` when runtime reports the turn remains active.
- Record `last_control_action = steer_turn`.

Failure behavior:

- Stale epoch or missing active turn is rejected at the workflow boundary.
- Runtime status values outside the supported control result set fail explicitly.

## Activity/Controller Contract

### Idempotency

Activity/controller boundaries must be safe under at-least-once delivery:

- `launch_session`: If the durable record matches the request and the recorded container is live, return that handle without launching another container.
- `clear_session`: If the durable record already reflects the requested next epoch/thread and reset boundary evidence exists, return the current handle without advancing another epoch.
- `interrupt_turn`: If durable state proves the requested active turn has already stopped, return the current interrupted/idle state without invoking another runtime interrupt.
- `terminate_session`: If durable state is already `terminated`, return the terminated handle without invoking another destructive cleanup.

Stale locators and mismatched durable records remain permanent failures.

### Failure Classification

Permanent failure examples:

- Missing required control identity fields.
- Stale `sessionEpoch`.
- Wrong `containerId` or `threadId` for the durable session record.
- Steering or interruption requested for a non-active turn.
- Unsupported runtime control status.

Transient failure examples:

- Docker transport failure that does not prove the container is missing.
- Runtime process startup or communication failure.
- Artifact publication delay where control state remains otherwise valid.

### Heartbeats

The following activity boundaries must heartbeat while waiting on runtime/controller work and must declare heartbeat timeouts:

- `agent_runtime.steer_turn`
- `agent_runtime.interrupt_turn`
- `agent_runtime.clear_session`
- `agent_runtime.terminate_session`
- Existing long-running `agent_runtime.send_turn` behavior must remain heartbeat-enabled.

## Runtime Protocol Contract

### Steering

The container-side runtime must:

- Validate the requested locator against persisted runtime state.
- Require requested `turnId` to match active turn.
- Resume the current Codex thread when needed.
- Send steering instructions to the active Codex turn through the Codex App Server steering protocol.
- Persist `last_control_action = steer_turn`, latest turn status, and active turn identity.
- Return a typed turn response, not a hardcoded unsupported result.

### Termination

The controller termination path must:

- Remove the session container or treat an already-missing container as removed.
- Emit/finalize session termination through the supervisor path when a record exists.
- Mark the durable record terminated.
- Return a typed session handle with `activeTurnId = null`.

## Validation Contract

Minimum automated validation coverage:

- Workflow `CancelSession` interrupts active turn and remains non-terminal.
- Workflow `TerminateSession` calls runtime termination and does not mark completion before cleanup confirmation.
- Runtime `steer_turn` calls the Codex App Server steering protocol.
- Controller duplicate launch, clear, interrupt, and terminate return durable state without duplicating side effects.
- Activity catalog/wrappers expose heartbeat behavior for blocking controls.
- Permanent stale/invalid control inputs fail deterministically.
