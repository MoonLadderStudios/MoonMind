# Feature Specification: Refactor Execution Service to Temporal Authority

**Feature Branch**: `001-refactor-execution-service`  
**Created**: 2026-03-08
**Status**: Draft  
**Input**: User description: "Implement 5.5 from docs/Temporal/TemporalMigrationPlan.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001**: All execution operations (list, detail, update, signal, cancel) use Temporal calls. (Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 5)
- **DOC-REQ-002**: Local DB only reflects Temporal, not source-of-truth. Listing shows only actual Temporal workflows. (Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 5)
- **DOC-REQ-003**: Signals/updates errors come from workflow validation, not stale DB checks. (Source: docs/Temporal/TemporalMigrationPlan.md, Section 5, Task 5)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Viewing execution lists and details (Priority: P1)

Users view their workflow executions on the Mission Control UI and see authoritative data coming directly from Temporal (or a strictly synchronized local cache).

**Why this priority**: Correct visibility into task status is essential for operators.

**Independent Test**: Can be fully tested by creating a workflow directly in Temporal and verifying it appears correctly in the API list/detail responses without requiring manual local DB inserts.

**Acceptance Scenarios**:

1. **Given** an execution running in Temporal, **When** the list API is called, **Then** the execution is returned with its state accurately reflecting Temporal's current state.
2. **Given** an execution that no longer exists in Temporal but is in the local DB, **When** the list API is called, **Then** the orphaned execution is not returned or is marked as synced/removed.

---

### User Story 2 - Triggering execution actions (Priority: P1)

Users perform actions on an execution (cancel, pause, resume, signal, update) through the UI, which routes those actions directly to the Temporal workflow.

**Why this priority**: Operators need to control running tasks durably, preventing stale-state issues.

**Independent Test**: Can be fully tested by issuing an API cancel request and verifying that the Temporal workflow receives the cancellation signal before the local database state is updated.

**Acceptance Scenarios**:

1. **Given** a running execution, **When** a user cancels the execution via API, **Then** a Temporal cancel request is issued to the workflow handle.
2. **Given** an invalid action (e.g., resuming an already completed execution), **When** the action is attempted, **Then** the error is returned by Temporal's workflow validation rather than a local database check.

### Edge Cases

- What happens when Temporal is temporarily unreachable during a list or detail request? (Should fail gracefully or return cached data with a staleness warning).
- How does system handle existing executions in the DB that don't have matching Temporal runs? (They should be ignored or cleaned up).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST implement production runtime code changes to switch the `ExecutionService` to use Temporal as the authoritative source of truth, satisfying DOC-REQ-001.
- **FR-002**: The system MUST query Temporal for workflow states during list and detail operations, ensuring the local DB acts only as a projection/cache, satisfying DOC-REQ-002.
- **FR-003**: The system MUST route all execution actions (signal, cancel, update) directly to the Temporal workflow handle.
- **FR-004**: The system MUST rely on Temporal for action validation and error reporting, satisfying DOC-REQ-003.
- **FR-005**: The system MUST include automated validation tests for the execution service refactor.

### Key Entities

- **Execution**: Represents a Temporal workflow run, identified by `workflowId` and `runId`.
- **Action/Signal**: An operator-initiated command routed to a specific Temporal workflow execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of execution API reads (list, detail) derive their primary state from Temporal or a validated Temporal projection.
- **SC-002**: 100% of execution actions (cancel, pause, resume) are successfully routed as Temporal signals/updates.
- **SC-003**: No orphaned DB executions (workflows not in Temporal) are returned as active in the Mission Control API.
- **SC-004**: Validation tests verify the execution service against a real or mocked Temporal client backend.

## Assumptions

- Temporal server is available and responsive for queries and signals.
- Existing frontend UI correctly consumes the execution API and does not need modification for this backend refactoring unless API contracts change.

## Prompt B Remediation Status (Step 12/16)

- Prompt B scope-control language is aligned across `spec.md`, `plan.md`, and `tasks.md` so runtime implementation mode remains explicit and deterministic.