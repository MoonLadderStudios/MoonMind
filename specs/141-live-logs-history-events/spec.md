# Feature Specification: Live Logs History Events

**Feature Branch**: `141-live-logs-history-events`  
**Created**: 2026-04-08  
**Status**: Draft  
**Input**: User description: "Implement Phase 3 using test-driven development from the Live Logs plan. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Query durable observability history for the timeline (Priority: P1)

MoonMind operators and the Live Logs viewer need a structured historical timeline endpoint that can load durable session-aware observability rows for completed and active runs without depending on live SSE.

**Why this priority**: Phase 3 exists so the UI can load a truthful timeline from durable history first. Without a real historical query contract, the session-aware timeline still depends on transient live transport.

**Independent Test**: Request structured observability history for one run with mixed stdout, stderr, system, and session rows, then verify the API returns correctly filtered, limited, ordered rows from durable history or the documented fallback sources.

**Acceptance Scenarios**:

1. **Given** a managed run has a persisted observability event journal, **When** the client requests historical events, **Then** MoonMind returns session-aware timeline rows from that durable history instead of requiring live transport.
2. **Given** the client supplies `since`, `limit`, stream filters, or kind filters, **When** MoonMind loads historical observability events, **Then** the response includes only the matching rows in the shared run timeline order.
3. **Given** a historical run does not yet have a structured observability journal, **When** the client requests historical events, **Then** MoonMind degrades through the existing spool and artifact-backed history sources without fabricating missing session facts.

---

### User Story 2 - Keep summary and live streaming aligned with the same observability contract (Priority: P1)

Operators need `/observability-summary` and `/logs/stream` to stay aligned with the same session-aware observability model so the panel header and live follow path remain truthful while historical loading moves to structured events first.

**Why this priority**: The historical endpoint is not sufficient if summary and SSE still drift from the session-aware contract or misreport live capability for ended and non-stream-capable runs.

**Independent Test**: Load observability summary for active and completed runs, then attach to live SSE for an active run and verify both surfaces use the same normalized event/session fields and truthful live-stream status.

**Acceptance Scenarios**:

1. **Given** a managed run record or session projection contains the latest bounded session identity, **When** `/observability-summary` is fetched, **Then** the response includes the latest session snapshot fields needed by the timeline header.
2. **Given** a run is terminal or does not support live follow, **When** `/observability-summary` is fetched, **Then** the response truthfully reports that live streaming is ended or unavailable rather than implying that SSE is still authoritative.
3. **Given** `/logs/stream` emits live rows for an active run, **When** the browser receives those rows, **Then** the payload shape matches the canonical observability event contract used by historical retrieval.

---

### User Story 3 - Preserve compatibility and fallback ordering across observability surfaces (Priority: P2)

Current Mission Control consumers need the structured history endpoint to land without breaking the existing merged-log fallback, task-run authorization model, or durable continuity drill-down behavior for older runs.

**Why this priority**: Phase 3 is an additive backend upgrade. It must not strand historical runs or force a frontend cutover before the session-aware timeline renderer is fully complete.

**Independent Test**: Exercise summary, structured history, merged logs, and authorization behavior together for runs with and without event journals and verify the API follows the documented fallback order.

**Acceptance Scenarios**:

1. **Given** the client can read structured history for a run, **When** historical content is available from `/observability/events`, **Then** the UI does not need to parse merged text as the primary timeline source.
2. **Given** structured history is missing or partial, **When** the client falls back to `/logs/merged` and continuity drill-down, **Then** the run remains observable without breaking existing merged-tail behavior.
3. **Given** the requesting user already has access to the task run, **When** they request summary, structured history, or merged logs, **Then** MoonMind authorizes those surfaces consistently for the same run.

### Edge Cases

- A run has a durable event journal plus a newer live spool file; historical requests must prefer the durable journal rather than mixing sources out of order.
- `since` is provided for a run with sparse or non-contiguous sequence numbers; the API must filter correctly without assuming continuous numbering.
- Stream and kind filters are combined in one request; only rows matching all provided filters should be returned.
- A run has only merged stdout/stderr artifacts and no structured journal or continuity projection; the API must degrade cleanly instead of returning fabricated session events.
- A run has record-backed session snapshot fields but no managed-session store record; summary must still expose the latest bounded session identity from the durable run record.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST provide a structured historical observability endpoint at `/api/task-runs/{id}/observability/events` for managed runs.
- **FR-002**: The structured historical observability endpoint MUST support `since` and `limit` query parameters.
- **FR-003**: The structured historical observability endpoint MUST support optional filtering by stream and by event kind.
- **FR-004**: Historical observability retrieval MUST prefer durable structured event history when available and otherwise degrade through existing spool or artifact-backed sources without breaking current run observability.
- **FR-005**: `/observability-summary` MUST include the latest session snapshot fields when present from durable session or run metadata.
- **FR-006**: `/observability-summary` MUST report live-stream capability truthfully for active, ended, and non-stream-capable runs.
- **FR-007**: `/logs/stream` MUST continue to emit the same canonical observability event shape used by structured historical retrieval.
- **FR-008**: `/logs/merged` MUST remain available as a human-readable fallback surface during the Phase 3 rollout.
- **FR-009**: Summary, structured history, merged logs, and live streaming MUST continue to enforce the same task-run observability authorization rules.
- **FR-010**: The Phase 3 slice MUST include production runtime code changes plus automated validation tests covering summary, structured history, and SSE compatibility together.

### Key Entities *(include if feature involves data)*

- **Historical Observability Query**: A task-run-scoped request for structured timeline rows with pagination and optional stream/kind filters.
- **Session Snapshot**: The latest bounded session identity attached to observability summary and historical retrieval responses.
- **Canonical Observability Event**: The normalized event row shared by historical retrieval and live SSE payloads across stdout, stderr, system, and session streams.
- **Fallback Order**: The documented retrieval priority of structured history first, merged log fallback second, and continuity drill-down when historical session facts are unavailable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated tests verify `/observability/events` returns correctly ordered historical rows from durable event history and supports `since`, `limit`, stream filters, and kind filters.
- **SC-002**: Automated tests verify `/observability/events` degrades through current spool or artifact-backed fallback behavior when no event journal is present.
- **SC-003**: Automated tests verify `/observability-summary` exposes the latest session snapshot and truthful live-stream status for active and completed runs.
- **SC-004**: Automated tests verify `/logs/stream` remains compatible with the canonical observability event contract used by historical retrieval.
