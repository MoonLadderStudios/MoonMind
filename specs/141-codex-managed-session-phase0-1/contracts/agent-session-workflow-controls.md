# Contract: Agent Session Workflow Controls

## Public Mutation Surface

`MoonMind.AgentSession` exposes these workflow-level mutating handlers for Phase 1:

- Signal: `attach_runtime_handles`
- Update: `SendFollowUp`
- Update: `InterruptTurn`
- Update: `SteerTurn`
- Update: `ClearSession`
- Update: `CancelSession`
- Update: `TerminateSession`

The generic mutating `control_action` signal is not part of the Phase 1 contract.

## Validation Rules

- Any update that depends on runtime state rejects if runtime handles are missing.
- `InterruptTurn` and `SteerTurn` reject when there is no active turn.
- `InterruptTurn` and `SteerTurn` reject when the provided `sessionEpoch` does not match the workflow binding epoch.
- `ClearSession` rejects while the workflow is already clearing.
- All mutating updates reject once the workflow is canceling, terminating, or terminated.

## Activity Wiring

- `SendFollowUp` -> `agent_runtime.send_turn`
- `InterruptTurn` -> `agent_runtime.interrupt_turn`
- `SteerTurn` -> `agent_runtime.steer_turn`
- `ClearSession` -> `agent_runtime.clear_session`

`CancelSession` and `TerminateSession` remain workflow-owned state transitions in this slice; true terminate semantics are deferred to later phases.
