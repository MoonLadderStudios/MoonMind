# Feature Specification: Live Logs Phase 2 Active Tail

**Feature Branch**: `148-live-logs-phase2-active-tail`  
**Created**: 2026-04-10  
**Status**: Draft  
**Input**: User description: "Implement Phase 2 using test-driven development from the Live Logs plan: keep the current Live Logs UI and API path, feed it correctly from active Codex managed-session observability, synthesize /logs/merged from the durable observability journal or live spool while runs are active, expose session snapshot fields consistently in observability summary, keep existing SSE lifecycle behavior, avoid blocking the first fix on a frontend rewrite, and preserve fallback behavior for historical and partially migrated runs. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Refresh Shows Active Live Content (Priority: P1)

Mission Control operators need the existing Live Logs panel to show recent output and session rows after a page refresh while a Codex managed-session run is still active, before any final merged artifact exists.

**Why this priority**: The current UI already fetches summary, then merged tail, then optional SSE. The active-run gap is that `/logs/merged` can be blank or artifact-only even though the run has a structured event journal or live spool.

**Independent Test**: Create an active live-capable managed-run record with a workspace event journal containing ordered `stdout`, `system`, and `session` events, request `/api/task-runs/{id}/logs/merged`, and confirm the response contains rendered content in sequence order without requiring final artifacts.

**Acceptance Scenarios**:

1. **Given** an active Codex managed-session run has durable observability journal events, **When** Mission Control requests merged logs, **Then** the response renders journal content in run-global sequence order.
2. **Given** an active run has no final merged artifact but has live spool entries, **When** Mission Control requests merged logs, **Then** the response renders spool-derived content instead of returning an empty body.
3. **Given** a terminal run has final artifacts and structured events, **When** Mission Control requests merged logs, **Then** the structured event history remains preferred for session-aware rows while legacy artifacts remain fallback evidence.

---

### User Story 2 - Summary Carries the Session Snapshot (Priority: P1)

Mission Control operators need `/observability-summary` to consistently expose the latest session identity snapshot for active and terminal Codex managed-session runs so the existing panel can show useful context without a new UI data source.

**Why this priority**: The summary response is already the first request in the Live Logs lifecycle. Consistent session fields let the current panel and later timeline UI share one truth source.

**Independent Test**: Create managed-run records with `sessionId`, `sessionEpoch`, `containerId`, `threadId`, `activeTurnId`, and `observabilityEventsRef`, then confirm `/observability-summary` returns the same values in its session snapshot for active and terminal states.

**Acceptance Scenarios**:

1. **Given** an active live-capable Codex managed-session run has session metadata on its managed-run record, **When** Mission Control requests observability summary, **Then** the response includes a populated session snapshot and the durable observability events reference.
2. **Given** the run has ended, **When** Mission Control requests observability summary, **Then** the response still includes the final session snapshot while reporting live streaming as ended.
3. **Given** some optional session fields are unavailable, **When** the summary is generated, **Then** the response omits or nulls only those fields without dropping the rest of the snapshot.

---

### User Story 3 - Current SSE Lifecycle Remains Truthful (Priority: P2)

Mission Control operators need the existing SSE stream behavior to remain unchanged while active-history fallback improves: active capable runs may stream, active incapable runs are rejected as unavailable, and terminal runs are treated as ended.

**Why this priority**: The first fix must feed the current UI path, not replace it. Regressing stream status would make the richer merged fallback unreliable.

**Independent Test**: Exercise `/logs/stream` and summary routes for active capable, active incapable, and terminal records while journal/spool content exists, and confirm status codes and live flags remain truthful.

**Acceptance Scenarios**:

1. **Given** an active live-capable run has journal or spool content, **When** Mission Control opens the SSE stream, **Then** the stream endpoint remains available and resume-by-sequence semantics are preserved.
2. **Given** an active run is not live-stream capable, **When** Mission Control opens the SSE stream, **Then** the endpoint rejects the request as live streaming unavailable.
3. **Given** a terminal run has historical content, **When** Mission Control requests summary or attempts SSE, **Then** summary reports live streaming ended and the stream endpoint rejects live follow as ended.

### Edge Cases

- An active run may have an event journal reference but the journal file is missing or unreadable; merged logs must continue through spool or artifact fallbacks.
- A spool may contain blank, malformed, or partially written rows; merged logs must skip invalid rows and render valid rows without failing the whole request.
- Session events may use additive fields not rendered by older clients; merged text must preserve the user-visible event text and stream/kind labels without requiring frontend changes.
- Historical final artifacts may exist for older runs that do not have structured events; merged logs must keep the legacy artifact fallback working.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `/api/task-runs/{id}/logs/merged` MUST prefer structured observability journal events when available and render them as human-readable merged log text.
- **FR-002**: `/api/task-runs/{id}/logs/merged` MUST render live spool content for active runs when structured journal content is unavailable and no final merged artifact exists.
- **FR-003**: `/api/task-runs/{id}/logs/merged` MUST keep existing final merged, stdout, and stderr artifact fallbacks for terminal and partially migrated runs.
- **FR-004**: Merged log rendering MUST order structured and spool-derived rows by run-global sequence when sequence values are available.
- **FR-005**: `/api/task-runs/{id}/observability-summary` MUST expose the latest session snapshot fields from the managed-run record when present: `sessionId`, `sessionEpoch`, `containerId`, `threadId`, and `activeTurnId`.
- **FR-006**: `/api/task-runs/{id}/observability-summary` MUST expose `observabilityEventsRef` when the managed-run record has one.
- **FR-007**: The active-history changes MUST NOT change the existing SSE availability contract: active capable runs can stream, active incapable runs are unavailable, and terminal runs are ended.
- **FR-008**: Invalid journal or spool rows MUST be ignored without failing the entire merged-log response when at least one valid fallback source exists.
- **FR-009**: Required deliverables MUST include production runtime code changes (not docs/spec-only) plus validation tests.

### Key Entities *(include if feature involves data)*

- **Observability Event Journal**: Durable JSONL history for normalized run observability events, including output, system, and session rows.
- **Live Spool**: Workspace-local append-only transport used by the active SSE path and by active merged-tail fallback when the journal is not available.
- **Merged Log View**: Human-readable text projection consumed by the existing UI before optional SSE attachment.
- **Session Snapshot**: Bounded managed-session identity fields shown in summary responses and later timeline headers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Router tests prove `/logs/merged` returns journal-rendered content for an active run before final artifacts exist.
- **SC-002**: Router tests prove `/logs/merged` returns spool-rendered content for an active run when the journal is absent.
- **SC-003**: Router tests prove `/observability-summary` includes `observabilityEventsRef` and session snapshot fields for active and terminal runs.
- **SC-004**: Router tests prove active capable, active incapable, and terminal SSE behavior is unchanged.
- **SC-005**: `./tools/test_unit.sh tests/unit/api/routers/test_task_runs.py` passes for the final implementation.
