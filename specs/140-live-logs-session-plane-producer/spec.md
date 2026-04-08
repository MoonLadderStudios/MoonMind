# Feature Specification: Live Logs Session Plane Producer

**Feature Branch**: `140-live-logs-session-plane-producer`  
**Created**: 2026-04-08  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 using test-driven development from the Live Logs Session-Aware Implementation Plan. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Session lifecycle controls publish timeline-visible events (Priority: P1)

MoonMind operators need the Codex managed session plane to publish normalized session lifecycle rows into the existing run-global observability stream so resets, resumes, interruptions, and termination are visible in Live Logs without relying on the separate continuity panel.

**Why this priority**: Phase 2 is the contract slice that turns the session plane into a first-class observability producer instead of a separate control surface with hidden state changes.

**Independent Test**: Exercise launch/resume, steer, interrupt, clear, and terminate boundaries through the managed-session controller and adapter, then confirm the emitted session rows are normalized and non-fatal when event publication fails.

**Acceptance Scenarios**:

1. **Given** a task-scoped session already has durable runtime handles, **When** the adapter resumes that session instead of launching a new container, **Then** MoonMind records a `session_resumed` row in the run-global observability stream and signals `resume_session` through the workflow control boundary.
2. **Given** an operator steers or interrupts an active turn, **When** the managed-session controller handles the request, **Then** MoonMind emits normalized `session`-stream rows describing the steering/interruption action and resulting lifecycle transition using the current session snapshot fields.
3. **Given** a session is terminated through the managed-session control surface, **When** the controller finalizes the session, **Then** MoonMind emits a `session_terminated` row instead of hiding the state change as a generic system annotation.
4. **Given** the observability publisher throws while writing a session event, **When** a lifecycle control action still succeeds, **Then** runtime control and durable artifact publication continue without surfacing a false success for the failed event row.

---

### User Story 2 - Session artifact publication becomes visible in the observability timeline (Priority: P1)

Operators need summary and checkpoint publication to appear in the same timeline as stdout, stderr, system, and session lifecycle rows so durable continuity evidence is visible inline with the session story.

**Why this priority**: The timeline remains incomplete if the system writes continuity artifacts but hides their publication from the main observability surface.

**Independent Test**: Publish session artifacts for one managed session and confirm the durable observability history contains `summary_published` and `checkpoint_published` rows with the latest session snapshot and artifact refs.

**Acceptance Scenarios**:

1. **Given** MoonMind publishes a session summary artifact, **When** the managed-session supervisor writes the artifact-backed publication record, **Then** the observability journal includes a `summary_published` row that links to the latest summary ref.
2. **Given** MoonMind publishes a session checkpoint artifact, **When** the same publication path completes, **Then** the observability journal includes a `checkpoint_published` row that links to the latest checkpoint ref.
3. **Given** a completed run later reloads its structured observability history, **When** the timeline is reconstructed, **Then** the publication rows appear in the same shared sequence namespace as stdout/stderr/system/session events.

### Edge Cases

- A session event publication fails while `clear_session` still rotates epoch/thread and reset-boundary artifacts are written; the control action must still succeed.
- A session is resumed from existing runtime handles instead of being launched fresh; the observability row must reflect `resume_session`, not `start_session`.
- Summary/checkpoint publication may happen multiple times across a long-lived session; each publication remains a passive observability fact and must not mutate control behavior.
- Steering a turn may carry arbitrary metadata; observability rows must stay MoonMind-normalized and must not dump raw provider payloads into the timeline.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Codex managed session plane MUST publish normalized `session`-stream observability rows for the stable lifecycle/control boundaries it currently exposes: start, resume, send-turn, steer-turn, interrupt-turn, clear-session, and terminate-session.
- **FR-002**: `clear_session` MUST continue to emit both a passive clear/control row and a dedicated `session_reset_boundary` row carrying the new epoch/thread context.
- **FR-003**: The session-plane boundary MUST emit `session_resumed`, `turn_started`, `turn_completed`, `turn_interrupted`, and `session_terminated` lifecycle rows when those transitions occur.
- **FR-004**: Session summary and checkpoint artifact publication MUST emit `summary_published` and `checkpoint_published` rows in the shared observability sequence.
- **FR-005**: Every emitted session publication row MUST include the latest known session snapshot fields when available.
- **FR-006**: Session-event publication failures MUST be treated as non-fatal best-effort failures and MUST NOT break runtime control actions or durable artifact publication.
- **FR-007**: The workflow adapter MUST mirror `start_session` and `resume_session` through the existing session-control signaling boundary so summary/projection surfaces observe the latest control transition consistently.
- **FR-008**: The Phase 2 slice MUST include automated validation tests for controller publication, supervisor publication, adapter signaling, and non-fatal publication failure behavior.

### Key Entities *(include if feature involves data)*

- **Session Lifecycle Row**: A `RunObservabilityEvent` with `stream="session"` representing one MoonMind-normalized session control or lifecycle fact.
- **Publication Row**: A `RunObservabilityEvent` emitted when MoonMind publishes a session summary or checkpoint artifact.
- **Session Control Signal**: The adapter-to-workflow payload that mirrors `start_session` or `resume_session` through the managed-session control boundary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated tests verify resumed sessions emit a timeline-visible `session_resumed` row and signal `resume_session` without relaunching a container.
- **SC-002**: Automated tests verify steering, interruption, clear, and termination produce normalized session observability rows instead of generic hidden state changes.
- **SC-003**: Automated tests verify session artifact publication writes `summary_published` and `checkpoint_published` rows into durable observability history.
- **SC-004**: Automated tests verify observability publication failures do not break successful control actions or reset-boundary artifact persistence.
