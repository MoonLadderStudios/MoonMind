# Feature Specification: Harden Session Workflow

**Feature Branch**: `157-harden-session-workflow`
**Created**: 2026-04-12
**Status**: Draft
**Input**: User description: "Implement Phase 3 of the Codex managed session plane rollout: harden the AgentSession workflow for long-lived message-heavy execution. Add workflow-level locking around async mutators that touch shared state, gate mutators on readiness with workflow wait conditions, wait for all handlers to finish before completion or Continue-As-New, add a Continue-As-New path from run that carries binding/session identity, current epoch, runtime locator, last control action and reason, continuity refs, and dedupe/request-tracking state needed for exactly-once semantics, and add a shortened-history test hook for Continue-As-New testing. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Safe Concurrent Session Control (Priority: P1)

As an operator running a long-lived managed Codex session, I need follow-up, steer, interrupt, clear, cancel, and terminate requests to update one coherent session state even when requests arrive close together, so that the session never reports mixed container, thread, active-turn, or artifact metadata.

**Why this priority**: Concurrent message handling is the highest-risk failure mode for a session workflow that accepts operator controls while agent work is active.

**Independent Test**: Submit overlapping session-control requests against a managed session and verify that shared session fields change in a serialized, internally consistent order.

**Acceptance Scenarios**:

1. **Given** a session with an attached runtime locator and an active turn, **When** two mutating controls arrive concurrently, **Then** each control observes a consistent state and the final query state reflects one complete ordered outcome.
2. **Given** a session-control request changes the active turn or continuity refs, **When** another mutator starts afterward, **Then** it uses the updated state rather than a stale intermediate value.

---

### User Story 2 - Deterministic Readiness Handling (Priority: P2)

As an operator or parent workflow, I need accepted session-control requests to wait for required runtime handles when launch is still completing, so that early controls do not fail randomly because the runtime locator has not been attached yet.

**Why this priority**: Parent workflows and operators can legitimately send controls near session startup; deterministic readiness prevents avoidable failures and reduces manual retries.

**Independent Test**: Send a runtime-bound control before runtime handles are attached, attach the handles afterward, and verify that the control proceeds using the attached locator.

**Acceptance Scenarios**:

1. **Given** a runtime-bound control arrives before container and thread handles are attached, **When** the handles are later attached, **Then** the control proceeds without losing the accepted request.
2. **Given** a mutator is disallowed because the session is terminating or terminated, **When** the request is submitted, **Then** the request is rejected deterministically and does not mutate session state.

---

### User Story 3 - Bounded Long-Running Session History (Priority: P3)

As an operator running a message-heavy session for an extended period, I need the session workflow to continue as new before history grows without bound, while preserving the identity and continuity needed for future controls and audit views.

**Why this priority**: Long-lived sessions must remain durable and inspectable without accumulating unbounded workflow history.

**Independent Test**: Force a low history threshold, trigger the handoff, and verify that the new run receives the same session identity, current epoch, runtime locator, control metadata, continuity refs, and request-tracking state needed to avoid duplicate effects.

**Acceptance Scenarios**:

1. **Given** a session reaches a configured or system-suggested history threshold, **When** the main workflow loop performs a handoff, **Then** the next run receives the bounded state required to continue the same logical session.
2. **Given** a session is completing or handing off, **When** async handlers are still active, **Then** completion or handoff waits for accepted handlers to finish so clients can retrieve update results.

---

### Edge Cases

- Runtime-bound controls arrive before runtime handles are attached.
- A terminate request arrives while another mutator is active.
- A clear request is submitted while clear is already in progress.
- A stale session epoch is supplied for turn-specific controls.
- Continue-As-New is suggested while a mutating handler is active.
- Continue-As-New occurs after continuity refs were updated by the latest control.
- The session is already terminating or terminated when a new mutator is submitted.
- A shortened-history test hook forces handoff earlier than production thresholds.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST serialize all asynchronous session mutators that can change shared session state, including runtime locator, thread identity, active turn, status, control metadata, and continuity refs.
- **FR-002**: The system MUST gate runtime-bound accepted controls on runtime-handle readiness before using a container, thread, or runtime locator.
- **FR-003**: The system MUST continue to reject invalid mutators deterministically when the session state makes the request unsafe, including stale epochs, missing active turns when a turn is required, duplicate clear operations, and mutators after termination begins.
- **FR-004**: The system MUST wait for accepted asynchronous handlers to finish before the session workflow completes.
- **FR-005**: The system MUST wait for accepted asynchronous handlers to finish before any session workflow handoff to a new run.
- **FR-006**: The system MUST initiate workflow handoff only from the main workflow execution path, not from individual message handlers.
- **FR-007**: The system MUST carry forward the logical session identity, current epoch, runtime locator, last control action, last control reason, latest continuity refs, and any request-tracking state required to prevent duplicate external effects across handoff.
- **FR-008**: The system MUST expose a test-only way to force shortened-history handoff so validation can prove handoff behavior without requiring production-scale histories.
- **FR-009**: The system MUST preserve operator-visible session query state across accepted controls and handoff boundaries.
- **FR-010**: The feature MUST include production runtime behavior changes and validation tests; documentation or specification updates alone are not sufficient.
- **FR-011**: Validation tests MUST cover locking behavior, readiness gating, handler-drain behavior, handoff trigger behavior, and handoff state carry-forward.

### Key Entities *(include if feature involves data)*

- **Managed Session**: A task-scoped runtime session with stable session identity, epoch, status, runtime locator, active turn, and continuity refs.
- **Runtime Locator**: The bounded handle set required to address the active runtime instance, including container and thread identity.
- **Control Request**: A user or workflow-initiated session mutation such as send, steer, interrupt, clear, cancel, or terminate.
- **Continuity Refs**: Artifact references that summarize the latest durable session context, checkpoint, control event, and reset boundary.
- **Handoff Payload**: The bounded state passed from one workflow run to the next when the same logical session continues.
- **Request-Tracking State**: The compact dedupe or idempotency data required to avoid applying the same accepted control more than once across a workflow handoff.

### Assumptions

- The managed session remains task-scoped and uses one active runtime locator for the current epoch.
- Runtime-handle attachment is expected to happen shortly after session launch for sessions that accept runtime-bound controls.
- Handoff preserves bounded operator and recovery metadata; full transcripts, prompts, and large runtime content remain outside the workflow state.
- Existing invalid-request semantics from earlier phases remain in force unless this feature explicitly changes readiness handling.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Concurrent mutator tests demonstrate that no session query state contains mixed locator, epoch, active-turn, status, or continuity values from two different controls.
- **SC-002**: Readiness tests demonstrate that a runtime-bound control accepted before handle attachment completes successfully after handles are attached.
- **SC-003**: Completion and handoff tests demonstrate that accepted asynchronous handlers finish before clients are expected to retrieve final update results.
- **SC-004**: Handoff tests demonstrate that session identity, epoch, runtime locator, latest control metadata, latest continuity refs, and request-tracking state are preserved in the next run payload.
- **SC-005**: Invalid-request tests demonstrate deterministic rejection for stale epoch, missing active turn, duplicate clear, and post-termination mutation cases.
- **SC-006**: The required validation suite passes using the repository's standard unit-test runner for the affected workflow boundary.
