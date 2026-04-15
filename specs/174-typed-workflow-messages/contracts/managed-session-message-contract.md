# Managed Session Message Contract

This contract defines the target Temporal message boundary for `MoonMind.AgentSession`.

## Workflow Run And Continue-As-New

- `MoonMind.AgentSession.run` accepts `CodexManagedSessionWorkflowInput`.
- `MoonMindAgentSessionWorkflow.__init__` accepts `CodexManagedSessionWorkflowInput`.
- Continue-As-New handoff uses `CodexManagedSessionWorkflowInput`.

## Signals

- `attach_runtime_handles` accepts `CodexManagedSessionAttachRuntimeHandlesSignal`.
- `control_action` remains a compatibility shim for existing histories and internal fallback choreography; it is not the canonical client-facing control surface.

## Updates

- `SendFollowUp` accepts `CodexManagedSessionSendFollowUpRequest`.
- `InterruptTurn` accepts `CodexManagedSessionInterruptRequest`.
- `SteerTurn` accepts `CodexManagedSessionSteerRequest`.
- `ClearSession` accepts `CodexManagedSessionClearUpdateRequest`.
- `CancelSession` accepts `CodexManagedSessionCancelUpdateRequest`.
- `TerminateSession` accepts `CodexManagedSessionTerminateUpdateRequest`.

Every mutating update has a validator. Validators check typed request shape and the relevant preconditions before mutation work starts.

## Query

- `get_status` returns a JSON projection built from `CodexManagedSessionSnapshot`.

## Mutation Safety

- Epoch-sensitive controls reject stale `sessionEpoch`.
- Completed requests reject duplicate mutation.
- Request identifiers cannot be reused across actions.
- Conflicting mutations execute under the workflow mutation lock.
- Continue-As-New carries bounded typed request tracking state.
