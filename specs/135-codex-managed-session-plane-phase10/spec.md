# Feature Specification: codex-managed-session-plane-phase10

**Feature Branch**: `135-codex-managed-session-plane-phase10`
**Created**: 2026-04-07
**Status**: Draft
**Input**: User description: "Implement Phase 10 of the Codex Managed Session Plane MVP plan using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reuse managed-run observability for session-backed Codex steps (Priority: P1)

Operators need Codex managed-session steps to appear through the existing managed-run observability APIs so they can inspect stdout, stderr, and diagnostics without a new session-only log path.

**Why this priority**: Phase 10 is explicitly about keeping the existing artifact-first observability model even though the runtime now lives in a separate session container.

**Independent Test**: Complete one managed Codex session-backed step and verify MoonMind persists a normal managed-run observability record keyed by the step task run ID.

**Acceptance Scenarios**:

1. **Given** a Codex managed-session step completes and publishes session artifacts, **When** the adapter finalizes the step result, **Then** MoonMind persists a `ManagedRunRecord` for that step run ID with stdout, stderr, and diagnostics artifact refs copied from durable session publication metadata.
2. **Given** the session-backed step wrote a managed-run observability record, **When** `/api/task-runs/{taskRunId}/observability-summary` is requested, **Then** the response comes from the existing observability router and includes the persisted artifact refs and terminal status.

---

### User Story 2 - Keep the observability path artifact-first after the session container is gone (Priority: P1)

Operators need managed-session runs to remain inspectable after the live session container no longer exists.

**Why this priority**: Phase 10 forbids terminal-first or session-liveness-dependent observability for the managed-session path.

**Independent Test**: Persist a session-backed managed-run record with stdout/stderr/diagnostics refs and verify the task-runs observability surface still treats the artifacts as authoritative without consulting live session state.

**Acceptance Scenarios**:

1. **Given** a session-backed managed run already has durable stdout, stderr, and diagnostics refs, **When** the observability summary is loaded later, **Then** MoonMind serves those refs from the persisted managed-run record instead of querying the session container.
2. **Given** a session-backed managed run has no live-follow implementation yet, **When** observability summary is loaded, **Then** the response reports artifact-backed observability only and does not claim live streaming support.

---

### User Story 3 - Avoid introducing terminal-first semantics for the managed-session path (Priority: P2)

Operators should keep using the current MoonMind-native logs and diagnostics surfaces rather than a new embedded shell or session attach path.

**Why this priority**: Phase 10 is specifically the observability reuse slice, not a terminal-attach or live-session UI slice.

**Independent Test**: Inspect the persisted observability summary for a session-backed run and verify it disables live streaming while preserving artifact refs.

**Acceptance Scenarios**:

1. **Given** a completed session-backed managed run, **When** its observability summary is returned, **Then** `supportsLiveStreaming` is `false` and `liveStreamStatus` is `ended`.
2. **Given** a non-terminal session-backed managed run record is surfaced before live-follow exists, **When** observability summary is returned, **Then** `supportsLiveStreaming` is `false` and `liveStreamStatus` is `unavailable`.

## Edge Cases

- Session publication omits one or more runtime artifact refs; MoonMind must not fabricate missing refs.
- The session-backed run completes successfully but has no live stream capability yet.
- The session adapter restarts inside the worker process; the durable managed-run observability record must still survive on disk.
- A canceled or failed session-backed run must still persist terminal status and any available diagnostics ref.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Codex managed-session steps MUST persist a durable `ManagedRunRecord` keyed by the step `taskRunId` used by `/api/task-runs/{taskRunId}/...`.
- **FR-002**: The persisted managed-run record MUST include `workflowId`, `agentId`, `runtimeId`, `status`, `startedAt`, optional `finishedAt`, and any available `stdoutArtifactRef`, `stderrArtifactRef`, and `diagnosticsRef`.
- **FR-003**: The managed-session observability record MUST source runtime artifact refs from durable session publication metadata and MUST NOT depend on a live container query.
- **FR-004**: The existing `/api/task-runs/{taskRunId}/observability-summary` route MUST serve session-backed managed runs without adding a session-specific observability endpoint.
- **FR-005**: Until a later live-follow phase is implemented, session-backed managed runs MUST set `liveStreamCapable` to `false`.
- **FR-006**: Phase 10 MUST preserve artifact-first observability and MUST NOT add terminal attach, PTY embedding, or session-shell semantics for the managed-session path.

### Key Entities

- **Session-Backed Managed Run Record**: The existing managed-run observability record populated for a Codex managed-session step so current task-run APIs can read its artifact refs.
- **Session Publication Metadata**: The durable session artifact publication payload that exposes stdout, stderr, diagnostics, and continuity refs after a step completes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests verify a completed Codex managed-session step writes a `ManagedRunRecord` with stdout/stderr/diagnostics refs sourced from session publication metadata.
- **SC-002**: Tests verify the existing task-runs observability summary route serves that record without session-specific router branching.
- **SC-003**: Tests verify session-backed observability summaries keep live streaming disabled while preserving artifact-first logs and diagnostics.
