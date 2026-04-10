# Data Model: Codex Managed Session Plane Phase 10

## Session-Backed ManagedRunRecord

Phase 10 reuses the existing `ManagedRunRecord` entity rather than introducing a new observability record type.

### Fields used by this slice

- `runId`: the step-scoped managed run identifier used by `/api/task-runs/{taskRunId}/...`
- `workflowId`: producing `MoonMind.AgentRun` workflow id for authorization and observability ownership
- `agentId`: managed agent identifier
- `runtimeId`: canonical runtime id (`codex_cli`)
- `status`: final managed-run status for the step
- `startedAt`
- `finishedAt`
- `workspacePath`: task-scoped workspace root used for artifact-backed fallbacks
- `stdoutArtifactRef`
- `stderrArtifactRef`
- `diagnosticsRef`
- `errorMessage`
- `failureClass`
- `liveStreamCapable`: `false` for this phase

## Source Mapping

The managed-session publication payload already contains the durable runtime refs needed for Phase 10.

### Mapping

- `sessionArtifacts.metadata.stdoutArtifactRef` -> `ManagedRunRecord.stdoutArtifactRef`
- `sessionArtifacts.metadata.stderrArtifactRef` -> `ManagedRunRecord.stderrArtifactRef`
- `sessionArtifacts.metadata.diagnosticsRef` -> `ManagedRunRecord.diagnosticsRef`
- step result terminal status -> `ManagedRunRecord.status`

## Non-Goals

- No new session-only observability entity
- No live-stream transport metadata for session-backed runs yet
- No terminal attach or shell session projection in the managed-run record
