# Data Model: Live Logs Session Plane Producer

## Session Lifecycle Row

Phase 2 continues to use `RunObservabilityEvent` from Phase 1.

Relevant rows in this slice:

- `session_started`
- `session_resumed`
- `turn_started`
- `turn_completed`
- `turn_interrupted`
- `session_cleared`
- `session_reset_boundary`
- `session_terminated`
- `summary_published`
- `checkpoint_published`

Common fields:

- `sequence`
- `timestamp`
- `stream="session"`
- `kind`
- `text`
- optional session snapshot fields:
  - `session_id`
  - `session_epoch`
  - `container_id`
  - `thread_id`
  - `turn_id`
  - `active_turn_id`
- `metadata`

## Publication Row Metadata

`summary_published` metadata:

- `summaryRef`
- `status`

`checkpoint_published` metadata:

- `checkpointRef`
- `status`

## Control-Signal Mapping

The adapter mirrors control intent into the workflow/session control path with:

- `start_session`
- `resume_session`
- `send_turn`
- `clear_session`
- `interrupt_turn`
- `terminate_session`

Phase 2 does not add a separate run-record schema. It relies on the Phase 1 managed-run/session snapshot fields and makes the missing control/lifecycle/publication facts visible in the observability journal.
