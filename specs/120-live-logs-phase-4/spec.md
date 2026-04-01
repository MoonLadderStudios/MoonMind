# Feature Specification: live-logs-phase-4

**Feature Branch**: `120-live-logs-phase-4-affordances`
**Created**: 2026-03-31
**Status**: Draft
**Input**: User description: "Fully implement Phase 4 of docs/tmp/009-LiveLogsPlan.md using test-driven development."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inspect Active Run Logs In Mission Control (Priority: P1)

Operators need the task detail page to show artifact-backed logs immediately and then follow live output when streaming is available, without relying on terminal embedding.

**Why this priority**: This is the core operator workflow for live-log observability and the main user-facing goal of Phase 4.

**Independent Test**: Open a running task detail page, expand Live Logs, confirm the merged artifact tail renders first, then confirm live updates append while the run remains active.

**Acceptance Scenarios**:

1. **Given** a task run with merged log artifacts and live streaming enabled, **When** the operator expands Live Logs, **Then** the page renders the merged tail before subscribing to live updates.
2. **Given** a task run is still active, **When** new log chunks arrive, **Then** the viewer appends them with stream provenance and a visible live-state indicator.
3. **Given** the task run is already terminal or live streaming is unavailable, **When** the operator expands Live Logs, **Then** the page shows artifact-backed content without opening a live connection.

---

### User Story 2 - Inspect Individual Observability Surfaces (Priority: P2)

Operators need separate panels for stdout, stderr, and diagnostics so they can isolate signal without downloading artifacts first.

**Why this priority**: The split panels materially improve troubleshooting, but they build on the core observability surface in User Story 1.

**Independent Test**: Open a task detail page with all observability artifacts present and verify stdout, stderr, and diagnostics each load independently with wrap, copy, and download affordances.

**Acceptance Scenarios**:

1. **Given** stdout, stderr, and diagnostics artifacts exist, **When** the operator expands the respective panels, **Then** each panel loads its own content through the MoonMind observability APIs.
2. **Given** a panel is expanded, **When** the operator toggles wrapping or downloads content, **Then** the panel updates presentation without mutating the underlying artifact data.
3. **Given** clipboard access is unavailable or denied, **When** the operator clicks Copy, **Then** the UI avoids throwing or producing unhandled promise rejections.

---

### User Story 3 - Preserve Stable Connection Lifecycle (Priority: P2)

Operators need the page to avoid unnecessary live connections when the panel is closed or the tab is hidden, and to recover cleanly when visibility returns.

**Why this priority**: Connection lifecycle bugs waste resources and create confusing observability behavior even when the basic viewer works.

**Independent Test**: Expand the Live Logs panel for an active run, background the page or collapse the panel, then restore visibility and confirm the connection closes and reopens only when appropriate.

**Acceptance Scenarios**:

1. **Given** the Live Logs panel is collapsed, **When** the task detail page loads, **Then** it does not fetch observability summary or create a live stream connection until the operator expands the panel.
2. **Given** the panel is expanded for an active run, **When** the operator collapses it or the document becomes hidden, **Then** the current live stream closes promptly.
3. **Given** the document becomes visible again while the run is still active and the panel remains expanded, **When** the page resumes, **Then** it reconnects and continues from the latest available sequence.

### Edge Cases

- What happens when the task detail payload has no `taskRunId` yet?
- How does the page behave when live streaming is disabled but artifact-backed observability still exists?
- How does the viewer avoid rendering spurious blank rows when artifact or SSE payloads end with a trailing newline?
- What happens when clipboard APIs are missing or reject writes in the browser context?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task detail page MUST expose a Live Logs panel that starts collapsed and avoids live-log network work until expanded.
- **FR-002**: The Live Logs panel MUST fetch observability summary and merged-tail artifact content before attempting a live stream subscription.
- **FR-003**: The Live Logs panel MUST surface viewer states for unavailable, starting, live, ended, and error conditions.
- **FR-004**: The Live Logs viewer MUST preserve stream provenance for stdout, stderr, and system lines.
- **FR-005**: The task detail page MUST expose separate stdout, stderr, and diagnostics panels backed by MoonMind observability APIs.
- **FR-006**: The observability panels MUST provide wrap, copy, and download affordances without causing unhandled browser errors when clipboard access is unavailable.
- **FR-007**: The live stream lifecycle MUST stop on collapse or page hide and reconnect only when the panel remains active and the run is still eligible for streaming.

### Key Entities

- **Observability Summary**: API payload describing task-run streaming support, stream status, and run status used to decide viewer behavior.
- **Log Line**: One rendered line in the Live Logs panel, including text plus stream provenance metadata.
- **Task Run Observability Artifact**: Durable stdout, stderr, merged-log, or diagnostics content retrieved independently of live streaming.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can open the task detail page for an active run and see artifact-backed log content before any live stream event arrives.
- **SC-002**: Operators can inspect stdout, stderr, diagnostics, and artifacts from one task detail page without needing a terminal viewer.
- **SC-003**: The Live Logs viewer does not create duplicate blank rows from trailing newlines in artifact or SSE payloads.
- **SC-004**: The task-detail UI tests cover collapsed default behavior, visibility-driven reconnection, ended-run behavior, provenance rendering, and the static observability panels.
