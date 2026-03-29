# Feature Specification: Task Dependencies Phase 1 — Submit Contract And Validation

**Feature Branch**: `117-task-dep-phase1`
**Created**: 2026-03-29
**Status**: Draft
**Input**: User description: "Fully implement Phase 1 from docs/tmp/011-TaskDependenciesPlan.md"

## Source Document Requirements

Extracted from `docs/Tasks/TaskDependencies.md` §4 API Contract and §4.1 Validation rules.

| Requirement ID | Source Citation | Requirement Summary |
|----------------|----------------|---------------------|
| DOC-REQ-001 | §4 "task.dependsOn" | A task-shaped create request MUST support declaring prerequisite execution IDs in `payload.task.dependsOn`. |
| DOC-REQ-002 | §4.1 rule 1 | `dependsOn` MUST be validated as a JSON array of strings. |
| DOC-REQ-003 | §4.1 rule 2 | Blank entries MUST be removed and repeated IDs deduplicated before use. |
| DOC-REQ-004 | §4.1 rule 3 | At most 10 dependency IDs may remain after normalization. |
| DOC-REQ-005 | §4.1 rule 4 | Each ID MUST resolve to an existing execution. |
| DOC-REQ-006 | §4.1 rule 5 | Each target execution MUST be a `MoonMind.Run` workflow. |
| DOC-REQ-007 | §4.1 rule 6 | The new execution MUST NOT depend on itself. |
| DOC-REQ-008 | §4.2 Cycle detection | Adding dependency edges MUST NOT create a cycle in the transitive dependency graph. |
| DOC-REQ-009 | §4 "normalizes this into" | Normalized dependency IDs MUST be persisted in `initialParameters.task.dependsOn`. |
| DOC-REQ-010 | §4.1 "Return clear errors" | Validation failures MUST return clear, specific error messages covering each rejection case. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator submits a task with valid dependencies (Priority: P1)

An operator creates a `MoonMind.Run` execution via `POST /api/executions` and includes a `payload.task.dependsOn` array referencing existing `MoonMind.Run` executions. The system normalizes the dependency list (trims whitespace, deduplicates, validates each ID), persists normalized IDs, and creates the execution without error.

**Why this priority**: This is the primary happy-path flow. Without it, the dependency feature cannot be used.

**Independent Test**: Can be tested by submitting a valid task with `dependsOn` and asserting the created execution's `initialParameters.task.dependsOn` contains the normalized list.

**Acceptance Scenarios**:

1. **Given** a `POST /api/executions` request with `payload.task.dependsOn` containing valid `MoonMind.Run` workflow IDs, **When** the request is processed, **Then** the execution is created with `initialParameters.task.dependsOn` containing the normalized list.
2. **Given** a `dependsOn` array with whitespace padding and duplicate entries, **When** normalized, **Then** the result contains trimmed, deduplicated entries.
3. **Given** an empty `dependsOn` array, **When** the request is processed, **Then** the execution is created without a `dependsOn` key in `initialParameters.task`.

---

### User Story 2 - Router rejects invalid dependsOn shape (Priority: P1)

The router validates `dependsOn` at the request-handling layer before calling the service. Invalid shapes (non-array type, non-string elements, more than 10 items) are rejected with a 422 response.

**Why this priority**: Pre-service validation prevents unnecessary DB or Temporal service calls for malformed requests.

**Independent Test**: Submit a request with each invalid shape and assert a 422 response with an appropriate error message.

**Acceptance Scenarios**:

1. **Given** `dependsOn` is a non-array (e.g., integer or string), **When** submitted, **Then** 422 with message indicating it must be a JSON array.
2. **Given** `dependsOn` has more than 10 items after deduplication, **When** submitted, **Then** 422 with message `"payload.task.dependsOn can have a maximum of 10 items."`.
3. **Given** `dependsOn` contains a non-string element, **When** submitted, **Then** 422 with message indicating elements must be strings.

---

### User Story 3 - Service rejects missing, non-Run, or self dependencies (Priority: P1)

The service validates each dependency ID at create time: each must resolve to an existing `MoonMind.Run` execution, and the new execution cannot depend on itself.

**Why this priority**: Ensures dependency integrity at the persistence layer, not just at the API layer.

**Independent Test**: Create executions and validate that referencing a missing ID, a non-Run execution, or self results in a 422 error surfaced through the API.

**Acceptance Scenarios**:

1. **Given** a `dependsOn` list containing a non-existent workflow ID, **When** submitted, **Then** 422 with message `"Dependency not found: <id>"`.
2. **Given** a `dependsOn` list targeting a `MoonMind.ManifestIngest` execution, **When** submitted, **Then** 422 with message indicating it is not a `MoonMind.Run` workflow.
3. **Given** a `dependsOn` list containing the to-be-created execution's own workflow ID, **When** submitted, **Then** 422 with message indicating self-dependency.

---

### User Story 4 - Service rejects cycles (Priority: P2)

The service detects transitive dependency cycles and graph size/depth violations with bounded traversal.

**Why this priority**: Cycle prevention is a correctness constraint. Without it, executions could wait on each other indefinitely.

**Independent Test**: Set up a simulated transitive dependency chain that would create a cycle, and assert a 422 cycle detection error.

**Acceptance Scenarios**:

1. **Given** a dependency chain that contains a direct or transitive cycle, **When** submitted, **Then** 422 with a clear cycle detection error.
2. **Given** a dependency graph that exceeds depth limits, **When** submitted, **Then** 422 indicating depth exceeded.
3. **Given** a dependency graph that exceeds the node count limit, **When** submitted, **Then** 422 indicating node limit exceeded.

---

### Edge Cases

- What if all dependency IDs are whitespace-only strings? After normalization, `dependsOn` is empty and no dependency key is stored.
- What if `dependsOn` is provided at `payload.dependsOn` instead of `payload.task.dependsOn`? The router must prefer `payload.task.dependsOn` when present; fall back to `payload.dependsOn`.
- What if the self-dependency check runs before `describe_execution` calls? The self-dependency check uses the to-be-assigned workflow ID (UUID-based `mm:...`), which won't collide with submitted user-provided IDs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST read `payload.task.dependsOn` from the request body and prefer it over `payload.dependsOn` when both are present. (DOC-REQ-001)
- **FR-002**: System MUST validate that `dependsOn` is a JSON array of strings; non-array types MUST be rejected with 422. (DOC-REQ-002)
- **FR-003**: System MUST strip whitespace from each entry and remove blank entries. (DOC-REQ-003)
- **FR-004**: System MUST deduplicate the normalized list while preserving first-occurrence order. (DOC-REQ-003)
- **FR-005**: System MUST reject requests where the normalized `dependsOn` list has more than 10 items with a 422 error. (DOC-REQ-004)
- **FR-006**: System MUST validate at create time that each ID in `dependsOn` resolves to an existing execution record. (DOC-REQ-005)
- **FR-007**: System MUST validate that each target execution has workflow type `MoonMind.Run`; others MUST be rejected with a type-specific error. (DOC-REQ-006)
- **FR-008**: System MUST reject a request where the new execution's own workflow ID appears in `dependsOn`. (DOC-REQ-007)
- **FR-009**: System MUST traverse the transitive dependency graph starting from each requested dependency ID and reject requests that would introduce a cycle. (DOC-REQ-008)
- **FR-010**: Cycle detection traversal MUST be bounded (depth and/or node count limits) to prevent unbounded execution. (DOC-REQ-008)
- **FR-011**: Normalized dependency IDs MUST be persisted in `initialParameters["task"]["dependsOn"]` so the workflow can read them. (DOC-REQ-009)
- **FR-012**: All validation failure messages MUST be specific to the rejection reason: missing target, wrong workflow type, self-dependency, cycle, or limit exceeded. (DOC-REQ-010)
- **FR-013**: All existing unit tests MUST continue to pass after any changes. No regressions.

### Key Entities

- **CreateExecutionRequest / CreateJobRequest**: The API request body. `payload.task.dependsOn` carries the raw dependency list.
- **TemporalExecutionService**: The service layer that performs create-time dependency validation via `_validate_dependencies`.
- **TemporalExecutionCanonicalRecord**: Database model whose `parameters` column stores `initialParameters`, including `task.dependsOn`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid task request with a non-empty `dependsOn` array creates an execution with `initialParameters["task"]["dependsOn"]` equal to the normalized list.
- **SC-002**: A `dependsOn` array with more than 10 items after normalization returns HTTP 422.
- **SC-003**: A `dependsOn` array referencing a non-existent execution returns HTTP 422 with `"Dependency not found: <id>"`.
- **SC-004**: A `dependsOn` array referencing a non-`MoonMind.Run` execution returns HTTP 422 with a workflow-type-specific message.
- **SC-005**: A transitive dependency cycle returns HTTP 422 with a cycle or depth/node-count exceeded message.
- **SC-006**: All unit tests pass with zero regressions (`./tools/test_unit.sh` exit code 0).
