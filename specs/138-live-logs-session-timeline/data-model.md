# Data Model: Live Logs Session Timeline

## RunObservabilityEvent

The canonical event entity for Phase 1 is `RunObservabilityEvent`.

### Required fields

- `runId`: task-run identifier used by observability APIs
- `sequence`: run-global monotonically increasing sequence number
- `timestamp`: ISO-8601 event timestamp
- `stream`: `stdout | stderr | system | session`
- `kind`: normalized MoonMind event kind
- `text`: display text for the timeline row

### Optional fields

- `offset`: byte offset for stream-backed output rows when known
- `sessionId`
- `sessionEpoch`
- `containerId`
- `threadId`
- `turnId`
- `activeTurnId`
- `metadata`

### Event kind set in scope for this slice

- `stdout_chunk`
- `stderr_chunk`
- `system_annotation`
- `session_started`
- `session_resumed`
- `session_cleared`
- `session_terminated`
- `turn_started`
- `turn_completed`
- `turn_interrupted`
- `approval_requested`
- `approval_resolved`
- `summary_published`
- `checkpoint_published`
- `reset_boundary_published`

## ManagedRunRecord extensions

Phase 1 extends the durable managed-run record with two groups of fields.

### Structured history fields

- `observabilityEventsRef`: durable artifact ref for `observability.events.jsonl`

### Latest session snapshot fields

- `sessionId`
- `sessionEpoch`
- `containerId`
- `threadId`
- `activeTurnId`

These fields are optional and only populated when the run is session-aware.

## SessionTimelineRollout

Phase 0 introduces one rollout configuration value for the session-aware timeline contract.

### Allowed values

- `off`
- `internal`
- `codex_managed`
- `all_managed`

### Derived values exposed to the UI

- `liveLogsSessionTimelineEnabled`: boolean derived from rollout not being `off`
- `liveLogsSessionTimelineRollout`: the exact rollout scope

## Persistence mapping

### Active live transport

- `RunObservabilityEvent` rows are appended to `live_streams.spool` as JSONL during execution.

### Ended-run durable history

- `live_streams.spool` content is promoted to an artifact-backed `observability.events.jsonl` file during publication/finalization.
- The resulting artifact ref is written to `ManagedRunRecord.observabilityEventsRef`.

### Summary snapshot

- The latest known session identity from managed-session publication/runtime state is mirrored onto the managed-run record at the time the durable history ref is written or updated.
