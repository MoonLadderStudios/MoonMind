# Feature Specification: Wire Temporal Artifacts

**Feature Branch**: `001-wire-temporal-artifacts`  
**Created**: 2026-03-08  
**Status**: Draft  
**Input**: User description: "Implement 5.8 from docs/Temporal/TemporalMigrationPlan.md: Wire activities to artifact store. Acceptance Criteria: In MoonMind.Run and ManifestIngest workflows, replace any large return values with artifact references. All expected artifacts are produced and retrievable via the artifact API. Workflow histories contain only references. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## Source Document Requirements

- **DOC-REQ-001**: Source: `docs/Temporal/TemporalMigrationPlan.md` Section 5.8 - In MoonMind.Run and ManifestIngest workflows, replace any large return values with artifact references.
- **DOC-REQ-002**: Source: `docs/Temporal/TemporalMigrationPlan.md` Section 5.8 - The "plan" activity must return a plan_ref link, not raw plan text.
- **DOC-REQ-003**: Source: `docs/Temporal/TemporalMigrationPlan.md` Section 5.8 - All expected artifacts (input, plan, summary, run index, logs, test output) are produced and retrievable via the artifact API.
- **DOC-REQ-004**: Source: `docs/Temporal/TemporalMigrationPlan.md` Section 5.8 - Workflow histories contain only references, never large payloads.
- **DOC-REQ-005**: Source: User Request - Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Replace Large Payloads with References in MoonMind.Run (Priority: P1)

As a system operator or developer inspecting Temporal workflows, I want the MoonMind.Run workflow to store large outputs (like plans, logs, run indices, test outputs) in the artifact store and keep only a reference in the workflow history, so that the Temporal history remains small and performant.

**Why this priority**: Large workflow histories cause Temporal performance degradation and can exceed size limits. Fixing this is essential for stable execution.

**Independent Test**: Can be fully tested by triggering a MoonMind.Run workflow and inspecting its Temporal history to ensure no large strings/blobs are returned by activities, but rather artifact reference IDs.

**Acceptance Scenarios**:

1. **Given** a MoonMind.Run workflow is executing the plan generation activity, **When** the activity finishes, **Then** it returns an artifact reference (e.g. `plan_ref`) instead of the raw plan text.
2. **Given** a MoonMind.Run workflow produces logs or summaries, **When** those are persisted, **Then** the workflow history only records the artifact references.

---

### User Story 2 - Replace Large Payloads with References in ManifestIngest (Priority: P1)

As a system operator, I want the ManifestIngest workflow to similarly use artifact references for all its large outputs, so that the ingest process does not bloat Temporal history.

**Why this priority**: Manifest ingestion involves large data objects which must be offloaded to maintain Temporal history limits.

**Independent Test**: Can be tested by running a ManifestIngest workflow and verifying that the Temporal execution history contains only reference pointers.

**Acceptance Scenarios**:

1. **Given** a ManifestIngest workflow executing an activity that produces large output, **When** the activity completes, **Then** the result is an artifact reference and the actual data is accessible via the artifact API.

---

### User Story 3 - Validate Production Code and Tests (Priority: P1)

As a maintainer, I need to ensure that the changes are backed by production runtime code and validation tests, not just documentation updates.

**Why this priority**: The scope guard explicitly requires runtime code changes and tests to ensure the implementation is robust.

**Independent Test**: Ensure CI passes and tests explicitly verify that large payloads are not leaking into Temporal histories.

**Acceptance Scenarios**:

1. **Given** the new implementation, **When** running the test suite, **Then** there are validation tests covering the artifact store wiring in both workflows.

### Edge Cases

- What happens when the artifact store is unavailable or returns an error during an activity? The activity should fail and be retried by Temporal, but not drop data.
- How does the system handle fetching an artifact reference that has been deleted or expired from the artifact store?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST replace all large return values in `MoonMind.Run` workflow activities with artifact references (satisfies DOC-REQ-001).
- **FR-002**: System MUST replace all large return values in `ManifestIngest` workflow activities with artifact references (satisfies DOC-REQ-001).
- **FR-003**: System MUST ensure the "plan" activity specifically returns a `plan_ref` link instead of raw text (satisfies DOC-REQ-002).
- **FR-004**: System MUST ensure that input, plan, summary, run index, logs, and test output artifacts are retrievable via the artifact API (satisfies DOC-REQ-003).
- **FR-005**: System MUST ensure that Temporal workflow histories for these workflows contain only references, not the raw data payloads (satisfies DOC-REQ-004).
- **FR-006**: System MUST include production runtime code changes to effect these updates, plus corresponding validation tests (satisfies DOC-REQ-005).

### Key Entities

- **Artifact Reference**: A structured pointer or ID string indicating where a large payload is stored in the external artifact store.
- **Artifact API**: The interface used to write and read the actual data payloads referenced by the workflow history.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Size of Temporal history payloads for MoonMind.Run and ManifestIngest workflows is reduced by >90% for typical execution paths, avoiding any history size limit warnings.
- **SC-002**: 100% of large outputs (plans, logs, summaries) generated during a run are verifiable and downloadable via the artifact API instead of being embedded in history.
- **SC-003**: 100% of the modified workflow activities have corresponding validation tests proving they return references instead of raw data.