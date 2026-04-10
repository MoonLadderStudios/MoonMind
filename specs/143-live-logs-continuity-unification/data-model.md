# Data Model: Live Logs Continuity Unification

## Timeline Artifact Link Ref Precedence

For one timeline row, the UI resolves artifact refs in this order:

1. specific ref keys in event metadata:
   - `summaryRef`
   - `checkpointRef`
   - `controlEventRef`
   - `resetBoundaryRef`
2. generic `artifactRef` when only one durable artifact is attached
3. no link when the event metadata contains no durable artifact ref

## Row-to-Link Mapping

- `summary_published` → summary artifact link
- `checkpoint_published` → checkpoint artifact link
- `session_cleared` → control-event link and reset-boundary link when present
- `session_reset_boundary` → reset-boundary link and control-event link when present

## Copy Semantics

- **Live Logs** explains the timeline as chronological event history.
- **Session Continuity** explains grouped artifacts as durable evidence and drill-down.
- The continuity panel remains grouped by:
  - `runtime`
  - `continuity`
  - `control`
