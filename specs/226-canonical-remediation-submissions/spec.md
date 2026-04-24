# Feature Specification: Canonical Remediation Submissions

**Feature Branch**: `226-canonical-remediation-submissions`
**Created**: 2026-04-22
**Status**: Draft
**Input**: Use the Jira preset brief for MM-451 as the canonical Moon Spec orchestration input: `spec.md` (Input).

## Original Preset Brief

```text
# MM-451 MoonSpec Orchestration Input

## Source

- Jira issue: MM-451
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Accept canonical task remediation submissions with pinned target linkage
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-451 from MM board
Summary: Accept canonical task remediation submissions with pinned target linkage
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-451 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-451: Accept canonical task remediation submissions with pinned target linkage

User Story
As an operator, I can create a remediation task for a target MoonMind execution and have the platform persist an explicit non-dependency relationship to the target workflow and pinned run snapshot.

Source Document
docs/Tasks/TaskRemediation.md

Source Title
Task Remediation

Source Sections
- 1. Purpose
- 2. Why a separate system is required
- 5. Architectural stance
- 6. Core invariants
- 7. Submission contract
- 8. Identity, linkage, and read models

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-004
- DESIGN-REQ-005

Acceptance Criteria
- Given a valid remediation request, the created MoonMind.Run contains the canonical task.remediation object and starts without waiting for target success.
- When runId is omitted, the backend resolves and stores the current target runId before the run starts.
- Malformed self-targets, unsupported targets, invalid authority modes, invisible targets, invalid taskRunIds, or incompatible policies are rejected with structured errors.
- The remediation relationship is visible from remediation-to-target and target-to-remediation read paths including pinned run identity and status fields.
- POST /api/executions/{workflowId}/remediation expands to the same canonical create contract as POST /api/executions.

Requirements
- Remediation is modeled separately from dependsOn and never as a success gate.
- Canonical payload storage is nested under task.remediation.
- The link record supports forward lookup, reverse lookup, current remediation status, lock holder, action summary, final outcome, and Mission Control/API rendering.

Implementation Notes
- Preserve MM-451 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Scope the implementation to accepting canonical remediation submissions and persisting explicit pinned target linkage.
- Use existing execution creation, task remediation payload, target run resolution, authorization, validation, and read-model surfaces where possible.
- Do not model remediation as a dependency gate or wait for target success before starting the remediation run.

Needs Clarification
- None
```

## User Story - Accept Canonical Remediation Submissions

**Summary**: As a MoonMind operator, I want to create a remediation task for a target execution so the platform records the troubleshooting relationship and pinned target run without treating the target as a dependency.

**Goal**: A valid `MoonMind.Run` creation request containing `task.remediation` starts through the normal task create path, stores the canonical remediation payload, pins the target run identity, and exposes the relationship from both the remediation and target sides.

**Independent Test**: Submit a task creation request with `task.remediation.target.workflowId` for an existing visible run, then verify the created run preserves `initialParameters.task.remediation`, records the resolved target run, starts independently of target success, and exposes inbound and outbound remediation relationship lookups.

**Acceptance Scenarios**:

1. **Given** an existing visible `MoonMind.Run` target, **When** a valid task create request includes `task.remediation.target.workflowId`, **Then** the created run contains the canonical `task.remediation` object and records an explicit remediation relationship to the target.
2. **Given** a valid remediation request omits `target.runId`, **When** the run is created, **Then** the platform resolves and persists the target execution's current run ID before the remediation run starts.
3. **Given** a valid remediation request, **When** the target execution is incomplete, failed, or otherwise not successful, **Then** the remediation run still starts without waiting on target success and no dependency gate is added.
4. **Given** a malformed remediation request with self-targeting, unsupported targets, invalid authority modes, invisible targets, invalid task run IDs, or incompatible policies, **When** the request is submitted, **Then** the platform rejects it with a structured validation error before workflow start.
5. **Given** an existing remediation relationship, **When** callers inspect the relationship from either the remediation run or target run, **Then** the pinned run identity and compact status fields are available through forward and reverse read paths.
6. **Given** a caller uses `POST /api/executions/{workflowId}/remediation`, **When** the request is accepted, **Then** the route expands into the same canonical task-shaped create contract as `POST /api/executions`.

### Edge Cases

- A target workflow identifier must not be a run ID or an empty value.
- A caller-supplied `target.runId` must match the current target run when historical run lookup is unavailable.
- A remediation task must not target itself.
- Nested remediation targets are disallowed unless a future policy explicitly enables them.
- `target.taskRunIds` must be bounded to valid string identifiers when supplied.
- Remediation relationships must not create dependency records or block on dependency satisfaction.

## Assumptions

- This story covers the canonical submission and pinned linkage slice only; evidence bundle creation, remediation action execution, locks, approvals, and Mission Control presentation beyond relationship read data are handled by separate stories.
- Existing execution visibility rules are sufficient for deciding whether a target can be remediated.
- Existing action policy names define the supported create-time compatibility surface for `actionPolicyRef`.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` sections 5.1 and 7.1): Remediation tasks remain `MoonMind.Run` executions and are accepted through the canonical task-shaped create path. Scope: in scope, mapped to FR-001.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` sections 6 and 7.2): The durable create contract stores remediation semantics under `task.remediation`. Scope: in scope, mapped to FR-002.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 6 and 8.1): Create time resolves and persists a concrete pinned target `runId`. Scope: in scope, mapped to FR-003.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` sections 5.2, 7.4, and 8.2): Remediation is a directed relationship rather than a dependency gate and supports forward and reverse lookup. Scope: in scope, mapped to FR-004 and FR-005.
- **DESIGN-REQ-005** (`docs/Tasks/TaskRemediation.md` sections 7.4 and 7.5): Invalid targets, malformed fields, unsupported authority modes, incompatible policies, and convenience-route submissions are handled through the same canonical create contract and validation boundary. Scope: in scope, mapped to FR-006 and FR-007.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task-shaped execution create path MUST accept a nested `task.remediation` object for `MoonMind.Run` creation.
- **FR-002**: The normalized execution payload MUST preserve canonical remediation data under `initialParameters.task.remediation`.
- **FR-003**: When `task.remediation.target.runId` is omitted, the system MUST resolve and persist the target execution's current run ID before workflow start.
- **FR-004**: Successful remediation creation MUST persist a directed relationship from remediation execution to target execution that includes remediation workflow ID, remediation run ID, target workflow ID, target run ID, mode, authority mode, status, and trigger type.
- **FR-005**: Remediation relationship data MUST be queryable from remediation-to-target and target-to-remediation read paths, including pinned run identity and compact status/action/outcome fields.
- **FR-006**: Remediation create validation MUST reject malformed self-targets, run IDs used as workflow IDs, missing or invisible targets, unsupported target types, invalid task run IDs, unsupported authority modes, incompatible action policies, and disallowed nested remediation targets before workflow start.
- **FR-007**: The convenience route `POST /api/executions/{workflowId}/remediation` MUST expand into the same canonical task-shaped create contract as `POST /api/executions`.
- **FR-008**: Remediation links MUST NOT create dependency edges, dependency wait requirements, or success gates against the target execution.

### Key Entities

- **Remediation Execution**: A `MoonMind.Run` created with a canonical `task.remediation` payload.
- **Target Execution**: The existing logical execution selected for remediation.
- **Pinned Target Run**: The concrete run snapshot resolved and stored at create time.
- **Remediation Relationship**: A durable directed link between remediation and target executions with compact status/action/outcome metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove valid remediation creation preserves `initialParameters.task.remediation` and persists exactly one directed relationship.
- **SC-002**: Tests prove omitted `target.runId` values are resolved and stored before workflow start.
- **SC-003**: Tests prove malformed targets, unsupported authority modes, incompatible policies, invalid task run IDs, invisible targets, and nested remediation are rejected before workflow start.
- **SC-004**: Tests prove remediation relationships are queryable in both inbound and outbound directions with pinned target run identity.
- **SC-005**: Tests prove remediation creation does not create dependency edges or wait on target success.
- **SC-006**: Tests prove `POST /api/executions/{workflowId}/remediation` expands into the canonical task-shaped create contract.
