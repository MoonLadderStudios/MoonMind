# Feature Specification: Queue Live Logs + SSE

**Feature Branch**: `023-queue-live-sse`  
**Created**: 2026-02-18  
**Status**: Draft  
**Input**: User description: "Implement live queue output using existing job events, add worker log-chunk emission during command execution, add live output panel on `/tasks/queue/:jobId`, and add SSE endpoint `/api/queue/jobs/{job_id}/events/stream`."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Watch Live Task Output (Priority: P1)

An operator viewing a queue job detail page can watch command output in near real-time while the task runs.

**Why this priority**: Real-time visibility is the core operational value and the direct user ask.

**Independent Test**: Submit a running queue task and verify live log entries appear continuously in queue detail without waiting for artifact upload.

**Acceptance Scenarios**:

1. **Given** a running queue job, **When** the worker emits log chunks, **Then** `/api/queue/jobs/{job_id}/events` returns new `payload.kind="log"` events with stream metadata.
2. **Given** queue detail is open, **When** new log events arrive, **Then** the Live Output panel updates incrementally with optional auto-follow scrolling.

---

### User Story 2 - Stream Events via SSE (Priority: P2)

An operator can use SSE for smoother real-time updates with reduced polling churn.

**Why this priority**: Improves UX and load profile while preserving existing polling behavior.

**Independent Test**: Open an EventSource connection to `/api/queue/jobs/{job_id}/events/stream` and verify incremental event payload delivery.

**Acceptance Scenarios**:

1. **Given** existing events and active job updates, **When** a client connects to `/events/stream`, **Then** it receives initial/new events in `text/event-stream` format.
2. **Given** no new events for the cursor, **When** SSE loop runs, **Then** keepalive/idle cycles do not terminate the connection unexpectedly.

---

### User Story 3 - Control/Filter Live Output (Priority: P3)

An operator can focus on relevant output and copy/download logs efficiently.

**Why this priority**: Improves usability once core live visibility exists.

**Independent Test**: In queue detail, switch filters (`All | Stages | Logs | Warnings/Errors`), toggle follow mode, and copy visible output.

**Acceptance Scenarios**:

1. **Given** mixed stage/log/error events, **When** filters change, **Then** only matching events are rendered in Live Output.
2. **Given** a completed run with artifacts, **When** user requests full logs, **Then** artifact download links remain available from the same detail page.

### Edge Cases

- Very chatty subprocess output should be batched/throttled so event writes do not flood storage.
- Redaction must apply to emitted live log chunks exactly as for persisted logs.
- SSE consumers reconnecting with an `after` cursor should not duplicate already-seen events.
- Jobs with no log events should still show stage timeline and remain functional.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Worker command execution MUST emit incremental queue events for stdout/stderr while commands are running.
- **FR-002**: Log events MUST use append-event payload metadata including `kind=log`, `stream`, and `stage`; `stepId`/`stepIndex` SHOULD be included when available.
- **FR-003**: Worker MUST redact secret-like values before emitting log chunk events.
- **FR-004**: Worker MUST batch/throttle live log event emission with configurable limits to prevent high event-write volume.
- **FR-005**: Live log emission MUST be controlled by an environment/config toggle.
- **FR-006**: API MUST expose `GET /api/queue/jobs/{job_id}/events/stream` as SSE using the same event source as polling.
- **FR-007**: Queue detail UI MUST render a Live Output panel that includes stage + log output entries.
- **FR-008**: Queue detail UI MUST support output filters: `All`, `Stages`, `Logs`, `Warnings/Errors`.
- **FR-009**: Queue detail UI MUST support follow-output toggle and copy-visible-output action.
- **FR-010**: Existing polling endpoint behavior (`GET /events?after=...`) MUST remain backward-compatible.

### Key Entities

- **AgentJobEvent**: Append-only queue event item carrying `level`, `message`, `payload`, and `createdAt`.
- **Live Log Event Payload**: Free-form `payload` object with `kind`, `stream`, `stage`, optional step metadata, and chunk text.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For active queue jobs, new command output appears in UI within 2 seconds under normal local runtime.
- **SC-002**: Live log emission remains bounded by configured batch/throttle values (no per-line event flood).
- **SC-003**: SSE endpoint streams incremental updates for running jobs without requiring database/model schema changes.
- **SC-004**: Unit tests cover worker emission behavior, SSE route behavior, and queue detail rendering/filter behavior.
