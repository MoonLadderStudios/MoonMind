# Feature Specification: Temporal Projection Sync

**Feature Branch**: `001-temporal-projection-sync`  
**Created**: 2026-03-08  
**Status**: Draft  
**Input**: User description: "Implement 5.6 from docs/Temporal/TemporalMigrationPlan.md (Projection Sync: DB <- Temporal). Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001**: (Section 3: Missing Features - Projection Sync) Map Temporal state deterministically to local `TemporalExecutionRecord` fields (status, closeStatus, state machine phase, waitingReason, artifacts, search-attributes, etc.).
- **DOC-REQ-002**: (Section 3 & 5.6: Projection Sync DB <- Temporal) When an execution is read via the API, the local record must repopulate or update from the Temporal state.
- **DOC-REQ-003**: (Section 5.6: Projection Sync DB <- Temporal) Rehydrate correctly without creating duplicates if a local execution row is deleted or stale.
- **DOC-REQ-004**: (Section 5.6: Projection Sync DB <- Temporal) Local list and detail queries must match the actual Temporal state after any workflow progress.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Viewing Up-to-Date Execution Detail (Priority: P1)

Users viewing the Mission Control UI task detail view need to see the authoritative state of an execution as managed by Temporal, so they can make decisions based on accurate data.

**Why this priority**: Accurate execution state is fundamental to observability and the core value of migrating to Temporal.

**Independent Test**: Can be tested by creating an execution in Temporal, querying the MoonMind execution detail API, and ensuring the API response matches the Temporal server state exactly without duplicates in the local DB.

**Acceptance Scenarios**:

1. **Given** a local DB execution record is older than the current Temporal state, **When** the execution detail API is queried, **Then** the local DB record is updated and the API returns the latest state from Temporal.
2. **Given** an execution exists in Temporal but is missing from the local DB, **When** the execution detail API is queried by ID, **Then** a local DB record is correctly rehydrated and the API returns the accurate state.

### User Story 2 - Viewing Execution Lists (Priority: P1)

Users viewing the execution dashboard need the list view to reflect recent workflow progress reliably, so they can monitor overall system activity.

**Why this priority**: Monitoring multiple tasks at once is essential for platform operators. List views must be consistent with the actual source of truth.

**Independent Test**: Can be tested by progressing a workflow in Temporal and verifying the list API endpoint returns updated statuses (e.g. from planning to executing) that match Temporal.

**Acceptance Scenarios**:

1. **Given** multiple workflow executions progressing in Temporal, **When** the list executions API is queried, **Then** the returned list reflects the latest states from Temporal, updating any stale local projections.

### Edge Cases

- What happens when the Temporal server is temporarily unreachable during an API query? (Assume fallback to last known local DB state or appropriate error indicating service unavailability).
- How does the system handle concurrent reads that might trigger simultaneous database upserts for the same execution? (Assume database upserts handle concurrency gracefully to avoid duplicate row creation).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST update or repopulate the local `TemporalExecutionRecord` using the Temporal state when an execution is retrieved via the API. (Maps to DOC-REQ-002)
- **FR-002**: System MUST map Temporal visibility and workflow data deterministically to local DB fields including `status`, `closeStatus`, state machine phase, `waitingReason`, `artifacts`, and `search-attributes`. (Maps to DOC-REQ-001)
- **FR-003**: System MUST gracefully handle rehydration for missing local rows without throwing "not found" if the execution exists in Temporal, and MUST NOT create duplicate local database rows during syncs. (Maps to DOC-REQ-003)
- **FR-004**: System MUST ensure that list and detail API queries return results that consistently match the latest state from the Temporal server after any workflow progress. (Maps to DOC-REQ-004)
- **FR-005**: System MUST include production runtime code implementing the projection sync logic along with corresponding validation tests (unit/integration) validating the synchronisation behaviour. (Runtime intent guard)

### Key Entities

- **Temporal Workflow Execution**: The source of truth containing actual workflow state, history, and search attributes in the Temporal Server.
- **TemporalExecutionRecord**: The local database projection of the workflow execution used for caching and querying.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of execution detail API requests successfully return the current Temporal state, rebuilding stale DB rows automatically.
- **SC-002**: 0 duplicate execution rows are created in the local database during concurrent rehydration operations.
- **SC-003**: Automated validation tests cover the projection sync logic and pass consistently in CI.

## Assumptions

- **Concurrency Handling**: Standard SQL unique constraints (e.g., on workflow ID) exist to prevent duplicate row insertion during concurrent sync attempts.
- **Performance**: The overhead of querying the Temporal client for describe/visibility operations during API reads is acceptable and does not severely degrade endpoint latency.
- **Unreachable Temporal**: If Temporal is down, the system should ideally return the last known DB projection or fail safely (not scoped for complex fallback in this specific feature, but standard error handling applies).

## Prompt B Remediation Status (Step 12/16)

### CRITICAL/HIGH remediation status

- Runtime-mode coverage is explicit in `tasks.md` with production runtime implementation tasks and required validation tasks listed under Prompt B scope controls.
- `DOC-REQ-*` traceability now includes deterministic implementation-task and validation-task mappings for `DOC-REQ-001` through `DOC-REQ-004` in `tasks.md`.
- Cross-artifact alignment is explicit: runtime implementation intent in this spec, runtime constraints in `plan.md`, and execution/validation sequencing in `tasks.md` are consistent.

### MEDIUM/LOW remediation status

- Wording has been normalized across artifacts to keep runtime-first constraints deterministic and avoid docs-only interpretation drift.

### Residual risks

- The feature interacts with an external service (Temporal Server) which may introduce integration defects during implementation, such as unhandled timeouts or missing visibility data.
- Validation evidence remains open until tests are executed and results recorded.
