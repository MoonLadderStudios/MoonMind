# Feature Specification: Temporal UI Actions and Submission Flags

**Feature Branch**: `071-temporal-ui-flags`  
**Created**: 2026-03-08  
**Status**: Draft  
**Input**: User description: "Implement 5.11 from docs/Temporal/TemporalMigrationPlan.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001**: Set `TEMPORAL_DASHBOARD_ACTIONS_ENABLED=true` by default (or enable the feature).
  - Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 11.
  - Summary: The UI actions feature flag must be enabled to allow operations on Temporal tasks.
- **DOC-REQ-002**: Set `TEMPORAL_DASHBOARD_SUBMIT_ENABLED=true` by default (or enable the feature).
  - Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 11.
  - Summary: The UI task submission feature flag must be enabled to allow creating Temporal tasks.
- **DOC-REQ-003**: Confirm UI buttons appear only when enabled by workflow state and invoke API.
  - Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 11.
  - Summary: UI actions like Pause, Resume, Approve, etc., must be context-aware and call the correct backend APIs.
- **DOC-REQ-004**: Submitting a new task uses Temporal directly.
  - Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 11.
  - Summary: Task creation from `/tasks/new` immediately uses Temporal, preventing duplicates and correctly redirecting to the detail view.
- **DOC-REQ-005**: Provide production runtime code changes and validation tests.
  - Source: User Scope Guard.
  - Summary: The deliverable must not be just documentation, but fully functional runtime code with automated tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enable Temporal UI Task Actions (Priority: P1)

As a Mission Control operator, I want to use UI buttons to manage Temporal workflows (Pause, Resume, Approve, etc.) so that I can directly interact with real-time execution state without legacy database limitations.

**Why this priority**: It is essential for operating in a Temporal-backed system. Without it, operators cannot halt or resume running tasks.

**Independent Test**: Can be tested by navigating to a task detail page for a running workflow and verifying the appropriate action buttons are visible and functional based on its state.

**Acceptance Scenarios**:

1. **Given** a task is in an actionable state (e.g., `awaiting_approval`), **When** the user views the task details, **Then** the "Approve" button is visible and clickable.
2. **Given** a user clicks a valid action button, **When** the action processes successfully, **Then** the workflow receives the corresponding Temporal signal or update without errors.

---

### User Story 2 - Enable Temporal Task Submission (Priority: P1)

As a Mission Control operator, I want to submit new tasks directly to Temporal so that execution begins immediately and correctly registers in the workflow history.

**Why this priority**: This connects the task creation directly to the new Temporal infrastructure, bypassing legacy paths.

**Independent Test**: Can be tested by filling out the `/tasks/new` form and submitting it to verify immediate execution and redirection.

**Acceptance Scenarios**:

1. **Given** a user fills out the new task form, **When** they click submit, **Then** the system launches a Temporal workflow and immediately redirects to the detail view.
2. **Given** a network delay or double click during submission, **When** the request is sent, **Then** idempotency guarantees that no duplicate workflow is created.

### Edge Cases

- What happens when a user attempts an action (like Pause) on a workflow that has just finished?
- How does the system handle failing Temporal connections during submission?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST enable `TEMPORAL_DASHBOARD_ACTIONS_ENABLED` to allow UI actions for Temporal tasks (covers DOC-REQ-001).
- **FR-002**: System MUST enable `TEMPORAL_DASHBOARD_SUBMIT_ENABLED` to allow task submission through Temporal (covers DOC-REQ-002).
- **FR-003**: System MUST conditionally display action buttons (Pause, Resume, Approve) only when the workflow state supports them (covers DOC-REQ-003).
- **FR-004**: System MUST invoke the proper API endpoints that route to Temporal signals/updates upon button clicks (covers DOC-REQ-003).
- **FR-005**: System MUST trigger direct Temporal workflow creation when a new task is submitted via `/tasks/new` (covers DOC-REQ-004).
- **FR-006**: System MUST enforce idempotency during task creation to prevent duplicate workflows from identical submissions (covers DOC-REQ-004).
- **FR-007**: System MUST provide validation tests for the updated runtime logic (covers DOC-REQ-005).

### Key Entities

- **Task Execution**: A representation of the workflow.
- **Task Submission**: The payload and form data used to initiate a workflow.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of newly submitted tasks via `/tasks/new` are orchestrated directly by Temporal.
- **SC-002**: Action buttons accurately map to workflow state, eliminating cases where invalid actions can be selected.
- **SC-003**: Test coverage is present and passes for all newly introduced runtime configurations and paths.
