# Data Model: codex-managed-session-plane-phase4

## Transitional Session Runtime State

The container-side bridge persists a small JSON state document in the mounted session workspace.

### Fields

- `sessionId`: MoonMind task-scoped session identity.
- `sessionEpoch`: current MoonMind epoch for the session.
- `containerId`: Docker container id for the active session container.
- `logicalThreadId`: MoonMind-owned logical thread id for the current epoch.
- `vendorThreadId`: Codex app-server thread id backing the current logical thread.
- `activeTurnId`: current in-flight turn id when one exists.
- `launchedAt`: UTC timestamp when the session was initialized.
- `lastControlAction`: last session action executed through the bridge.
- `lastControlAt`: UTC timestamp of the last action.

## Invariants

- `sessionId` and `containerId` remain stable across `clear_session`.
- `sessionEpoch` increments on `clear_session`.
- `logicalThreadId` changes on `clear_session`.
- `vendorThreadId` may change on launch and clear/reset, but it is hidden behind the logical mapping.
- `activeTurnId` is non-null only while a turn is in progress.

## Boundary Rule

This state document is runtime continuity cache only. MoonMind artifacts and bounded workflow metadata remain the durable truth.
