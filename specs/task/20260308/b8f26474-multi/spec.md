# Feature Specification: Temporal API Consistency

**Feature Branch**: `071-temporal-api-consistency`
**Created**: 2026-03-08
**Status**: Draft
**Input**: User description: "Implement 5.10 from docs/Temporal/TemporalMigrationPlan.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001**: `/tasks/list?source=temporal` and `/tasks/{id}` return authoritative data. Fields like `status`, `rawState`, `closeStatus`, `waitingReason` come from Temporal (or synced DB).
  - Source: docs/Temporal/TemporalMigrationPlan.md, section 5, item 10.
- **DOC-REQ-002**: Workflow IDs with `mm:` prefix map correctly.
  - Source: docs/Temporal/TemporalMigrationPlan.md, section 5, item 10.
- **DOC-REQ-003**: Filters on workflowType/entry/state work as expected.
  - Source: docs/Temporal/TemporalMigrationPlan.md, section 5, item 10.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Authoritative Task Listing (Priority: P1)

As a Mission Control UI user, I want to see an authoritative list of Temporal tasks so that I can accurately monitor workflow executions with reliable status, state, and waiting reasons.

**Why this priority**: Essential for observing the correct state of workflows powered by Temporal rather than stale or faked local DB data.

**Independent Test**: Can be fully tested by starting a Temporal workflow, listing executions with `?source=temporal`, and verifying the returned data precisely matches Temporal visibility/history.

**Acceptance Scenarios**:

1. **Given** a Temporal-backed workflow is running, **When** I request `/tasks/list?source=temporal`, **Then** the list includes the execution with fields `status`, `rawState`, `closeStatus`, and `waitingReason` sourced from Temporal.
2. **Given** multiple workflow types and states, **When** I request `/tasks/list?source=temporal&state=...`, **Then** the results are properly filtered using Temporal search attributes.

---

### User Story 2 - Authoritative Task Details (Priority: P1)

As a Mission Control UI user, I want to view accurate details of a specific Temporal task so that I can inspect its complete and current state, including its exact waiting reasons or closure status.

**Why this priority**: Without accurate details, users cannot diagnose task issues or determine exactly what the workflow is doing.

**Independent Test**: Can be fully tested by fetching `/tasks/{id}` for a workflow with the `mm:` prefix and validating the response payloads against the actual Temporal history.

**Acceptance Scenarios**:

1. **Given** I have a task ID with an `mm:` prefix, **When** I fetch its details via `/tasks/{id}`, **Then** the workflow maps correctly to the Temporal server and returns true state.
2. **Given** a workflow is waiting on an external integration, **When** I view its details, **Then** `waitingReason` is explicitly surfaced from Temporal.

### Edge Cases

- What happens when the requested workflow ID does not exist in Temporal but exists in local DB?
- How does the system handle Temporal server unavailability when fetching `/tasks/list?source=temporal`?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST route requests to `/tasks/list?source=temporal` and `/tasks/{id}` to fetch or synthesize authoritative state from Temporal. (Satisfies DOC-REQ-001)
- **FR-002**: System MUST return accurate values for `status`, `rawState`, `closeStatus`, and `waitingReason` deriving from Temporal's execution history or synced DB records. (Satisfies DOC-REQ-001)
- **FR-003**: System MUST recognize and properly map Workflow IDs starting with the `mm:` prefix when resolving executions. (Satisfies DOC-REQ-002)
- **FR-004**: System MUST apply filters for `workflowType`, `entry`, and `state` utilizing Temporal search attributes or correct DB synced states. (Satisfies DOC-REQ-003)
- **FR-005**: System MUST include explicit production runtime code changes and validation tests for these API endpoint adjustments, fulfilling the runtime deliverables scope guard. (Satisfies runtime scope guard)

### Key Entities

- **Execution Record**: Represents a Temporal workflow execution containing authoritative fields (`status`, `rawState`, `closeStatus`, `waitingReason`).
- **Workflow ID Mapping**: Logic to handle ID translations specifically with `mm:` prefixes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of `/tasks/list?source=temporal` and `/tasks/{id}` responses for Temporal tasks return data consistent with Temporal's actual state.
- **SC-002**: All supported filters (`workflowType`, `entry`, `state`) correctly isolate matching workflow executions without returning false positives.
- **SC-003**: Production runtime code changes are successfully implemented and validated by an automated test suite.
