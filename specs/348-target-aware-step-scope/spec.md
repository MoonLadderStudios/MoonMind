# Feature Specification: Target-Aware Step Execution Scope

**Feature Branch**: `348-target-aware-step-scope`
**Created**: 2026-05-13
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-649 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-649 MoonSpec Orchestration Input

## Source

- Jira issue: MM-649
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Target-aware step execution & AgentRun child-workflow scope inheritance
- Priority: Medium
- Labels: moonmind-workflow-mm-a1fb7aa8-954b-4c59-acc2-c0a2c5339282
- Linked issues: None
- Trusted fetch tool: `jira.get_issue`
- Trusted response artifact: `/work/agent_jobs/mm:9776de74-4361-4351-8716-2fcc1af2ca51/artifacts/moonspec-inputs/MM-649-trusted-jira-get-issue-summary.json`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or `recommendedPresetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-649 from MM project
Summary: Target-aware step execution & AgentRun child-workflow scope inheritance
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-649 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-649: Target-aware step execution & AgentRun child-workflow scope inheritance

Source Reference
Source Document: docs/Tasks/TaskArchitecture.md
Source Title: Task Architecture (Control Plane)
Source Sections:
- 8.3 Step execution responsibilities
- 8.4 Child workflow responsibilities
- 12.2 MoonMind.AgentRun
- Invariant 10
Coverage IDs:
- DESIGN-REQ-021
- DESIGN-REQ-022

As an execution-plane engineer, I want each step to consume task-level objective context plus only its own step-scoped image context (no cross-step leakage) and want MoonMind.AgentRun child workflows to receive only the prepared context for the step they execute, never redefining or broadening attachment scope or target-binding semantics.

Acceptance Criteria
- Step execution receives only objective context plus its own step-scoped attachment context by default.
- No cross-step attachment leakage occurs even when prepare materialized all attachments in one workspace.
- MoonMind.AgentRun children receive only prepared context relevant to the assigned step.
- Parent MoonMind.Run remains the source of truth for attachment target binding.
- Child workflow logs/diagnostics do not redefine target binding semantics.

Requirements
Implement step-context dispatcher in workflow code that filters prepared context by target, and AgentRun child-input scoping.
"""

## User Story - Step-Scoped Execution Context

**Summary**: As an execution-plane engineer, I want each step and delegated AgentRun child to receive only the prepared context relevant to that step so that attachments cannot leak across step boundaries.

**Goal**: Step execution preserves target-aware attachment semantics by combining task-level objective context with only the current step's own prepared step-scoped context, including when execution is delegated to a child agent run.

**Independent Test**: Run or simulate a task with objective context and at least two steps that each have distinct prepared attachments, then verify each step runtime and AgentRun child receives the objective context plus only its own step-scoped prepared context, with diagnostics preserving the parent workflow as the target-binding authority.

**Acceptance Scenarios**:

1. **Given** a task has objective context and distinct step-scoped prepared attachments for multiple steps, **When** the first step begins execution, **Then** it receives the objective context plus only attachments targeted to that first step.
2. **Given** all attachments were materialized in one preparation workspace, **When** any individual step runtime context is assembled, **Then** unrelated step attachments remain excluded from that step's context.
3. **Given** a step is executed through a MoonMind.AgentRun child workflow, **When** the child input is prepared, **Then** the child receives only prepared context relevant to the represented step.
4. **Given** child workflow logs or diagnostics are emitted, **When** an operator reviews them, **Then** they describe consumed context without redefining or broadening attachment target-binding semantics.
5. **Given** a prepared context item cannot be associated with the current step or the task objective, **When** step execution context is assembled, **Then** the system rejects or omits it with explicit evidence rather than leaking it into the step.

### Edge Cases

- A step with no scoped attachments still receives relevant task-level objective context and no unrelated step context.
- Two steps may use attachments with the same filename or storage location pattern; filtering remains based on target binding, not filename.
- A child run may be started after prior steps completed; it still receives only context for the represented child step.
- Diagnostic artifacts may summarize excluded context counts but must not expose unrelated attachment contents to the child step.

## Assumptions

- Objective context is relevant to each step unless a later policy explicitly narrows objective visibility.
- Prepare-time materialization and manifest generation are handled by existing or preceding target-aware preparation work; this story focuses on execution-time dispatch and child-input scoping.
- Parent MoonMind.Run has access to prepared target metadata before step runtime or child workflow dispatch.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tasks/TaskArchitecture.md` section 8.3 requires step execution to consume task-level objective context when relevant, consume only the current step's step-scoped image context by default, and avoid leaking unrelated step attachments into the wrong step. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-008. Original Jira coverage ID: DESIGN-REQ-021.
- **DESIGN-REQ-002**: Source `docs/Tasks/TaskArchitecture.md` section 8.4 requires MoonMind.AgentRun parent-child boundaries to preserve target-aware prepared context, keep the parent workflow as the source of truth for attachment target binding, send children only context relevant to their step, and prevent child logs or diagnostics from redefining target semantics. Scope: in scope. Maps to FR-005, FR-006, FR-007, and FR-008. Original Jira coverage ID: DESIGN-REQ-022.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST assemble step runtime context from task-level objective context plus only prepared context explicitly targeted to the current step.
- **FR-002**: System MUST exclude prepared context targeted to other steps from the current step runtime context by default.
- **FR-003**: System MUST preserve target-aware filtering even when preparation materialized objective and step attachments in the same workspace.
- **FR-004**: System MUST use explicit target metadata, not filename, path position, step order, or text content, to decide which prepared context belongs to a step.
- **FR-005**: System MUST pass MoonMind.AgentRun child workflows only the prepared context relevant to the child workflow's represented step.
- **FR-006**: Parent MoonMind.Run MUST remain the authoritative source of attachment target binding for child workflow dispatch.
- **FR-007**: Child workflow logs and diagnostics MUST NOT redefine, broaden, or override attachment target-binding semantics.
- **FR-008**: System MUST produce verification evidence showing unrelated step-scoped context is absent from step runtime inputs and AgentRun child inputs.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-649` and the canonical Jira preset brief.

### Key Entities

- **Prepared Context Item**: A lightweight reference or derived context artifact with target metadata identifying whether it belongs to the task objective or a specific step.
- **Step Runtime Context**: The bounded context delivered to a step, composed from relevant objective context and the current step's prepared context.
- **AgentRun Child Input**: The prepared execution payload sent from MoonMind.Run to a child workflow for one represented step.
- **Target Binding Authority**: The parent MoonMind.Run state and prepared metadata that define which attachments belong to each objective or step target.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a task with at least two step-scoped attachment sets, 100% of inspected step runtime contexts exclude attachments targeted to other steps.
- **SC-002**: AgentRun child workflow input verification confirms 100% of child runs receive only the represented step's prepared context plus relevant objective context.
- **SC-003**: Boundary tests cover at least one same-workspace materialization case where unrelated prepared attachments are present but excluded from a step.
- **SC-004**: Diagnostic evidence identifies the parent workflow as the target-binding authority and contains no child-side target redefinition.
- **SC-005**: Traceability review confirms `MM-649`, the canonical Jira preset brief, DESIGN-REQ-001, and DESIGN-REQ-002 remain preserved in MoonSpec artifacts and final verification evidence.
