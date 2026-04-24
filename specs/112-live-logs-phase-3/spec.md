# Feature Specification: Live Logs Phase 3

**Feature Branch**: `112-live-logs-phase-3`
**Created**: 2026-03-29
**Status**: Draft
**Input**: User description: "Fully implement Phase 3 from docs/ManagedAgents/LiveLogs.md"

## Source Document Requirements

- **DOC-REQ-001**: Use Server-Sent Events (SSE) over `text/event-stream` for live log delivery. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-002**: Implement a live log publisher fanning out log chunks to subscribers. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-003**: Emitted log records must include monotonically increasing sequence values, `stream`, `offset`, `timestamp`, and raw `text`. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-004**: Expose a `GET /api/task-runs/{id}/logs/stream` endpoint supporting resume semantics (`since`) and stream filtering. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-005**: Closed or collapsed clients must promptly stop receiving updates; disconnects should cleanly resume state. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-006**: Stream lifecycle metadata must be reflected in the task run observability summary. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-007**: Must gracefully fallback to artifact-backed tail retrieval when live streaming is unavailable or unsupported. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)
- **DOC-REQ-008**: Supervisor/system events must be clearly identified as `system` entries in the merged stream. (Source: `docs/ManagedAgents/LiveLogs.md` Phase 3 Tasks)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Live Streaming Connection (Priority: P1)

Operators can open a live stream for an active run and receive appended log records seamlessly in real-time.

**Why this priority**: Real-time log visibility is the core value proposition of Phase 3, allowing operators to monitor in-progress managed runs without relying on terminal infrastructure.

**Independent Test**: Can be independently tested by starting a continuous output task and verifying that the `GET /api/task-runs/{id}/logs/stream` endpoint emits proper SSE chunks as the task writes to stdout/stderr.

**Acceptance Scenarios**:
1. **Given** an active managed task run, **When** a client connects to the logs stream endpoint, **Then** the client receives real-time log chunk payloads containing `sequence`, `text`, `stream`, `offset`, and `timestamp`.
2. **Given** multiple clients observing the same run, **When** the task produces logs, **Then** all connected clients receive the identical log chunks via fan-out publish.

---

### User Story 2 - Resilient Streaming & Disconnects (Priority: P1)

Reconnect-from-last-sequence behavior works reliably enough for normal browser refreshes and short disconnects.

**Why this priority**: Networks are unreliable, and operators may refresh their tabs. Ensuring the log stream reconstructs cleanly without missing data guarantees trust in the observability layer.

**Independent Test**: Can be tested by forcing a client disconnect, noting the last sequence number, and reconnecting with `since=<sequence>`, verifying no missed logs.

**Acceptance Scenarios**:
1. **Given** a client reading a stream, **When** the network drops and the client reconnects with `since=<sequence>`, **Then** the server resumes exactly from the requested sequence.
2. **Given** a connected client, **When** the client closes the connection, **Then** the server immediately stops fanning out and releases subscriber resources.

---

### User Story 3 - Graceful Artifact Fallback (Priority: P2)

When streaming is unavailable or the run is ended, the system seamlessly falls back to artifact-backed retrieval.

**Why this priority**: Handles edge cases where real-time pub/sub isn't viable or historical review is required. 

**Independent Test**: Can be tested on a completed check where attempting to stream immediately exits or returns a fallback indicator, routing the caller to standard artifact tail ends.

**Acceptance Scenarios**:
1. **Given** a run that has already completed, **When** a streaming connection is requested, **Then** it either closes gracefully indicating end-of-stream or directs the caller to the static artifact tail.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a Server-Sent Events (SSE) `/api/task-runs/{id}/logs/stream` GET endpoint. (Mappings: DOC-REQ-001, DOC-REQ-004)
- **FR-002**: System MUST implement an internal memory/Redis channel publisher that fans out incoming task subprocess logs to active SSE subscribers. (Mappings: DOC-REQ-002)
- **FR-003**: System MUST structure SSE payload events such that they include a `sequence` (integer), `stream` (e.g. `stdout`, `stderr`, `system`), `offset` (byte/line position), `timestamp` (ISO8601), and the raw log string `text`. (Mappings: DOC-REQ-003, DOC-REQ-008)
- **FR-004**: System MUST parse the `since` query-string parameter to resume stream delivery from a given sequence number. (Mappings: DOC-REQ-004, DOC-REQ-005)
- **FR-005**: System MUST automatically cleanup inactive client connections and remove them from the publisher subscriber pool. (Mappings: DOC-REQ-005)
- **FR-006**: System MUST update the Observability summary record upon stream startup and shutdown, updating stream lifecycle metadata. (Mappings: DOC-REQ-006)
- **FR-007**: System MUST provide deterministic capability indication to clients when streams are unavailable (allowing for artifact retrieval fallback). (Mappings: DOC-REQ-007)

### Key Entities 

- **LogStreamEvent**: DTO containing `sequence`, `stream` (out/err/sys), `offset`, `text`, and `timestamp`.
- **LogPublisher**: The internal service holding active connections per task-run, tracking sequence history bounds.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Real-time log chunks are delivered to active Server-Sent Event clients within 500ms of generation.
- **SC-002**: Server supports at least 50 concurrent SSE subscribers per active task run without noticeable latency degradation.
- **SC-003**: Client reconnect requests with `since=<sequence>` successfully reconstruct missing log gaps (assuming retention buffers have not rolled over).
- **SC-004**: System cleanly reclaims memory when an SSE client disconnects, avoiding connection leaks.
