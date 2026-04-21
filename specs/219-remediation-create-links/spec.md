# Feature Specification: Remediation Create Links

**Feature Branch**: `219-remediation-create-links`
**Created**: 2026-04-21
**Status**: Draft
**Input**: Jira Orchestrate for MM-431.

Source story: STORY-001.
Source summary: Accept remediation create requests and persist target links.
Source Jira issue: unknown.
Original brief reference: not provided.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside the breakdown task.

## User Story - Persist Remediation Targets

**Summary**: As a MoonMind operator, I want task creation to accept a remediation target and persist the target relationship so remediation tasks can be inspected from either side.

**Goal**: A task submitted with `task.remediation.target.workflowId` starts through the normal execution create path, pins the current target run, and records a durable non-dependency relationship between the new remediation run and the target run.

**Independent Test**: Submit a `MoonMind.Run` task containing `task.remediation.target.workflowId` for an existing run and verify the created run keeps the remediation payload and exposes a persisted link for outbound and inbound lookup.

**Acceptance Scenarios**:

1. **Given** an existing visible `MoonMind.Run` target, **When** a new `MoonMind.Run` is created with `task.remediation.target.workflowId`, **Then** the create path persists a remediation link from the new workflow to the target workflow and target run.
2. **Given** the remediation request omits `target.runId`, **When** creation succeeds, **Then** the persisted link records the target execution's current run ID at create time.
3. **Given** a malformed remediation request with no target workflow ID, **When** the task is submitted, **Then** creation fails before the workflow starts and no remediation link is written.
4. **Given** a target workflow that does not exist or is not a `MoonMind.Run`, **When** the task is submitted, **Then** creation fails with a validation error and no remediation link is written.

### Edge Cases

- A remediation request must not use a target run ID as the workflow target.
- A caller-provided `target.runId` must match the current known target run because historical run lookup is not available in this slice.
- Remediation links are relationships, not dependencies, so they must not add `dependsOn` gates or dependency fan-out behavior.

## Assumptions

- This story implements the create-time persistence slice only. Evidence bundle creation, action policies, locks, and remediation tool surfaces remain out of scope.
- Visibility reuses the existing execution owner boundary used by dependency validation.
- The durable link table is sufficient for the first forward and reverse lookup methods; UI rendering can be layered on later.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` section 7.4): Create-time validation must require `task.remediation.target.workflowId`. Scope: in scope, mapped to FR-001 and FR-004.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` sections 6 and 8.1): Create time must resolve and persist a concrete target run ID. Scope: in scope, mapped to FR-002.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 5.2 and 8.2): Remediation is a directed relationship, not a dependency, and must support forward and reverse lookup. Scope: in scope, mapped to FR-003 and FR-006.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` sections 10 and 9): Evidence context, action execution, locks, and live follow are typed later-stage surfaces. Scope: out of scope for this persistence slice.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task-shaped and direct execution create paths MUST accept a nested `task.remediation` object for `MoonMind.Run` creation.
- **FR-002**: When `task.remediation.target.runId` is absent, the system MUST persist the target execution's current run ID on the remediation link.
- **FR-003**: The system MUST persist one durable remediation link containing remediation workflow ID, remediation run ID, target workflow ID, target run ID, mode, authority mode, status, and trigger type when remediation creation succeeds.
- **FR-004**: The system MUST reject remediation create requests that omit `target.workflowId`, reference a run ID instead of a workflow ID, reference a missing execution, reference a non-`MoonMind.Run` execution, or specify a target run ID that does not match the current target run.
- **FR-005**: Remediation link persistence MUST occur in the same create transaction as the canonical execution source record so failed validation or persistence cannot leave orphan links.
- **FR-006**: The service layer MUST provide forward lookup from remediation workflow to target and reverse lookup from target workflow to remediation workflows.
- **FR-007**: Remediation create handling MUST NOT create dependency edges or alter dependency wait behavior.

### Key Entities

- **Remediation Link**: A directed relationship from a remediation execution to a target execution, including pinned run identity and compact remediation metadata.
- **Target Execution**: The existing `MoonMind.Run` being investigated or repaired.
- **Remediation Execution**: The new `MoonMind.Run` created with `task.remediation`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove valid remediation creation persists exactly one link with target workflow and resolved run identity.
- **SC-002**: Unit tests prove malformed, missing, run-ID, and non-run targets fail before workflow start.
- **SC-003**: Unit tests prove remediation links are queryable in both outbound and inbound directions.
- **SC-004**: Existing dependency create tests continue to pass, proving remediation links do not change dependency semantics.
