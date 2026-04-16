# Contract: Claude Managed Session Core

## Public Schema Module

`moonmind.schemas.managed_session_models`

## Required Models

- `ClaudeManagedSession`
- `ClaudeManagedTurn`
- `ClaudeManagedWorkItem`
- `ClaudeSurfaceBinding`

## Required Type Aliases

- `ClaudeRuntimeFamily`
- `ClaudeExecutionOwner`
- `ClaudeSurfaceKind`
- `ClaudeProjectionMode`
- `ClaudeSessionState`
- `ClaudeTurnState`
- `ClaudeWorkItemKind`
- `ClaudeWorkItemStatus`
- `ClaudeSurfaceConnectionState`
- `ClaudeSessionCreatedBy`

## Wire Shape

Models use camelCase aliases for serialized fields and reject unknown fields.

Required session fields:
- `sessionId`
- `runtimeFamily`
- `executionOwner`
- `state`
- `primarySurface`
- `projectionMode`
- `createdBy`
- `createdAt`
- `updatedAt`

Required turn fields:
- `turnId`
- `sessionId`
- `inputOrigin`
- `state`
- `startedAt`

Required work-item fields:
- `itemId`
- `turnId`
- `sessionId`
- `kind`
- `status`
- `payload`
- `startedAt`

## Boundary Functions

### `ClaudeManagedSession.with_remote_projection(...) -> ClaudeManagedSession`

Creates a copy of an existing local/session-owned Claude managed session with an additional remote-projection surface binding.

Requirements:
- Preserves the original `sessionId`.
- Preserves the original `executionOwner`.
- Adds a surface binding with `projectionMode = remote_projection`.
- Rejects blank surface identifiers.

### `ClaudeManagedSession.cloud_handoff(...) -> ClaudeManagedSession`

Creates a destination cloud-owned Claude managed session from a source session.

Requirements:
- Destination `sessionId` must differ from the source session.
- Destination `executionOwner` is `anthropic_cloud_vm`.
- Destination `handoffFromSessionId` equals the source `sessionId`.
- Source session is not mutated.

## Alias Exclusions

Claude models must reject:
- `threadId`
- `thread_id`
- `childThread`
- `child_thread`

No compatibility transform may map these fields to `sessionId`.

## Validation Surface

Tests must confirm:
- All documented session shapes validate.
- Invalid lifecycle values are rejected.
- Remote Control projection preserves execution ownership.
- Cloud handoff creates a distinct destination session with lineage.
- Unknown alias fields such as `threadId` and `childThread` are rejected.
- Existing Codex managed-session contracts remain unaffected.
