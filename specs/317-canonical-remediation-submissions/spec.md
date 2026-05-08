# Feature Specification: Canonical Remediation Submissions

**Feature Branch**: `317-canonical-remediation-submissions`
**Created**: 2026-05-08
**Status**: Draft
**Input**:

```text
Jira Issue: MM-617
Issue Type: Story
Current Jira Status: In Progress
Summary: Create canonical remediation submissions with durable target links

Use this Jira issue as the canonical source request for MoonSpec artifacts and pull request traceability. Preserve `MM-617` in spec.md, plan.md/task traceability where applicable, verification output, commit/PR metadata, and any Jira-visible handoff.

Source Reference:
- Source Document: docs/Tasks/TaskRemediation.md
- Source Title: Task Remediation
- Coverage IDs: DESIGN-REQ-001 through DESIGN-REQ-007

Jira Preset Brief / Description:
Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 1. Purpose
- 2. Why a separate system is required
- 5. Architectural stance
- 6. Core invariants
- 7. Submission contract
- 8. Identity, linkage, and read models
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-007

As an operator, I can create a remediation task against a target execution through the normal execution create path so that the relationship is explicit, validated, pinned to a target run, and visible in both directions without behaving like a dependency.

Acceptance Criteria
- Given a valid remediation create request, the backend persists task.remediation with workflowId and resolved runId before MoonMind.Run starts.
- Given invalid target, visibility, self-reference, nested, authority, action policy, or taskRunIds input, create fails early with a structured remediation error and no null-target task is created.
- Given an execution with inbound or outbound remediation links, the API returns direction-specific link records with status, authority mode, lock, last action, resolution, and timestamps.
- Remediation links are modeled separately from task dependencies and do not wait for target success.

Requirements
- Remediation tasks remain normal top-level MoonMind.Run executions with nested remediation metadata.
- Create-time validation must be fail-fast and bounded.
- The durable link model must support Mission Control list/detail rendering.

Relevant implementation notes from docs/Tasks/TaskRemediation.md:
- Remediation tasks remain normal top-level MoonMind.Run executions with nested task.remediation metadata.
- The remediation relationship is a directed link to a target execution and is not a dependency gate.
- Create-time validation must require target.workflowId, resolve and persist target.runId, reject self-reference and unsupported target types, validate selected taskRunIds, reject unsupported authority/action policy values, block unsupported nested remediation, and initialize durable forward and reverse lookup records.
- Durable link/read models must support direction-specific inbound/outbound remediation records with target and remediation workflow/run identity, status/phase, authority and lock metadata, last action, resolution, timestamps, and final outcome.
- Evidence and artifacts remain server-mediated; artifact refs are identifiers, not access grants.

MoonSpec story boundary:
Implement the single story for MM-617: create canonical remediation submissions with durable target links. The implementation must keep remediation tasks as normal top-level MoonMind.Run executions with nested remediation metadata, validate remediation create requests fail-fast before the run starts, persist explicit durable target links, and expose direction-specific link records for Mission Control list/detail rendering.
```

## User Story - Create Canonical Remediation Submissions

**Summary**: As a MoonMind operator, I want to create a remediation task against a target execution so the platform records an explicit, validated, pinned relationship that is visible in both directions without treating the target as a dependency.

**Goal**: A valid remediation submission creates a normal MoonMind run with nested remediation metadata, persists the target workflow and resolved run identity before the run starts, and makes the relationship visible from both the remediation and target execution views.

**Independent Test**: Submit a remediation create request for an existing visible target execution without a target run ID, then verify the created run contains remediation metadata, records the resolved target run, starts without dependency gating, and exposes inbound and outbound link records with compact status and lifecycle fields.

**Acceptance Scenarios**:

1. **Given** an existing visible target execution, **When** an operator submits a valid remediation create request with a target workflow ID, **Then** the created run contains nested remediation metadata and records a directed relationship to the target execution before the run starts.
2. **Given** a valid remediation create request omits the target run ID, **When** the request is accepted, **Then** the platform resolves and persists the current target run ID before the remediation run starts.
3. **Given** the target execution is failed, incomplete, stalled, or otherwise not successful, **When** a valid remediation create request is accepted, **Then** the remediation run can start without waiting for target success and without creating a dependency gate.
4. **Given** invalid target, visibility, self-reference, nested remediation, unsupported authority mode, unsupported action policy, or invalid task-run selection input, **When** the request is submitted, **Then** creation fails before workflow start with a structured remediation error and no null-target remediation task is created.
5. **Given** an execution has inbound or outbound remediation links, **When** an operator inspects the relationship, **Then** direction-specific records include target identity, remediation identity, pinned run identity, status, authority mode, lock state, latest action, resolution, and timestamps.

### Edge Cases

- Empty target workflow IDs, run IDs used where workflow IDs are required, and unknown targets are rejected before a remediation run starts.
- A remediation task cannot target itself.
- A remediation task cannot target another remediation task unless an explicit future policy allows nested remediation.
- Selected task-run IDs must belong to the selected target execution or selected target steps.
- Unsupported authority modes and unsupported action policies fail closed rather than falling back to hidden defaults.
- Relationship creation must not produce dependency records or dependency wait requirements.
- Historical targets with limited evidence still preserve the explicit relationship; missing evidence is handled by later evidence-collection behavior rather than blocking link creation.

## Assumptions

- This story covers canonical create-time submission, validation, pinned target linkage, and compact bidirectional relationship visibility only.
- Evidence bundle creation, live log following, action execution, approvals, lifecycle artifact publication, and automatic self-healing are covered by separate remediation stories.
- Existing execution visibility rules determine whether the caller may create remediation for a target execution.
- The source design's broader evidence and action requirements constrain safety but are out of scope unless needed to validate create-time submission and link visibility.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` sections 1 and 5): Remediation tasks remain normal top-level MoonMind runs with nested remediation semantics rather than a separate workflow class. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` section 5.2): Remediation is a directed relationship to a target execution and is not a dependency gate. Scope: in scope, mapped to FR-003 and FR-008.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 6 and 7): The canonical create contract stores remediation intent under nested task remediation metadata. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` sections 6, 7.3, and 8.1): Create time resolves and persists a concrete pinned target run identity. Scope: in scope, mapped to FR-004.
- **DESIGN-REQ-005** (`docs/Tasks/TaskRemediation.md` section 7.4): Create-time validation rejects missing targets, malformed self-reference, unsupported target types, invalid task-run selections, unsupported authority modes, incompatible action policies, and disallowed nested remediation before workflow start. Scope: in scope, mapped to FR-005 and FR-006.
- **DESIGN-REQ-006** (`docs/Tasks/TaskRemediation.md` sections 8.2 through 8.4): Durable remediation link records support forward and reverse lookup with compact status, lifecycle, authority, lock, action, resolution, and timestamp fields. Scope: in scope, mapped to FR-007.
- **DESIGN-REQ-007** (`docs/Tasks/TaskRemediation.md` sections 6 and 9): Evidence access remains server-mediated and artifact refs are identifiers, not access grants. Scope: out of scope for this create/link story except as a safety constraint on link metadata; later evidence-bundle stories cover evidence retrieval behavior.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST allow a normal run creation request to carry nested remediation metadata for a target execution.
- **FR-002**: The created run MUST preserve remediation metadata in the canonical normalized task payload before the run starts.
- **FR-003**: Successful remediation creation MUST persist a directed relationship from the remediation run to the target execution.
- **FR-004**: When the request omits the target run ID, the system MUST resolve and persist the target execution's current run ID before the remediation run starts.
- **FR-005**: Remediation creation MUST reject missing targets, empty target identifiers, run IDs used as workflow IDs, missing or invisible targets, unsupported target types, and self-targeting before workflow start.
- **FR-006**: Remediation creation MUST reject invalid task-run selections, unsupported authority modes, incompatible action policies, and disallowed nested remediation targets before workflow start.
- **FR-007**: Operators MUST be able to inspect remediation relationships in both directions with target identity, remediation identity, pinned run identity, status, authority mode, lock state, latest action, resolution, and timestamps.
- **FR-008**: Remediation relationships MUST NOT create dependency edges, dependency wait requirements, or success gates against the target execution.
- **FR-009**: Invalid remediation submissions MUST fail with structured remediation errors and MUST NOT create a remediation task with a missing or null target relationship.
- **FR-010**: The specification, downstream implementation notes, verification evidence, and pull request metadata MUST preserve Jira issue key MM-617 for traceability.

### Key Entities

- **Remediation Run**: A normal MoonMind run created with remediation metadata that investigates or repairs another execution.
- **Target Execution**: The logical execution selected for remediation.
- **Pinned Target Run**: The concrete target run identity resolved and stored at create time.
- **Remediation Relationship**: The durable directed link between remediation and target executions with compact status, authority, lock, action, resolution, and timestamp fields.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of valid remediation create tests preserve nested remediation metadata and exactly one directed relationship.
- **SC-002**: 100% of valid requests without a target run ID persist a resolved target run ID before run start.
- **SC-003**: 100% of malformed target, visibility, self-reference, nested, authority, action policy, and task-run selection validation cases fail before run start with structured remediation errors.
- **SC-004**: 100% of relationship lookup tests can read inbound and outbound remediation records with pinned target identity and compact lifecycle fields.
- **SC-005**: 0 dependency records or dependency wait gates are created by remediation relationship tests.
- **SC-006**: Verification evidence and pull request metadata include `MM-617` in every completed run for this feature.
