# Data Model: codex-managed-session-phase0-1

## Managed Session Truth Surfaces

### Operator/Audit Truth

- Source: durable artifacts plus bounded workflow metadata
- Used for: operator presentation, audit, continuity inspection, recovery reasoning inputs
- Must not depend on: container-local state, runtime home directories, scrollback

### Operational Recovery Index

- Source: `CodexManagedSessionRecord` persisted through `ManagedSessionStore`
- Used for: supervision restart, reconciliation, locating active containers/threads, production artifact publication bookkeeping
- Not authoritative for: operator/audit truth

### Disposable Cache

- Source: container-local process state and vendor thread storage
- Used for: continuity/performance only
- Durability: disposable

## Agent Session Workflow Update Requests

### SendFollowUp

- Fields:
  - `message` (required, non-blank)
  - `reason` (optional, non-blank)

### InterruptTurn

- Fields:
  - `sessionEpoch` (required, integer >= 1)
  - `reason` (optional, non-blank)

### SteerTurn

- Fields:
  - `sessionEpoch` (required, integer >= 1)
  - `message` (required, non-blank)

### ClearSession

- Fields:
  - `reason` (optional, non-blank)

### CancelSession

- Fields:
  - `reason` (optional, non-blank)

### TerminateSession

- Fields:
  - `reason` (optional, non-blank)

## Validator Context

The workflow validators must inspect:

- whether a binding exists,
- whether runtime handles are attached,
- the current `sessionEpoch`,
- whether an `activeTurnId` exists,
- whether a clear is already in progress,
- whether cancellation/termination has started.
