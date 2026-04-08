# Feature Specification: codex-managed-session-plane-phase11

**Feature Branch**: `136-codex-managed-session-plane-phase11`
**Created**: 2026-04-07
**Status**: Draft
**Input**: User description: "Implement Phase 11 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Show session continuity alongside existing step observability (Priority: P1)

Operators need the task detail page to show that a Codex managed-session task reused one session across multiple steps without replacing the existing stdout, stderr, and diagnostics panels.

**Why this priority**: Phase 11 is the first operator-visible proof that the managed session plane exists independently from one step while preserving the Phase 10 observability model.

**Independent Test**: Render the task detail page for a Codex managed-session task run and verify it shows a Session Continuity panel with the current epoch, grouped continuity artifacts, and latest summary/checkpoint/control/reset badges while the step-level logs and diagnostics panels still render normally.

**Acceptance Scenarios**:

1. **Given** a Codex managed-session task run has a session projection, **When** Mission Control loads the task detail page, **Then** the page shows a `Session Continuity` panel populated from `/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}` and still keeps the existing `Live Logs`, `Stdout`, `Stderr`, and `Diagnostics` panels.
2. **Given** the session projection reports `session_epoch > 1` and a latest reset-boundary artifact, **When** the panel renders, **Then** the current epoch boundary is visible to the operator and the latest reset boundary is surfaced as a first-class badge/link instead of being hidden inside raw artifact tables.

### User Story 2 - Let operators send follow-up turns through the managed session plane (Priority: P1)

Operators need a session-level follow-up control that targets the task-scoped Codex session rather than pretending the old generic task controls are sufficient.

**Why this priority**: The UI is incomplete if the Session Continuity panel is read-only; follow-up is the minimum interactive control that proves the UI talks to the session plane.

**Independent Test**: Execute a session follow-up control request and verify MoonMind routes it through the task-scoped session workflow, publishes updated continuity artifacts, and returns the refreshed projection without querying a worker-local Codex process.

**Acceptance Scenarios**:

1. **Given** a task-scoped Codex session exists for a task run, **When** the operator submits a follow-up message from Mission Control, **Then** MoonMind routes the request through the managed session workflow/activity surface and returns a refreshed session projection.
2. **Given** the follow-up control succeeds, **When** the Session Continuity panel refreshes, **Then** the latest summary/checkpoint badges reflect the updated projection and the panel does not depend on a live terminal attachment.

### User Story 3 - Let operators clear/reset the session without hiding the epoch boundary (Priority: P1)

Operators need to reset a reused Codex session from Mission Control and immediately see the new epoch boundary reflected in the session continuity view.

**Why this priority**: Reset/clear is a hard MVP requirement from Phase 7 and must be visible in the UI for the session plane to be operable.

**Independent Test**: Execute a clear/reset control request and verify the session epoch increments, the latest reset-boundary artifact updates, and the Mission Control panel reflects the change after refresh.

**Acceptance Scenarios**:

1. **Given** a Codex managed session is active, **When** the operator clicks `Clear / Reset`, **Then** MoonMind invokes the managed session clear control, increments `session_epoch`, and returns a refreshed projection with the new reset-boundary artifact.
2. **Given** the reset succeeds, **When** the panel rerenders, **Then** the operator can see the new epoch number and latest control/reset badges without leaving the task detail page.

### User Story 4 - Keep cancellation and observability aligned with existing task semantics (Priority: P2)

Operators need the Session Continuity panel to expose cancellation without creating a new terminal-first or session-shell control model.

**Why this priority**: Phase 11 adds UI support, not a new task orchestration model.

**Independent Test**: Render the Session Continuity panel and verify it exposes `Cancel` through the existing task cancellation path while leaving terminal attach/debug shell features absent.

**Acceptance Scenarios**:

1. **Given** the task is still active, **When** the Session Continuity panel renders, **Then** it exposes `Cancel` by reusing the existing execution cancel route rather than a new terminal/session-shell endpoint.
2. **Given** the operator inspects the page after Phase 11, **When** they review the available controls, **Then** there is no terminal attach, debug shell, transcript explorer, or branching UI added by this phase.

## Edge Cases

- The task detail page resolves a Codex task run ID but no managed-session projection exists; the UI must degrade cleanly and not break logs/diagnostics.
- A follow-up or clear/reset control is requested after the task has already reached a terminal state; the server must reject the action instead of mutating stale session state.
- The latest continuity artifacts are partially missing; the UI must render only the badges and grouped artifacts that exist and must not fabricate refs.
- A reset occurs before the page refresh completes; the returned projection must show the updated `session_epoch` and latest reset-boundary ref deterministically.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control task detail MUST load the existing task-run session projection for Codex managed-session task runs and present it in a dedicated `Session Continuity` panel.
- **FR-002**: The `Session Continuity` panel MUST show `session_id`, `session_epoch`, grouped continuity/control/runtime artifacts, and latest summary/checkpoint/control-event/reset-boundary badges when those refs exist.
- **FR-003**: The Session Continuity UI MUST preserve the existing step-level `Live Logs`, `Stdout`, `Stderr`, and `Diagnostics` panels; Phase 11 MUST NOT replace them with a session-only observability surface.
- **FR-004**: MoonMind MUST expose a server-side session control endpoint for Codex managed-session task runs that accepts at least `send_follow_up` and `clear_session`.
- **FR-005**: `send_follow_up` MUST route through the task-scoped managed session workflow/activity contract and MUST refresh continuity artifacts/projection without calling a worker-local Codex subprocess path.
- **FR-006**: `clear_session` MUST invoke the managed session clear control, refresh the durable projection, and surface the new `session_epoch` plus latest reset-boundary artifact to the UI.
- **FR-007**: The Session Continuity panel MUST expose `Cancel` by reusing the existing task cancellation path and MUST NOT introduce terminal attach, PTY embedding, debug shell, transcript explorer, or branch/fork controls.

### Key Entities

- **Task-Run Session Projection**: The Phase 9 read model returned by `/api/task-runs/{taskRunId}/artifact-sessions/{sessionId}` that groups the current runtime, continuity, and control artifacts for one task-scoped session.
- **Task-Run Session Control Request**: The Phase 11 operator request that targets one task-scoped Codex session using `{taskRunId, sessionId, action}` and causes the session workflow to execute a remote managed-session control.
- **Session Continuity Panel**: The Mission Control task-detail UI section that displays session continuity metadata and exposes follow-up, clear/reset, and cancel controls without replacing existing step observability panels.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Backend tests verify the new task-run session control route executes `send_follow_up` and `clear_session` against the task-scoped session workflow and returns the refreshed session projection.
- **SC-002**: Workflow tests verify `MoonMind.AgentSession` can execute follow-up and clear/reset updates through the existing `agent_runtime.*` activity surface while keeping session epoch state authoritative in workflow metadata.
- **SC-003**: Frontend tests verify the task detail page renders a Session Continuity panel with visible epoch/badge metadata and preserves the existing logs/diagnostics panels.
- **SC-004**: Frontend tests verify `Send follow-up`, `Clear / Reset`, and `Cancel` controls behave as intended, with cancel reusing the existing execution cancel route.
