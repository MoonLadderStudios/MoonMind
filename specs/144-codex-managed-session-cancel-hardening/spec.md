# Feature Specification: Codex Managed Session Cancel Hardening

**Feature Branch**: `144-codex-managed-session-cancel-hardening`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Harden cancel flow for Codex managed session workflows so UI-triggered task cancellation still tears down the managed session when workflow replay is stuck or the parent does not reach finalization promptly."

## User Scenarios & Testing

### User Story 1 - Canceling a Codex task tears down its managed session (Priority: P1)

Operators need task cancellation to stop the task-scoped Codex managed session, not just record a cancel request on the parent workflow.

**Why this priority**: A canceled task that leaves its managed session container running leaks runtime resources and makes the UI appear stuck.

**Independent Test**: Trigger cancellation for a Codex managed-session task and verify the service best-effort targets the session workflow while the workflow-owned terminate path invokes the managed-session terminate activity when runtime handles exist.

**Acceptance Scenarios**:

1. **Given** a `MoonMind.Run` has an active Codex task-scoped session, **When** an operator cancels the task, **Then** MoonMind best-effort invokes `TerminateSession` on the session workflow in addition to the normal parent cancel path.
2. **Given** the session workflow still has runtime handles for the active container and thread, **When** `TerminateSession` runs, **Then** it calls the existing `agent_runtime.terminate_session` activity and marks the session as terminating.
3. **Given** runtime handles are already missing or the session is already terminating, **When** `TerminateSession` runs, **Then** the workflow fails fast or degrades safely without leaving a competing control path.

### Edge Cases

- The managed-session store has no record for the task run.
- The stored session record belongs to a different task run.
- The session workflow is already closed or already terminating when the cancel service attempts `TerminateSession`.
- The parent workflow later reaches finalization and attempts a second termination.

## Requirements

### Functional Requirements

- **FR-001**: `TemporalExecutionService.cancel_execution` MUST best-effort target the task-scoped Codex session workflow with `TerminateSession` before or alongside the normal parent cancel/terminate call when a live managed-session record exists for that run.
- **FR-002**: The best-effort session teardown path MUST use existing task-scoped session identity derived from `ManagedSessionStore`; it MUST NOT introduce a second non-Temporal control plane.
- **FR-003**: `MoonMind.AgentSession.TerminateSession` MUST invoke `agent_runtime.terminate_session` when the workflow snapshot still contains `containerId` and `threadId`.
- **FR-004**: The workflow-owned terminate path MUST remain idempotent enough for duplicate invocations from service-level cancel and parent finalization.
- **FR-005**: The slice MUST include automated tests covering service-level session teardown dispatch and workflow-level terminate activity execution.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Unit tests verify canceling a `MoonMind.Run` with a live Codex managed-session record dispatches `TerminateSession` to the session workflow before the existing parent cancel call.
- **SC-002**: Unit tests verify `TerminateSession` executes `agent_runtime.terminate_session` when runtime handles are available.
- **SC-003**: Unit tests verify duplicate or degraded terminate paths do not crash the cancel service.
