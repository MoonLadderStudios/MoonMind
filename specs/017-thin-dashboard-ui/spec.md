# Feature Specification: Thin Dashboard Task UI

**Feature Branch**: `017-thin-dashboard-ui`  
**Created**: 2026-02-15  
**Status**: Draft  
**Input**: User description: "Implement Strategy 1 thin dashboard UI for MoonMind task submission and monitoring. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Monitor Active Work Across Systems (Priority: P1)

As a MoonMind operator, I want one dashboard view that shows running and queued work across Agent Queue, SpecKit, and Orchestrator so I can monitor current platform activity without switching tools.

**Why this priority**: Observability of in-flight work is the core outcome and prerequisite for efficient operations.

**Independent Test**: Open the consolidated dashboard route, verify it renders data from all three systems, and confirm entries update during polling without page refresh.

**Acceptance Scenarios**:

1. **Given** Agent Queue, SpecKit, and Orchestrator each have active work, **When** the user opens the consolidated tasks page, **Then** the page shows rows for all three sources with source-specific and normalized statuses.
2. **Given** one source temporarily fails to respond, **When** polling runs, **Then** the UI keeps rendering available sources and surfaces a non-blocking error state for the failed source.
3. **Given** active work changes status over time, **When** polling continues, **Then** row statuses and timestamps update without requiring manual page reload.

---

### User Story 2 - Submit New Queue, Workflow, and Orchestrator Runs (Priority: P1)

As an authenticated user, I want to submit Agent Queue jobs, SpecKit runs, and Orchestrator runs from the dashboard so I can start work directly from the UI.

**Why this priority**: Submission is a first-class goal of the strategy and required for end-to-end usability.

**Independent Test**: Use each submit form once, verify request validation behavior, and confirm successful submissions appear in their corresponding list/detail views.

**Acceptance Scenarios**:

1. **Given** a valid queue job payload, **When** the user submits it, **Then** the UI creates the job and navigates to the created job detail.
2. **Given** a valid SpecKit run request, **When** the user submits it, **Then** the UI creates the run and exposes current task state in the detail view.
3. **Given** a valid Orchestrator run request, **When** the user submits it, **Then** the UI creates the run and exposes plan status in the detail view.
4. **Given** an invalid request payload, **When** submission is attempted, **Then** the UI shows actionable validation or API error feedback and does not lose entered form data.

---

### User Story 3 - Inspect Execution Details, Events, and Artifacts (Priority: P2)

As a MoonMind operator, I want detail pages for queue jobs, SpecKit runs, and Orchestrator runs so I can diagnose failures and review outputs.

**Why this priority**: Monitoring needs deep visibility into progress events, artifacts, and run metadata to be operationally useful.

**Independent Test**: Open each detail view and confirm metadata, event/task timeline data, and artifact listings render; verify queue artifact downloads work with auth enforced.

**Acceptance Scenarios**:

1. **Given** a queue job with events and artifacts, **When** the user opens queue detail, **Then** the page renders incremental event history and artifact downloads.
2. **Given** a SpecKit run with task states and artifacts, **When** the user opens run detail, **Then** the page renders task progression and available artifacts.
3. **Given** an Orchestrator run with plan steps and artifacts, **When** the user opens run detail, **Then** the page renders step status history and available artifacts.
4. **Given** an invalid or inaccessible run/job identifier, **When** detail is requested, **Then** the UI shows a clear not-found or authorization error state.

### Edge Cases

- One or more backend source endpoints time out while others respond successfully.
- Polling receives out-of-order timestamps from different systems.
- Queue jobs include `dead_letter` status and must be represented as a failed normalized state.
- A user submits duplicate requests due to retrying after transient network failure.
- Artifact metadata exists but artifact download fails or file is missing.
- Authentication mode differs between local disabled mode and OIDC mode.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a consolidated tasks page that lists active work from Agent Queue, SpecKit, and Orchestrator in one view.
- **FR-002**: System MUST provide separate list pages for Agent Queue jobs, SpecKit runs, and Orchestrator runs.
- **FR-003**: System MUST provide submit forms for Agent Queue jobs, SpecKit runs, and Orchestrator runs.
- **FR-004**: System MUST provide detail pages for queue jobs, SpecKit runs, and Orchestrator runs.
- **FR-005**: System MUST render queue event history with incremental polling semantics so new events appear without full-page reload.
- **FR-006**: System MUST render artifact listings on detail pages and provide authenticated artifact download actions where download endpoints exist.
- **FR-007**: System MUST normalize source-specific statuses into a shared dashboard status model while preserving raw source status labels.
- **FR-008**: System MUST continue partial rendering when one source API fails and expose user-visible error feedback per source.
- **FR-009**: System MUST poll list and detail pages at configurable intervals and pause polling while the browser tab is hidden.
- **FR-010**: System MUST call MoonMind APIs with user authentication context and MUST NOT use worker tokens in dashboard user flows.
- **FR-011**: System MUST preserve submitted form values after failed submission attempts and show actionable validation messages.
- **FR-012**: System MUST include production runtime code changes that implement dashboard routes, data fetching adapters, and UI rendering logic.
- **FR-013**: System MUST include validation tests covering route responses and key UI/data-normalization behavior.
- **FR-014**: Unit test execution for this feature MUST run through `./tools/test_unit.sh`.

### Key Entities *(include if feature involves data)*

- **DashboardRun**: Normalized task record used in the consolidated view, including source, id, normalized status, raw status, and timestamp fields.
- **SubmitRequestModel**: User-entered request payload for each submit form type with validation state and request lifecycle state.
- **SourceHealthState**: Per-source API polling status used to render partial-failure UI.
- **DetailPanelState**: UI state for metadata, timeline/event data, and artifacts for a specific run/job detail page.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can load a consolidated active-work page that includes data from all three systems in one navigation flow.
- **SC-002**: Users can submit all three run/job types from dashboard forms with success and error outcomes clearly visible.
- **SC-003**: Queue detail polling shows newly appended events within one polling cycle without manual refresh.
- **SC-004**: In mixed availability scenarios, at least one healthy source remains visible while failed sources show localized error messaging.
- **SC-005**: Feature validation tests pass via `./tools/test_unit.sh`.

## Assumptions

- Existing API endpoints under `/api/queue/*`, `/api/workflows/speckit/*`, and `/orchestrator/*` remain the backend integration surface for MVP.
- A thin dashboard can be hosted from the current MoonMind API service templates/static mechanism for initial rollout.
- Realtime push protocols are deferred; polling is acceptable for first production release.
