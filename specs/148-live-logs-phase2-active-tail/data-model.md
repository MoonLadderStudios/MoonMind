# Data Model: Live Logs Phase 2 Active Tail

## Managed Run Record

Represents the current observability summary source for a managed task run.

Relevant fields:
- `runId`
- `status`
- `workspacePath`
- `mergedLogArtifactRef`
- `stdoutArtifactRef`
- `stderrArtifactRef`
- `diagnosticsRef`
- `observabilityEventsRef`
- `liveStreamCapable`
- `sessionId`
- `sessionEpoch`
- `containerId`
- `threadId`
- `activeTurnId`

Validation rules:
- Terminal statuses report live streaming as ended even when `liveStreamCapable` was true while active.
- Session snapshot fields are optional individually, but `sessionId` anchors the record-derived snapshot.

## Run Observability Event

Represents one normalized row in the active or historical observability timeline.

Relevant fields:
- `runId`
- `sequence`
- `stream`
- `timestamp`
- `text`
- `kind`
- `sessionId`
- `sessionEpoch`
- `containerId`
- `threadId`
- `turnId`
- `activeTurnId`
- `metadata`

Validation rules:
- Rows must validate against the canonical event schema before merged rendering.
- Rows from a shared journal with a mismatched `runId` are ignored.
- Rows with invalid JSON or invalid schema are skipped.

## Merged Log Projection

Represents the text response consumed by the current Live Logs UI.

Source preference:
1. Structured observability event journal.
2. Workspace spool.
3. Final merged log artifact.
4. Legacy combined log artifact.
5. Split stdout/stderr plus diagnostics annotations.

Validation rules:
- Sequence ordering is used when valid sequence values are present.
- Stream headers remain stable text delimiters.
- Empty invalid sources do not block later fallback sources.

## Session Snapshot

Represents bounded managed-session identity for compact operator context.

Relevant fields:
- `sessionId`
- `sessionEpoch`
- `containerId`
- `threadId`
- `activeTurnId`
- continuity artifact refs when a full session record exists

Validation rules:
- Session-store snapshot wins when available.
- Managed-run record snapshot is used when the session-store record is missing.
