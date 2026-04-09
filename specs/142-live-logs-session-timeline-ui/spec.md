# Feature Specification: Live Logs Session Timeline UI

**Feature Branch**: `142-live-logs-session-timeline-ui`  
**Created**: 2026-04-09  
**Status**: Draft  
**Input**: User description: "Implement Phase 4 using test-driven development from the Live Logs Session-Aware Implementation Plan."

## User Scenarios & Testing

### User Story 1 - Load the Live Logs panel as a session-aware timeline (Priority: P1)

Mission Control operators need the Live Logs panel to prefer structured observability history and render a unified timeline instead of treating the primary experience as a merged text tail.

**Why this priority**: This is the Phase 4 product shift. Without it, the backend Phase 1-3 contract remains underused and Live Logs still behaves like a line viewer.

**Independent Test**: Open Live Logs for a managed run that has structured history and verify the page loads summary first, then structured event history, and only falls back to merged text when structured history is unavailable.

**Acceptance Scenarios**:

1. **Given** observability summary and structured history exist for a task run, **When** the operator opens Live Logs, **Then** the UI requests summary first and structured history second before attempting SSE follow mode.
2. **Given** structured history is unavailable for an older run, **When** the operator opens Live Logs, **Then** the UI degrades to merged artifact-backed content without breaking the existing operator workflow.
3. **Given** the run is active, visible, and stream-capable, **When** the historical content is loaded, **Then** the UI attaches SSE follow mode without replacing the already-rendered historical timeline.

### User Story 2 - Render session-aware timeline rows and header context (Priority: P1)

Operators need distinct rendering for stdout, stderr, system, and session lifecycle events plus a compact session snapshot header so resets and turn changes are understandable without opening a separate continuity panel first.

**Why this priority**: The desired-state Live Logs experience is continuity-aware observability, not a plain merged tail.

**Independent Test**: Render a timeline containing mixed output, system, session lifecycle, approval, and reset-boundary rows and verify the header and row types expose session identity and timeline semantics clearly.

**Acceptance Scenarios**:

1. **Given** the observability summary or history response includes a session snapshot, **When** the panel renders, **Then** the header shows session ID, epoch, container ID, thread ID, active turn ID, and live status when present.
2. **Given** the timeline contains `stdout`, `stderr`, `system`, `session`, approval, publication, and `session_reset_boundary` rows, **When** the panel renders, **Then** each row uses distinct timeline treatment and reset boundaries appear as obvious banners.
3. **Given** session continuity artifacts still exist separately, **When** the operator reads Live Logs, **Then** the main timeline carries the important session milestones without requiring continuity drill-down to notice them.

### User Story 3 - Harden the viewer for large logs with virtualization and ANSI rendering (Priority: P2)

Operators need the session-aware timeline viewer to remain responsive on long histories and preserve ANSI formatting for output rows so the panel scales beyond the current naive DOM growth.

**Why this priority**: The desired-state architecture explicitly calls for `react-virtuoso` and `anser` as the long-term viewer baseline.

**Independent Test**: Render the timeline viewer with enough mixed rows to require virtualization semantics and ANSI-colored output, then verify the panel still shows the expected content and styling markers through the React test harness.

**Acceptance Scenarios**:

1. **Given** a large timeline, **When** the panel renders, **Then** the list is owned by a virtualized viewer rather than direct unbounded row mapping.
2. **Given** a `stdout` or `stderr` row contains ANSI escape sequences, **When** the panel renders, **Then** the visible text is preserved and ANSI spans are parsed into styled fragments rather than raw escape codes.
3. **Given** the session-aware timeline flag is disabled, **When** the operator opens Live Logs, **Then** the existing line-oriented behavior remains available during rollout.

### Edge Cases

- Structured history may be present but empty while merged artifacts still contain legacy text; the UI should still use the documented fallback order truthfully.
- SSE events may arrive before or after history finishes; the viewer must avoid duplicated rows and keep sequence ordering stable.
- Session metadata may appear in history rows even when the summary snapshot is absent; the panel should still surface the most recent bounded session context it can derive.
- Older runs may expose only merged text and no session rows; the panel must remain usable without fabricating session lifecycle events.
- Mixed ANSI and plain-text rows must render without leaking raw escape sequences into copied or visible output.

## Requirements

### Functional Requirements

- **FR-001**: The task-detail Live Logs panel MUST keep the existing lifecycle of summary first, historical content second, and SSE follow only when the panel is open, visible, and the run is active.
- **FR-002**: The Live Logs panel MUST prefer `/api/task-runs/{id}/observability/events` for initial history loading and MUST fall back to `/logs/merged` only when structured history is unavailable.
- **FR-003**: The Live Logs panel MUST render a unified timeline model that can represent `stdout`, `stderr`, `system`, and `session` events plus reset boundaries and other timeline-specific row types.
- **FR-004**: The Live Logs header MUST show the latest available session snapshot fields, including session ID, epoch, container ID, thread ID, active turn ID, and live status when present.
- **FR-005**: `session_reset_boundary` events MUST render as explicit boundary banners instead of plain line rows.
- **FR-006**: The viewer MUST preserve stream provenance and visually differentiate output, system, session lifecycle, approval/publication, and reset-boundary rows.
- **FR-007**: The task-detail frontend MUST adopt `react-virtuoso` for the main Live Logs timeline renderer.
- **FR-008**: The task-detail frontend MUST adopt `anser` for ANSI-aware output rendering in timeline rows.
- **FR-009**: The session-aware timeline UI MUST remain gated by the existing `liveLogsSessionTimelineEnabled` runtime-config feature flag so rollout can degrade to the prior line viewer.
- **FR-010**: This phase MUST ship production frontend/runtime code changes plus automated validation tests; docs-only changes are insufficient.

### Key Entities

- **Timeline Row View Model**: The frontend-owned normalized row model used by the Live Logs viewer for output, system, session, approval, publication, and boundary rows.
- **Session Snapshot Header**: The compact UI summary of the latest bounded session identity shown above the timeline.
- **Structured History Source**: The canonical initial-load source from `/observability/events`.
- **Merged Tail Fallback**: The artifact-backed compatibility path used only when structured history is unavailable.

## Success Criteria

### Measurable Outcomes

- **SC-001**: UI tests prove the panel loads summary first, then structured history, and only falls back to merged text when the structured history path is unavailable.
- **SC-002**: UI tests prove mixed `stdout`, `stderr`, `system`, `session`, approval/publication, and reset-boundary rows render in one timeline with a session snapshot header.
- **SC-003**: UI tests prove the session-aware path is feature-flagged and the viewer degrades to the legacy line view when the flag is disabled.
- **SC-004**: `npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx`, `npm run ui:typecheck`, and `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx` pass.
