# Feature Specification: Codex Session Phase 2 Runtime Behaviors

**Feature Branch**: `155-codex-session-phase2-runtime`  
**Created**: 2026-04-12  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 for the Codex managed session plane using test-driven development. Make termination, cancellation, and steering real end-to-end runtime behaviors. TerminateSession must call the managed runtime terminate activity, wait for controller cleanup/finalization, update workflow state, and only then let the workflow complete. CancelSession must be distinct from TerminateSession by stopping active in-flight work while leaving the session recoverable or idle. steer_turn must be supported through the Codex managed-session runtime and container protocol instead of returning a hardcoded unsupported response. Launch, clear, interrupt, and terminate must be idempotent at the activity/controller boundary for at-least-once activity execution. Permanent failures must be classified explicitly as non-retryable where appropriate. Session activities that may block for meaningful time must use heartbeats plus heartbeat timeout so cancellation is delivered. Exit criteria: terminate always removes the container and finalizes supervision, cancel and terminate have distinct semantics, steer is no longer a stub, and validation tests cover the behavior. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing

### User Story 1 - Terminating a Managed Session Cleans Up Runtime Resources (Priority: P1)

Operators need termination of a task-scoped Codex managed session to reliably destroy the runtime container, finalize supervision, and update the workflow-visible session state only after cleanup succeeds.

**Why this priority**: A terminated workflow that leaves a container or supervision record alive leaks resources, creates misleading operator state, and undermines unattended execution.

**Independent Test**: Start or simulate a task-scoped Codex managed session with runtime handles, invoke termination, and verify the runtime container is removed, the supervision record is finalized, and the workflow reports termination only after those cleanup effects are complete.

**Acceptance Scenarios**:

1. **Given** a managed session has an active container and thread, **When** `TerminateSession` is requested, **Then** MoonMind calls the managed runtime termination path, removes the container, finalizes supervision, clears active turn state, and only then allows the session workflow to complete.
2. **Given** the runtime termination path fails before cleanup is confirmed, **When** `TerminateSession` is requested, **Then** the request does not falsely mark the session as completed and the failure remains visible for retry or operator action.
3. **Given** the same termination request is delivered more than once, **When** cleanup has already succeeded, **Then** the duplicate request returns the existing terminated state without creating a second competing cleanup path.

---

### User Story 2 - Canceling a Session Stops Work Without Destroying Continuity (Priority: P1)

Operators need cancellation to stop the active turn or in-flight work while keeping the managed session recoverable or idle for follow-up, inspection, or later termination.

**Why this priority**: Treating cancel and terminate as the same action forces destructive cleanup when the operator only intended to stop current work, and it prevents clear recovery semantics.

**Independent Test**: Run a managed session with an active turn, invoke cancellation, and verify the active work is stopped while the session identity, container, and thread remain available for recovery or subsequent controls.

**Acceptance Scenarios**:

1. **Given** a managed session has an active turn, **When** `CancelSession` is requested, **Then** MoonMind stops or interrupts that active turn and leaves the session in a non-terminal recoverable state.
2. **Given** a managed session has no active turn, **When** `CancelSession` is requested, **Then** MoonMind records the cancellation intent without destroying the container or finalizing the session.
3. **Given** `CancelSession` has completed, **When** an operator later requests `TerminateSession`, **Then** termination still performs destructive cleanup as a separate action.

---

### User Story 3 - Steering an Active Turn Works End to End (Priority: P1)

Operators need steering to provide additional instructions to an active Codex turn through the managed session plane instead of receiving a permanent unsupported response.

**Why this priority**: Steering is part of the canonical control vocabulary and is required for interactive correction without canceling or resetting the session.

**Independent Test**: Start or simulate an active Codex turn, invoke `SteerTurn`, and verify the steering instruction reaches the runtime protocol, the active turn remains tracked, and workflow/controller state reflects the steering action.

**Acceptance Scenarios**:

1. **Given** a managed session has an active turn, **When** `SteerTurn` is requested with additional instructions, **Then** MoonMind forwards those instructions to the active runtime turn and reports a non-stub response.
2. **Given** a steering request references the wrong turn or stale epoch, **When** the request is evaluated, **Then** MoonMind rejects or fails it deterministically without changing unrelated session state.
3. **Given** steering succeeds, **When** operators inspect session state or observability output, **Then** the latest control action and active turn identity reflect the steering event.

---

### User Story 4 - Runtime Control Activities Are Safe Under Retry and Cancellation (Priority: P2)

Platform operators need launch, clear, interrupt, and terminate controls to behave correctly when activities are retried or canceled by the orchestration system.

**Why this priority**: Managed-session controls affect external runtime resources, so at-least-once delivery and cancellation must not duplicate side effects, hide permanent failures, or leave long-running work uncancelable.

**Independent Test**: Inject duplicate control requests, stale session state, permanent failures, and activity cancellation into the session control path and verify the resulting state remains consistent and operator-visible.

**Acceptance Scenarios**:

1. **Given** launch, clear, interrupt, or terminate is delivered more than once with the same logical request, **When** the prior side effect already completed, **Then** MoonMind returns the current durable session state instead of duplicating the side effect.
2. **Given** a permanent input or state error occurs, **When** the control request is processed, **Then** MoonMind classifies it so it is not retried as a transient runtime failure.
3. **Given** a session control activity can block for meaningful time, **When** cancellation is requested, **Then** MoonMind delivers cancellation through activity heartbeats and stops waiting promptly.

### Edge Cases

- Termination is requested after the container has already been removed.
- Termination is requested while a turn is active.
- Cancellation is requested when there is no active turn.
- Steering is requested with a stale `sessionEpoch` or non-active `turnId`.
- A clear/reset request is retried after the epoch already advanced.
- A launch request is retried after the container and supervision record already exist.
- Runtime cleanup succeeds but artifact or observability publication is delayed or unavailable.
- The managed session record is missing, stale, or does not match the workflow locator.
- The runtime returns an unknown or invalid status for a control action.

## Requirements

### Functional Requirements

- **FR-001**: `TerminateSession` MUST invoke the managed runtime termination path when runtime handles are available.
- **FR-002**: `TerminateSession` MUST wait for runtime cleanup and supervision finalization before marking the session workflow ready to complete.
- **FR-003**: `TerminateSession` MUST clear active turn state and expose the final terminated session state after successful cleanup.
- **FR-004**: `TerminateSession` MUST NOT silently mark a session as completed when runtime cleanup fails before confirmation.
- **FR-005**: `CancelSession` MUST be distinct from `TerminateSession` and MUST NOT destroy the session container or finalize the session record as its normal behavior.
- **FR-006**: `CancelSession` MUST stop or interrupt active in-flight work when an active turn exists, leaving the session recoverable or idle.
- **FR-007**: `CancelSession` MUST remain safe when no active turn exists and MUST preserve session identity for later controls.
- **FR-008**: `SteerTurn` MUST support real runtime steering for an active turn and MUST NOT return a hardcoded unsupported response in the production path.
- **FR-009**: Steering requests MUST preserve active turn tracking when the runtime reports the turn remains active.
- **FR-010**: Steering, interruption, cancellation, clearing, and termination MUST update workflow-visible session state consistently with the durable session record.
- **FR-011**: Launch controls MUST be idempotent for duplicate delivery when the same live session record and container already exist.
- **FR-012**: Clear/reset controls MUST be idempotent for duplicate delivery after the requested new epoch/thread boundary has already been durably recorded.
- **FR-013**: Interrupt controls MUST be idempotent for duplicate delivery after an active turn has already been stopped.
- **FR-014**: Terminate controls MUST be idempotent for duplicate delivery after the session has already reached a terminated state.
- **FR-015**: Permanent invalid input, unsupported state, or stale locator failures MUST be classified so they are not retried as transient runtime failures.
- **FR-016**: Session control activities that may block for meaningful time MUST heartbeat and declare heartbeat timeouts so cancellation can be delivered.
- **FR-017**: The feature MUST include production runtime code changes, not docs/spec-only changes.
- **FR-018**: The feature MUST include automated validation tests for termination cleanup, cancel-vs-terminate semantics, real steering behavior, retry idempotency, failure classification, and cancellation delivery for blocking controls.

### Key Entities

- **Managed Session Workflow State**: The operator-visible session status, current epoch, runtime handles, active turn identity, latest control action, and termination readiness.
- **Managed Session Runtime State**: The container-local mapping between MoonMind session identity and Codex runtime thread/turn identity.
- **Managed Session Supervision Record**: The operational recovery index for current container, thread, epoch, active turn, status, and latest continuity artifacts.
- **Session Control Request**: A typed operator or workflow mutation such as cancel, terminate, steer, clear, interrupt, or launch.
- **Session Control Result**: The normalized state returned after a control action, including status, active turn identity, and latest continuity refs when available.

### Assumptions

- `CancelSession` means stop active work and preserve recoverable continuity; destructive cleanup remains the responsibility of `TerminateSession`.
- Duplicate control requests are considered the same logical request when they reference the same session identity and the already-recorded durable state proves the requested side effect completed.
- Validation tests may be implemented at unit, workflow-boundary, or hermetic integration level as appropriate to the risk of each behavior.
- Provider verification with real external credentials is not required for this phase unless the production runtime path already requires it.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Automated tests verify termination removes or treats as already removed the target container, finalizes supervision, clears active turn state, and reports termination only after cleanup succeeds.
- **SC-002**: Automated tests verify cancellation interrupts active work without marking the session terminated or destroying the session container.
- **SC-003**: Automated tests verify steering reaches the runtime turn protocol and no longer returns the previous hardcoded unsupported result.
- **SC-004**: Automated tests verify duplicate launch, clear, interrupt, and terminate requests do not duplicate external side effects or corrupt durable session state.
- **SC-005**: Automated tests verify stale locator, unsupported state, and invalid permanent failure cases fail deterministically instead of retrying as transient runtime failures.
- **SC-006**: Automated tests verify blocking session control activities expose heartbeat behavior and heartbeat timeout configuration for cancellation delivery.
- **SC-007**: The full required unit-test suite passes after the production runtime changes and validation tests are added.
