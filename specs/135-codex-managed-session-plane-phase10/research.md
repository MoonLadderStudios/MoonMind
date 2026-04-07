# Research: Codex Managed Session Plane Phase 10

- Reuse the existing `/api/task-runs/{taskRunId}/...` observability plane instead of adding a session-specific route. This keeps Mission Control on the same artifact-first fetch sequence already documented in `docs/ManagedAgents/LiveLogs.md`.
- The managed-session controller and supervisor already persist stdout, stderr, diagnostics, summary, checkpoint, and reset refs durably. Phase 10 should consume those durable refs rather than query the session container again.
- `CodexSessionAdapter` is currently the gap because it finishes a session-backed step without persisting a `ManagedRunRecord`, so API-side observability cannot discover the run through `ManagedRunStore`.
- A session-backed `ManagedRunRecord` can legitimately set `liveStreamCapable=false` in this phase because the MVP requirement is artifact-first observability, with optional live following explicitly deferred.
