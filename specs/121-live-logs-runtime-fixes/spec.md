# Feature Specification: live-logs-runtime-fixes

**Feature Branch**: `120-live-logs-phase-4-affordances`
**Created**: 2026-03-31
**Status**: Draft
**Input**: User description: "Address the findings and update the docs"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Live Follow Works For Real Managed Runs (Priority: P1)

Operators need the runtime and API contracts to advertise live streaming only when a real managed run can actually be followed from Mission Control.

**Why this priority**: The Phase 4 UI cannot function correctly if the backend never marks real runs as stream-capable.

**Independent Test**: Launch a managed run record through the real launcher/supervisor path, fetch observability summary, and confirm the summary exposes live-stream support while the run is active.

**Acceptance Scenarios**:

1. **Given** a newly launched managed run with the MoonMind log spool path available, **When** the launcher persists the run record, **Then** the record marks live streaming as supported for that run.
2. **Given** an active run with live streaming support, **When** the client fetches `/api/task-runs/{id}/observability-summary`, **Then** the response reports `supportsLiveStreaming: true` and `liveStreamStatus: "available"`.
3. **Given** a terminal run, **When** the client fetches the observability summary, **Then** the response suppresses live connectivity even if the run was stream-capable while active.

---

### User Story 2 - Reconnect Uses Correct Global Ordering (Priority: P1)

Operators need reconnect and artifact fallback to preserve merged log ordering across stdout, stderr, and system events.

**Why this priority**: Per-stream counters break resume semantics and can lose or reorder live output after refreshes or reconnects.

**Independent Test**: Emit interleaved stdout/stderr chunks, reconnect from the latest observed sequence, and confirm the resumed stream skips only previously delivered events.

**Acceptance Scenarios**:

1. **Given** interleaved stdout and stderr chunks, **When** the runtime emits live records, **Then** each chunk receives one run-global monotonically increasing sequence value.
2. **Given** a client reconnects with `since=<last-sequence>`, **When** the API resumes from the spool, **Then** only newer chunks are delivered regardless of stream origin.
3. **Given** a merged artifact is synthesized from separate stdout/stderr sources, **When** the merged endpoint responds, **Then** lines are returned in emit order rather than all stdout followed by all stderr.

---

### User Story 3 - Docs And Rollout Flags Match The Live System (Priority: P2)

Operators and contributors need the docs and runtime flag names to describe the actual implementation so rollout and troubleshooting stay reliable.

**Why this priority**: A mismatched flag name or overstated plan status turns operations and future implementation into guesswork.

**Independent Test**: Read the updated docs and runtime config, then verify the same feature-flag name controls the observability panel in Mission Control.

**Acceptance Scenarios**:

1. **Given** the dashboard runtime config is generated, **When** Mission Control reads feature flags, **Then** the observability panel is gated by `logStreamingEnabled`.
2. **Given** the canonical live-logs doc describes the merged-tail and reconnect contracts, **When** a contributor reads it, **Then** those contracts match the runtime implementation after this fix.
3. **Given** the tmp implementation plan tracks phase status, **When** a contributor reads it, **Then** incomplete or newly remediated work is described honestly rather than marked complete prematurely.

### Edge Cases

- What happens when the spool file exists but the run has already become terminal before the client connects?
- How does merged-tail synthesis behave when one stream artifact is missing or shorter than the other?
- How does the API behave when a historical record lacks `liveStreamCapable`?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Managed run launch or supervision MUST persist `liveStreamCapable` accurately for real live-stream-capable runs.
- **FR-002**: Live log chunks MUST use one run-global monotonically increasing `sequence` namespace across stdout, stderr, and system events.
- **FR-003**: The SSE resume contract MUST use that global sequence namespace so reconnects do not skip unseen chunks from another stream.
- **FR-004**: When `merged_log_artifact_ref` is absent, the merged-log retrieval path MUST synthesize merged output in emit order instead of concatenating all stdout before all stderr.
- **FR-005**: Mission Control runtime config and docs MUST use `logStreamingEnabled` as the canonical feature-flag name for the observability panel.
- **FR-006**: Canonical and tmp live-logs docs MUST describe the corrected implementation state and must not claim incomplete runtime work is already complete.

### Key Entities

- **Managed Run Record**: Durable record for one managed run, including live-stream capability metadata.
- **Live Log Chunk**: One emitted live-log payload identified by a run-global sequence.
- **Merged Log View**: Artifact-backed or synthesized chronological view across stdout, stderr, and system output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Active managed runs created through the real runtime path expose `supportsLiveStreaming: true` in observability summary when live streaming is available.
- **SC-002**: Interleaved multi-stream live-log tests confirm reconnect by `since` skips no unseen chunks and preserves ordering.
- **SC-003**: The merged-log API no longer returns a naive stdout-then-stderr concatenation when synthesizing from separate artifacts.
- **SC-004**: The docs and dashboard config consistently refer to `logStreamingEnabled`.
