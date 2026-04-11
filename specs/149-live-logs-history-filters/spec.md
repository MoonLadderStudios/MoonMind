# Feature Specification: Live Logs History Filters

**Feature Branch**: `149-live-logs-history-filters`  
**Created**: 2026-04-11  
**Status**: Draft  
**Input**: User description: "Implement Phase 3 using test-driven development from the Live Logs plan: add first-class structured historical observability retrieval for task runs while preserving the existing summary, merged-tail, and SSE paths. The endpoint must serve ordered RunObservabilityEvent rows from durable event history, support since, limit, stream, kind, sessionEpoch, and threadId filters, return the latest session snapshot with the event history, and keep existing live-log compatibility behavior intact. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Load Structured Observability History (Priority: P1)

Operators need Live Logs to load a structured historical event timeline for a task run so refreshes, reconnects, and completed-run views do not depend on live streaming.

**Why this priority**: This is the core Phase 3 capability. Without a durable structured history source, the session-aware Live Logs experience remains tied to merged text fallbacks and transient live transport.

**Independent Test**: Create or simulate a task run with durable structured observability events and verify the history surface returns ordered events and a session snapshot without opening a live stream.

**Acceptance Scenarios**:

1. **Given** a task run with durable observability events, **When** an operator loads structured history, **Then** the response contains the matching events in chronological order.
2. **Given** a task run has session metadata, **When** structured history is loaded, **Then** the response includes the latest available session snapshot alongside the event rows.
3. **Given** a task run is completed, **When** structured history is loaded, **Then** the event history remains available without requiring SSE or an active run.

---

### User Story 2 - Narrow History To Relevant Session Context (Priority: P2)

Operators need to filter structured history by position, stream, event kind, session epoch, and thread so a timeline can focus on the relevant execution window.

**Why this priority**: Session-aware timelines must distinguish reset boundaries, resumed sessions, and thread changes without forcing clients to download and filter unrelated rows.

**Independent Test**: Create mixed observability rows across streams, kinds, epochs, and threads, then verify each supported filter returns only matching events while preserving ordering and limit semantics.

**Acceptance Scenarios**:

1. **Given** a history contains stdout, stderr, system, and session rows, **When** a stream filter is applied, **Then** only rows from the requested streams are returned.
2. **Given** a history contains multiple event kinds, **When** one or more kind filters are applied, **Then** only matching event kinds are returned.
3. **Given** a history contains multiple session epochs and thread IDs, **When** epoch and thread filters are applied together, **Then** only rows matching both filters are returned.
4. **Given** a caller provides a sequence cursor and a limit, **When** history is loaded, **Then** only rows after the cursor are considered and the response indicates whether more matching rows exist.

---

### User Story 3 - Preserve Existing Live Logs Compatibility (Priority: P3)

Operators need the existing summary, merged-tail, and live stream behavior to keep working while structured history becomes the preferred source for timeline views.

**Why this priority**: Phase 3 must be additive for existing users and frontend rollout paths. Older consumers still rely on summary, merged text, and SSE behavior.

**Independent Test**: Run existing Live Logs summary, merged-tail, and SSE tests together with the structured-history tests and verify no compatibility behavior regresses.

**Acceptance Scenarios**:

1. **Given** a task run supports live streaming, **When** a client uses the existing live stream path, **Then** the live event payload remains compatible with current consumers.
2. **Given** structured history is unavailable for a run, **When** the client requests log content through existing fallback paths, **Then** merged or artifact-backed content remains available as before.
3. **Given** a run is terminal, **When** the summary and live stream paths are used, **Then** summary truthfully reports ended live status and live streaming is not attempted.

### Edge Cases

- A structured event history can be empty while merged or artifact-backed content still exists.
- Some historical rows may lack session-scoped fields; session filters must exclude those rows only when the corresponding filter is requested.
- Invalid, malformed, or partial event rows must not prevent valid rows from being returned.
- Multiple filters can be combined; the returned rows must satisfy all requested filters.
- The response must remain bounded by the requested limit and expose whether the result was truncated.
- Runtime deliverables must include production behavior changes and validation tests, not docs-only updates.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a first-class structured historical observability read path for task runs.
- **FR-002**: The system MUST return normalized observability events with sequence, stream, timestamp, text, and kind fields when available.
- **FR-003**: The system MUST include additive session metadata on event rows when available, including session ID, session epoch, container ID, thread ID, and active turn ID.
- **FR-004**: The system MUST support filtering structured history by sequence cursor, result limit, stream, event kind, session epoch, and thread ID.
- **FR-005**: The system MUST apply combined filters conjunctively so returned rows satisfy every requested filter.
- **FR-006**: The system MUST return the latest available session snapshot with structured history responses.
- **FR-007**: The system MUST preserve existing summary, merged-tail, and live stream behavior while adding structured history.
- **FR-008**: The system MUST keep ended-run behavior truthful: completed or otherwise terminal runs must expose historical content without advertising active live streaming.
- **FR-009**: The system MUST tolerate malformed or unrelated history rows by skipping them without failing the entire history response.
- **FR-010**: The implementation MUST include production runtime code changes and validation tests for the structured-history behavior.

### Key Entities

- **Task Run**: A managed execution record whose observability history, live capability, terminal status, and session snapshot can be inspected.
- **Observability Event**: A normalized timeline row representing output, system annotations, or session lifecycle facts.
- **Session Snapshot**: The latest known session context for a task run, including session identity, epoch, container, thread, active turn, and status details when available.
- **History Filter**: A caller-supplied constraint used to narrow the returned event timeline by cursor, limit, stream, kind, epoch, or thread.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Structured history for a run with persisted events can be loaded successfully without opening a live stream.
- **SC-002**: Filtering by stream, kind, session epoch, and thread ID returns only matching rows in validation tests.
- **SC-003**: Cursor and limit behavior is covered by validation tests, including a truncated response when more matching rows exist than requested.
- **SC-004**: Existing summary, merged-tail, and live stream validation tests continue to pass after the structured-history changes.
- **SC-005**: Completed runs retain useful structured or fallback history while never presenting active live streaming as available.

## Assumptions

- Structured history is the preferred source for session-aware timelines, while merged text remains a compatibility and fallback surface.
- Session epoch and thread filters are optional; callers that do not provide them receive the same results they would have received before those filters existed.
- Runtime behavior is the deliverable for this feature. Documentation-only changes are insufficient for completion.
