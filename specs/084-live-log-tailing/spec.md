# Feature Specification: Live Log Tailing

**Feature Branch**: `084-live-log-tailing`  
**Created**: 2026-03-17  
**Status**: Draft  
**Input**: User description: "Implement Phase 1 Live Log Tailing from docs/Temporal/LiveTaskManagement.md"

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | §5.1 Concept | The Mission Control task detail page MUST include a collapsible Live Output panel that shows the most recent ~200 lines of terminal output from a running task. |
| DOC-REQ-002 | §5.1 Concept | When enabled, new terminal lines MUST stream in continuously, pushing older lines off the buffer (rolling tail behavior). |
| DOC-REQ-003 | §5.1 Concept | When the panel is collapsed or the browser tab is backgrounded, streaming MUST stop (no background resource usage). |
| DOC-REQ-004 | §5.2 Data Source | The UI MUST embed the tmate `web_ro` URL directly in the detail page — the browser connects to the tmate web viewer with zero additional backend work. |
| DOC-REQ-005 | §5.3 UI Behavior | Default state MUST be panel collapsed with no connection established. |
| DOC-REQ-006 | §5.3 UI Behavior | On toggle open, the UI MUST show a loading indicator while the session connects. |
| DOC-REQ-007 | §5.3 UI Behavior | For terminal (completed) workflows, the UI MUST show "Session ended" with no stream. |
| DOC-REQ-008 | §5.3 UI Behavior | When no session is available (DISABLED or ERROR), the UI MUST show "Live output is not available for this task." |
| DOC-REQ-009 | §5.4 API Contract | The UI MUST use the existing `GET /api/workflows/{id}/live-session` endpoint to obtain the `web_ro` URL. No new API endpoint is required. |
| DOC-REQ-010 | §10.2 Feature Flags | Live log tailing MUST be gated behind a `logStreamingEnabled` feature flag. |
| DOC-REQ-011 | §4.1 Session Lifecycle | The UI MUST handle all session lifecycle states: DISABLED, STARTING, READY, REVOKED, ENDED, ERROR. |
| DOC-REQ-012 | §5.3 UI Behavior | The UI MUST disconnect or pause the stream when the tab loses visibility (via `visibilitychange` event). |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator Views Live Agent Output (Priority: P1)

An operator has submitted a task and wants to see what the agent is doing in real time. They navigate to the task detail page and toggle open the Live Output panel. The panel loads the tmate web RO terminal view, showing the agent's terminal output streaming in real time. Older lines scroll off the top as new output arrives.

**Why this priority**: This is the core value of the feature — giving operators immediate, on-demand visibility into running tasks without leaving Mission Control.

**Independent Test**: Can be fully tested by starting a managed agent task, navigating to its detail page, toggling the Live Output panel, and verifying that terminal output appears and updates in real time.

**Acceptance Scenarios**:

1. **Given** a running task with a live session in READY state, **When** the operator toggles the Live Output panel open, **Then** the tmate web RO terminal view loads and displays the agent's terminal output.
2. **Given** the Live Output panel is open and streaming, **When** the agent writes new output, **Then** the new lines appear in the panel within a few seconds and the view follows the latest output.
3. **Given** the Live Output panel is open and streaming, **When** the operator toggles the panel closed, **Then** the stream connection is terminated and no background resource usage continues.

---

### User Story 2 - Operator Sees Correct State Feedback (Priority: P1)

An operator navigates to a task detail page and sees appropriate feedback based on the live session's lifecycle state. For a session that is starting, they see a loading indicator. For a completed task, they see "Session ended." For a task with no session, they see "Live output is not available."

**Why this priority**: Without proper state feedback, operators would see broken or confusing UI elements when the session is not in the READY state.

**Independent Test**: Can be tested by navigating to tasks in each lifecycle state and verifying the panel shows the correct message.

**Acceptance Scenarios**:

1. **Given** a task with live session in STARTING state, **When** the operator opens the detail page, **Then** the Live Output panel shows a loading indicator.
2. **Given** a task with live session in ENDED state, **When** the operator opens the detail page, **Then** the Live Output panel shows "Session ended" and does not attempt to stream.
3. **Given** a task with no live session (DISABLED) or a session in ERROR state, **When** the operator opens the detail page, **Then** the Live Output panel shows "Live output is not available for this task."
4. **Given** a running session that transitions from STARTING to READY while the panel is open, **When** the session becomes READY, **Then** the panel transitions from the loading indicator to the live terminal view.

---

### User Story 3 - Background Tab Behavior (Priority: P2)

An operator has the Live Output panel open and switches to a different browser tab. When the tab is backgrounded, the stream pauses to conserve resources. When the operator returns to the tab, the stream reconnects and shows the latest output.

**Why this priority**: Prevents wasted bandwidth and resources when the operator is not actively watching.

**Independent Test**: Can be tested by opening the panel, switching tabs, verifying the stream stops, switching back, and verifying the stream resumes.

**Acceptance Scenarios**:

1. **Given** the Live Output panel is open and streaming, **When** the browser tab loses visibility, **Then** the stream connection is paused or disconnected.
2. **Given** the stream was paused due to tab backgrounding, **When** the operator returns to the tab, **Then** the stream reconnects and displays current output.

---

### User Story 4 - Feature Flag Control (Priority: P2)

The platform operator can enable or disable the Live Output panel via the `logStreamingEnabled` feature flag. When disabled, the panel does not appear on the task detail page.

**Why this priority**: Enables gradual rollout and the ability to disable the feature without code changes.

**Independent Test**: Can be tested by toggling the feature flag and verifying the panel appears or disappears.

**Acceptance Scenarios**:

1. **Given** `logStreamingEnabled` is set to `true`, **When** an operator views a task detail page, **Then** the Live Output panel toggle is visible.
2. **Given** `logStreamingEnabled` is set to `false`, **When** an operator views a task detail page, **Then** the Live Output panel toggle is not rendered.

---

### Edge Cases

- What happens when the tmate web RO URL returns a connection error? → Show a user-friendly error message in the panel with a retry option.
- What happens when the session transitions from READY to ENDED while the panel is open? → Show "Session ended" and stop streaming.
- What happens when network connectivity is intermittent? → The tmate web viewer handles reconnection natively; the panel should relay any persistent failure as an error state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST add a collapsible Live Output panel to the task detail page. *(DOC-REQ-001)*
- **FR-002**: The panel MUST embed the tmate `web_ro` URL in an iframe or equivalent browser-native viewer. *(DOC-REQ-004)*
- **FR-003**: The panel MUST default to collapsed with no active connection. *(DOC-REQ-005)*
- **FR-004**: The panel MUST show a loading indicator during the STARTING state. *(DOC-REQ-006)*
- **FR-005**: The panel MUST display "Session ended" for completed/terminal workflows. *(DOC-REQ-007)*
- **FR-006**: The panel MUST display "Live output is not available for this task" for DISABLED or ERROR sessions. *(DOC-REQ-008)*
- **FR-007**: The panel MUST use `GET /api/workflows/{id}/live-session` (or existing equivalent) to retrieve the `web_ro` URL. *(DOC-REQ-009)*
- **FR-008**: The panel MUST disconnect the stream when collapsed. *(DOC-REQ-003)*
- **FR-009**: The panel MUST disconnect or pause the stream when the browser tab loses visibility. *(DOC-REQ-012)*
- **FR-010**: The panel MUST reconnect the stream when the browser tab regains visibility and the panel is open.
- **FR-011**: The feature MUST be gated behind a `logStreamingEnabled` feature flag. *(DOC-REQ-010)*
- **FR-012**: The panel MUST handle all session lifecycle states: DISABLED, STARTING, READY, REVOKED, ENDED, ERROR. *(DOC-REQ-011)*
- **FR-013**: The rolling buffer MUST display approximately the most recent 200 lines. *(DOC-REQ-001, DOC-REQ-002)*
- **FR-014**: The `ManagedRuntimeLauncher` MUST wrap managed agent subprocesses in a headless `tmate` session to generate the `web_ro` URL for log tailing. *(DOC-REQ-004)*
- **FR-015**: The `ManagedRuntimeLauncher` MUST extract the generated `tmate` endpoints and store them in the `TaskRunLiveSession` record associated with the run. *(DOC-REQ-004)*

### Key Entities

- **Live Session**: Per-workflow session record tracking lifecycle state, provider, and connection endpoints (existing entity in `task_run_live_sessions`).
- **Live Output Panel**: New UI component on the task detail page that embeds the terminal viewer.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can view live agent terminal output within 5 seconds of toggling the panel open when the session is READY.
- **SC-002**: The panel correctly renders all 6 session lifecycle states with appropriate user-facing messages.
- **SC-003**: Stream connections are terminated within 2 seconds of the panel being collapsed or the tab being backgrounded.
- **SC-004**: The feature can be fully disabled via the `logStreamingEnabled` flag with no residual UI elements visible.
- **SC-005**: No new API endpoints are required — the feature works entirely with the existing live session endpoint.
