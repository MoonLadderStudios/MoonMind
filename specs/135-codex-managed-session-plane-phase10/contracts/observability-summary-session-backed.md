# Contract: Session-Backed Observability Summary Reuse

Phase 10 does not add a new observability endpoint.

## Existing route reused

- `GET /api/task-runs/{taskRunId}/observability-summary`

## Session-backed run expectations

- The route reads a normal `ManagedRunRecord` stored under the step `taskRunId`.
- The record may originate from a session-backed Codex step rather than a launcher-supervised one-shot process.
- `stdoutArtifactRef`, `stderrArtifactRef`, and `diagnosticsRef` remain the authoritative observability artifacts.
- `supportsLiveStreaming` stays `false` for this path until a later live-follow slice exists.
- No session-only router, PTY, or terminal-attach contract is introduced in this phase.
