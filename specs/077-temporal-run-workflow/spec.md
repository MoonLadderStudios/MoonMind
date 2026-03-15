# Feature Specification: Temporal Run Workflow

**Feature Branch**: `001-temporal-run-workflow`  
**Created**: 2026-03-08  
**Status**: Draft  
**Input**: User description: Implement MoonMind.Run workflow. Starting a new 'Run' via API creates a real Temporal execution. History shows phases (initializing, planning, executing, etc.). Terminal success/fail closes with correct status. Search attributes visible. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

## Clarifications
### Session 2026-03-08
- Q: Are UI dashboard updates or local DB syncs in scope for this specific feature branch? → A: No. This branch strictly implements the real Temporal execution and its history phases. API/DB integration and UI are out of scope.
- Q: How should workflow timeouts and activity failures be handled during planning/executing? → A: Activities must use Temporal RetryPolicies with exponential backoff. Workflow fails on terminal errors.
- Q: Should large payloads be stored in Temporal history? → A: No. Large payloads must be offloaded to the artifact store, with only references stored in workflow history.

- **DOC-REQ-001**: Source: docs/Temporal/TemporalMigrationPlan.md section 5.2. Summary: Starting a new "Run" via API must create a real Temporal execution.
- **DOC-REQ-002**: Source: docs/Temporal/TemporalMigrationPlan.md section 5.2. Summary: Workflow history must reflect all execution phases including initializing, planning, executing, and finalizing.
- **DOC-REQ-003**: Source: docs/Temporal/TemporalMigrationPlan.md section 5.2. Summary: Workflow must close with the correct terminal status upon success or failure.
- **DOC-REQ-004**: Source: docs/Temporal/TemporalMigrationPlan.md section 5.2. Summary: Search attributes must be populated and visible in Temporal.
- **DOC-REQ-005**: Source: Scope Guard. Summary: Required deliverables must include production runtime code changes and validation tests, not just documentation.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create and Execute Run Workflow (Priority: P1)

As a system user or operator, I need the "Run" API to trigger a real Temporal workflow so that the execution is durable, observable, and fully orchestrated by Temporal.

**Why this priority**: Core functionality that replaces the legacy local database state machine with the authoritative Temporal execution engine.

**Independent Test**: Can be independently tested by triggering a new run via the API and observing a new execution appearing in the Temporal Web UI with the correct phases and terminal status.

**Acceptance Scenarios**:

1. **Given** the MoonMind system is running with Temporal workers enabled, **When** a user submits a new Run task via the API, **Then** a corresponding Temporal workflow execution (type `MoonMind.Run`) is started and assigned a Temporal Run ID.
2. **Given** an actively running `MoonMind.Run` workflow, **When** the workflow progresses through its logic, **Then** the Temporal history shows distinct phases (initializing, planning, executing, etc.).
3. **Given** a `MoonMind.Run` workflow completes its work or encounters a fatal error, **When** it finishes, **Then** the workflow closes with the appropriate terminal status (e.g., Completed, Failed).
4. **Given** a deployed Temporal workflow, **When** the execution starts or changes state, **Then** the relevant search attributes (e.g., state, owner, repo) are visible in the Temporal server.

### Edge Cases

- **Temporal Server Unreachable**: The API returns a 503/500 error and the workflow is not started.
- **Activity Failures**: Activities use Temporal RetryPolicies with exponential backoff. The workflow will fail if max retries are exceeded or if a non-retryable terminal error occurs.
- **Invalid Parameters**: The API validates inputs using Pydantic models and returns a 422 Unprocessable Entity before contacting Temporal.
- **Large Payloads**: Large outputs (e.g., plan text) are offloaded to the artifact store, and only references are kept in Temporal history to avoid bloat.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST create a new Temporal workflow execution of type `MoonMind.Run` when requested via the Run API. (Maps to DOC-REQ-001, DOC-REQ-005)
- **FR-006**: Activities executed by the workflow MUST use Temporal RetryPolicies with exponential backoff.
- **FR-007**: Large payloads (e.g., plan text) MUST be offloaded to the artifact store, keeping only references in Temporal history.
- **FR-002**: System MUST transition the workflow through distinct operational phases such as initializing, planning, and executing, and record these in the execution history. (Maps to DOC-REQ-002)
- **FR-003**: System MUST close the Temporal workflow with a valid terminal status indicating either success or the specific nature of failure upon completion. (Maps to DOC-REQ-003)
- **FR-004**: System MUST attach and update relevant Temporal search attributes to the workflow execution so they can be filtered and viewed in Temporal UI/CLI. (Maps to DOC-REQ-004)
- **FR-005**: System MUST include a suite of automated validation tests to empirically verify the workflow end-to-end. (Maps to DOC-REQ-005)

### Key Entities

- **Run Workflow Execution**: Represents a durable, stateful process running in Temporal, containing the history of phases, current status, and associated search attributes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of newly submitted "Run" tasks successfully start a corresponding `MoonMind.Run` execution on the Temporal server.
- **SC-002**: Workflow executions correctly report search attributes, verified via Temporal queries.
- **SC-003**: Validation tests pass continuously in the CI environment without flaky failures.
- **SC-004**: Workflow executions successfully reach a terminal state (Completed/Failed) within expected timeout bounds.