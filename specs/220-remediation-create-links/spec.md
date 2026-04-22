# Feature Specification: Remediation Create Links

**Feature Branch**: `220-remediation-create-links`
**Created**: 2026-04-21
**Status**: Draft
**Input**: Jira Orchestrate for MM-431 using `docs/tmp/jira-orchestration-inputs/MM-431-moonspec-orchestration-input.md` as the canonical MoonSpec orchestration input.

Source story: STORY-001.
Source summary: Accept remediation create requests and persist target links.
Source Jira issue: MM-431.
Original brief reference: `docs/tmp/jira-orchestration-inputs/MM-431-moonspec-orchestration-input.md`.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside the breakdown task.

## Original Preset Brief

```text
# MM-431 MoonSpec Orchestration Input

## Source

- Jira issue: MM-431
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Accept remediation create requests and persist target links
- Labels: `moonmind-workflow-mm-a59f3b1d-da4d-4600-86a8-1d582ee67fe8`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-431 from MM project
Summary: Accept remediation create requests and persist target links
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-431 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-431: Accept remediation create requests and persist target links

Source Reference
- Source Document: docs/Tasks/TaskRemediation.md
- Source Title: Task Remediation
- Source Sections:
  - 5.1 Remediation tasks remain MoonMind.Run
  - 5.2 Remediation is a relationship, not a dependency
  - 7. Submission contract
  - 8. Identity, linkage, and read models
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-024

User Story
As an operator, I can create a remediation task that targets another execution so MoonMind records an explicit troubleshooting relationship without treating the target as a dependency gate.

Acceptance Criteria
- POST /api/executions accepts a task.remediation payload and stores it as initialParameters.task.remediation before MoonMind.Run starts.
- target.workflowId is required and a concrete target.runId is resolved and persisted when omitted by the caller.
- Malformed self-reference, unsupported target workflow types, invalid taskRunIds, unsupported authorityMode values, incompatible actionPolicyRef, and disallowed nested remediation are rejected with structured errors.
- A remediation link record supports remediator-to-target and target-to-remediator lookup including mode, authorityMode, current status, pinned run identity, lock holder, latest action summary, and outcome fields.
- The convenience route expands into the same canonical create contract and does not introduce a second durable payload shape.

Requirements
- Remediation tasks remain MoonMind.Run executions with additional nested task.remediation semantics.
- Remediation links are relationships, not dependsOn gates, and start independently of target success.
- The system exposes inbound and outbound remediation lookup APIs for execution detail surfaces.
- Canonical source data remains upstream of any derived read model.

Implementation Notes
- Preserve MM-431 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation task semantics, submission contract, identity, linkage, and read model behavior.
- Scope implementation to accepting remediation create requests, persisting the canonical task.remediation payload, resolving target run identity, validating unsupported or malformed remediation submissions, and exposing durable remediation link lookup data.
- Keep remediation links as explicit troubleshooting relationships rather than dependency gates; target execution success must not be required for remediation task startup.
- Keep the convenience route as an expansion into the canonical create contract, not a second durable payload shape.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-431 is blocked by MM-432, whose embedded status is Backlog.
```

## User Story - Persist Remediation Targets

**Summary**: As a MoonMind operator, I want task creation to accept a remediation target and persist the target relationship so remediation tasks can be inspected from either side.

**Goal**: A task submitted with `task.remediation.target.workflowId` starts through the normal execution create path, pins the current target run, and records a durable non-dependency relationship between the new remediation run and the target run.

**Independent Test**: Submit a `MoonMind.Run` task containing `task.remediation.target.workflowId` for an existing run and verify the created run keeps the remediation payload and exposes a persisted link for outbound and inbound lookup.

**Acceptance Scenarios**:

1. **Given** an existing visible `MoonMind.Run` target, **When** a new `MoonMind.Run` is created with `task.remediation.target.workflowId`, **Then** the create path persists a remediation link from the new workflow to the target workflow and target run.
2. **Given** the remediation request omits `target.runId`, **When** creation succeeds, **Then** the persisted link records the target execution's current run ID at create time.
3. **Given** a malformed remediation request with no target workflow ID, **When** the task is submitted, **Then** creation fails before the workflow starts and no remediation link is written.
4. **Given** a target workflow that does not exist or is not a `MoonMind.Run`, **When** the task is submitted, **Then** creation fails with a validation error and no remediation link is written.
5. **Given** a remediation request with unsupported authority mode, incompatible action policy, malformed task run IDs, or a remediation target that is itself a remediation task, **When** the task is submitted, **Then** creation fails with a structured validation error before workflow start.
6. **Given** a caller submits through the remediation convenience route, **When** the route expands the request, **Then** it produces the same canonical `POST /api/executions` task create contract with `task.remediation.target.workflowId` set to the target workflow.

### Edge Cases

- A remediation request must not use a target run ID as the workflow target.
- A caller-provided `target.runId` must match the current known target run because historical run lookup is not available in this slice.
- Remediation links are relationships, not dependencies, so they must not add `dependsOn` gates or dependency fan-out behavior.
- `target.taskRunIds` must be a list of non-empty strings when supplied.
- Nested remediation targets are rejected in this slice because automatic remediation of remediation tasks is disabled by default.

## Assumptions

- This story implements the create-time persistence slice only. Evidence bundle creation, action policies, locks, and remediation tool surfaces remain out of scope.
- Visibility reuses the existing execution owner boundary used by dependency validation.
- The durable link table is sufficient for the first forward and reverse lookup methods; UI rendering can be layered on later.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` section 7.4): Create-time validation must require `task.remediation.target.workflowId`. Scope: in scope, mapped to FR-001 and FR-004.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` sections 6 and 8.1): Create time must resolve and persist a concrete target run ID. Scope: in scope, mapped to FR-002.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 5.2 and 8.2): Remediation is a directed relationship, not a dependency, and must support forward and reverse lookup. Scope: in scope, mapped to FR-003 and FR-006.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` sections 7.4 and 7.3): Create-time validation must reject unsupported target types, malformed task run IDs, unsupported authority modes, incompatible action policy references, and disallowed nested remediation. Scope: in scope, mapped to FR-004, FR-008, FR-009, FR-010, and FR-011.
- **DESIGN-REQ-005** (`docs/Tasks/TaskRemediation.md` section 7.5): The remediation convenience route expands into the same canonical execution create contract as `POST /api/executions`. Scope: in scope, mapped to FR-012.
- **DESIGN-REQ-024** (`docs/Tasks/TaskRemediation.md` sections 8.3 and 8.4): Canonical remediation link data remains upstream of derived read models and includes compact status/action/outcome fields. Scope: in scope for durable link fields, mapped to FR-003.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The task-shaped and direct execution create paths MUST accept a nested `task.remediation` object for `MoonMind.Run` creation.
- **FR-002**: When `task.remediation.target.runId` is absent, the system MUST persist the target execution's current run ID on the remediation link.
- **FR-003**: The system MUST persist one durable remediation link containing remediation workflow ID, remediation run ID, target workflow ID, target run ID, mode, authority mode, status, and trigger type when remediation creation succeeds.
- **FR-004**: The system MUST reject remediation create requests that omit `target.workflowId`, reference a run ID instead of a workflow ID, reference a missing execution, reference a non-`MoonMind.Run` execution, or specify a target run ID that does not match the current target run.
- **FR-005**: Remediation link persistence MUST occur in the same create transaction as the canonical execution source record so failed validation or persistence cannot leave orphan links.
- **FR-006**: The service layer MUST provide forward lookup from remediation workflow to target and reverse lookup from target workflow to remediation workflows.
- **FR-007**: Remediation create handling MUST NOT create dependency edges or alter dependency wait behavior.
- **FR-008**: Remediation create validation MUST reject unsupported `authorityMode` values.
- **FR-009**: Remediation create validation MUST reject unsupported or incompatible `actionPolicyRef` values.
- **FR-010**: Remediation create validation MUST reject malformed `target.taskRunIds` values.
- **FR-011**: Remediation create validation MUST reject nested remediation targets when the target execution is itself a remediation task.
- **FR-012**: The convenience route `POST /api/executions/{workflowId}/remediation` MUST expand into the canonical task-shaped create contract without introducing a second durable payload shape.

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
- **SC-005**: Unit tests prove unsupported authority modes, incompatible action policies, malformed task run IDs, and nested remediation targets fail before workflow start.
- **SC-006**: Router unit tests prove the remediation convenience route expands into canonical task-shaped execution creation.
