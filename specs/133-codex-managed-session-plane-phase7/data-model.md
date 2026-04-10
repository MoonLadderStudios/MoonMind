# Data Model: Codex Managed Session Plane Phase 7

## Codex Managed Session Record

- Continues to use the Phase 6 durable record.
- `latest_control_event_ref` stores the newest `session.control_event` artifact ref.
- `latest_checkpoint_ref` stores the newest `session.reset_boundary` artifact ref.

## Session Control Event Artifact

- Suggested filename: `session.control_event.epoch-<N>.json`
- Required payload fields:
  - `linkType`
  - `action`
  - `sessionId`
  - `taskRunId`
  - `containerId`
  - `previousSessionEpoch`
  - `newSessionEpoch`
  - `previousThreadId`
  - `newThreadId`
  - `reason`

## Session Reset Boundary Artifact

- Suggested filename: `session.reset_boundary.epoch-<N>.json`
- Required payload fields:
  - `linkType`
  - `boundaryKind`
  - `sessionId`
  - `taskRunId`
  - `containerId`
  - `sessionEpoch`
  - `threadId`
  - `previousSessionEpoch`
  - `previousThreadId`
