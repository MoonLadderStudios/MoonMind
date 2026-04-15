# Data Model: Typed Workflow Messages

## CodexManagedSessionWorkflowInput

- **Purpose**: Workflow run and Continue-As-New input for one task-scoped Codex managed session.
- **Fields**: task run id, runtime id, optional execution profile, session id, session epoch, runtime handles, latest continuity refs, event threshold, request tracking state.
- **Rules**: Identifiers are nonblank when present; runtime id must identify Codex; request tracking entries are typed and bounded by workflow logic.

## CodexManagedSessionAttachRuntimeHandlesSignal

- **Purpose**: Typed signal payload for attaching bounded runtime handles to a live session workflow.
- **Fields**: optional session epoch, container id, thread id, active turn id, last control action, last control reason.
- **Rules**: Unknown fields are rejected; nonblank fields are normalized; control action is restricted to the canonical managed-session action set.

## Managed Session Update Requests

- **Purpose**: Named request contracts for mutating controls.
- **Models**: `CodexManagedSessionSendFollowUpRequest`, `CodexManagedSessionInterruptRequest`, `CodexManagedSessionSteerRequest`, `CodexManagedSessionClearUpdateRequest`, `CodexManagedSessionCancelUpdateRequest`, `CodexManagedSessionTerminateUpdateRequest`.
- **Rules**: Unknown fields are rejected; reason and request id are normalized; epoch-sensitive controls carry `sessionEpoch` and validators reject stale values.

## CodexManagedSessionRequestTrackingEntry

- **Purpose**: Bounded idempotency record for mutating control requests.
- **Fields**: request id, action, session epoch, status, optional result ref.
- **Rules**: Request id cannot be reused across actions; completed requests reject duplicate mutation; carried state remains capped.

## CodexManagedSessionSnapshot

- **Purpose**: Typed query projection of workflow-owned session state.
- **Fields**: binding, status, handles, latest control details, latest continuity refs, termination flag, request tracking state.
- **Rules**: Operator-visible snapshot is bounded and excludes prompts, raw logs, transcripts, credentials, and scrollback.
