# Feature Specification: Remediation Mission Control Panels

**Feature Branch**: `324-remediation-mission-control-panels`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-624 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-624 MoonSpec Orchestration Input

## Source

- Jira issue: MM-624
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose Mission Control remediation creation, review, and handoff panels
- Labels: moonmind-workflow-mm-d644bfaa-e9fb-4d63-9dff-519fed1a09b7
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or recommended preset instructions.

## Canonical MoonSpec Feature Request

Jira issue: MM-624 from MM project
Summary: Expose Mission Control remediation creation, review, and handoff panels
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-624 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-624: Expose Mission Control remediation creation, review, and handoff panels

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 15. Mission Control UX
- 16. Failure modes and edge cases
Coverage IDs:
- DESIGN-REQ-010
- DESIGN-REQ-024
- DESIGN-REQ-025
- DESIGN-REQ-028

As an operator, I can launch, monitor, inspect, and approve remediation from Mission Control so that target and remediation relationships, evidence, live observation, allowed actions, locks, and approvals are understandable from both task detail pages.

Acceptance Criteria
- Operators can start remediation from all specified task/problem surfaces and the submitted payload matches canonical task.remediation.
- Target task detail shows remediation links, status, authority, last action, resolution, and active lock.
- Remediation task detail shows target link, pinned run, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
- Live follow UI is labeled as observation, preserves sequence position, shows reconnect/epoch state, and falls back to durable artifacts.
- Approval handoff shows action, preconditions, expected blast radius, approve/reject controls, and persists the decision to the audit trail.

Requirements
- Mission Control makes remediation visible in both directions.
- Operators can review evidence and action decisions without raw backend access.
- High-risk/approval-gated flows have an explicit handoff.

## Relevant Linked Issues

- MM-623 (blocks): Publish remediation audit artifacts, summaries, and queryable events [Story, Done]
- MM-625 (is blocked by): Ship bounded manual remediation v1 and keep future automation policy-gated [Story, Backlog]

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

## User Story - Operate Remediation From Mission Control

**Summary**: As an operator, I want Mission Control to expose remediation creation, relationship, observation, evidence, lock, and approval panels so that remediation work is understandable and controllable from both target and remediation task detail pages.

**Goal**: Operators can start remediation from relevant problem surfaces, understand bidirectional target/remediation relationships, inspect bounded evidence and live observation state, and approve or reject high-risk handoffs without using raw backend access.

**Independent Test**: Can be fully tested by walking an operator through remediation creation from each supported Mission Control surface, then inspecting the target and remediation task detail views across normal, degraded-evidence, live-follow, lock-conflict, and approval-gated scenarios.

**Acceptance Scenarios**:

1. **Given** an operator views a task detail, failure banner, attention-required surface, stuck task surface, or applicable provider/session problem surface, **When** they start remediation, **Then** Mission Control captures the pinned target run, selected step scope, remediation authority mode, live-follow preference, action policy, and evidence preview before submission.
2. **Given** remediation has been created for a target task, **When** an operator opens the target task detail, **Then** a remediation panel shows remediation task links, status, authority mode, last action, resolution, and active lock state.
3. **Given** an operator opens a remediation task detail, **When** target context is available, **Then** a target panel shows the target execution link, pinned run, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
4. **Given** live follow is active for remediation observation, **When** the operator watches or reloads the detail view, **Then** the UI labels the stream as observation, preserves sequence position, exposes reconnect and epoch state, and falls back to durable artifacts if live streaming is unavailable.
5. **Given** a remediation task is approval-gated or proposes a high-risk action, **When** an operator reviews the handoff, **Then** Mission Control shows the proposed action, preconditions, expected blast radius, approve/reject controls, and records the decision in the audit trail.
6. **Given** target evidence, links, locks, or live observation are degraded or unavailable, **When** the operator inspects either task detail page, **Then** the UI shows bounded degraded states and available durable evidence instead of hiding the relationship or implying complete evidence.

### Edge Cases

- The target execution may be missing or not visible; remediation creation must fail validation or surface an early structured error instead of creating an unlinked remediation task.
- The target may rerun after remediation starts; the UI must preserve and display the pinned snapshot rather than silently retargeting.
- Historical targets may only have merged logs or partial artifacts; the UI must indicate degraded evidence and still expose safe available artifacts.
- Live follow may be unavailable or disconnected; durable logs, diagnostics, summaries, and artifact links remain the fallback.
- Another remediation task may own the mutation lock; the UI must expose the conflict and permitted operator path instead of allowing concurrent mutation by default.
- Approval handoff preconditions may no longer hold; the decision state must surface no-op or precondition-failed outcomes rather than silent success.

## Assumptions

- Runtime intent is required because Jira Orchestrate always runs as a runtime implementation workflow and the brief describes product behavior.
- The cited Task Remediation sections are source requirements for the selected Mission Control operator story, while unrelated remediation design sections are out of scope for this specification.
- Existing Mission Control authorization, artifact preview, redaction, and task detail navigation rules continue to apply to remediation panels.
- Live follow is observational and non-authoritative; durable artifacts remain the authoritative fallback evidence.

## Source Design Requirements

- **DESIGN-REQ-010** (`docs/Tasks/TaskRemediation.md` lines 1196-1262): Mission Control must expose remediation creation, target-side remediation panels, remediation-side target panels, evidence presentation, live observation state, and approval handoff controls. Scope: in scope. Mapped to FR-001 through FR-010.
- **DESIGN-REQ-024** (`docs/Tasks/TaskRemediation.md` lines 1270-1283): Missing targets, rerun targets, historical logs, partial artifacts, and unavailable live follow must produce explicit validation, degraded evidence, or durable fallback behavior. Scope: in scope. Mapped to FR-011 through FR-014.
- **DESIGN-REQ-025** (`docs/Tasks/TaskRemediation.md` lines 1285-1305): Lock conflicts, stale/precondition failures, non-runaway forced termination attempts, and failed remediation tasks must be visible as bounded states rather than silent success or unsafe mutation. Scope: in scope. Mapped to FR-015 through FR-018.
- **DESIGN-REQ-028** (`docs/Tasks/TaskRemediation.md` lines 1255-1262 and 1292-1305): Operator-visible approval, audit, final summary, and release-state outcomes must be preserved for handoffs and failure paths. Scope: in scope. Mapped to FR-010, FR-017, FR-018, and FR-019.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control MUST expose a remediation creation action from task detail, failed task banners, attention-required surfaces, stuck task surfaces, and applicable provider-slot or session problem surfaces.
- **FR-002**: The remediation creation flow MUST let operators select or review the pinned target run, step scope, authority mode, live-follow mode, action policy, and evidence preview before submission.
- **FR-003**: Remediation submissions from Mission Control MUST preserve the canonical task.remediation payload shape expected by the remediation workflow.
- **FR-004**: Target task details MUST show remediation task links, remediation status, authority mode, last action, resolution, and active lock state.
- **FR-005**: Remediation task details MUST show the target execution link, pinned target run, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
- **FR-006**: Mission Control MUST provide operator access to remediation context, target logs and diagnostics, decision logs, action request/result evidence, and verification artifacts through bounded product surfaces.
- **FR-007**: Live follow UI MUST be labeled as observation and MUST NOT be presented as authoritative remediation evidence.
- **FR-008**: Live follow UI MUST preserve sequence position across reloads and show reconnect and epoch state when live observation is active.
- **FR-009**: When live follow is unavailable, Mission Control MUST fall back to durable logs, diagnostics, summaries, and artifact references.
- **FR-010**: Approval handoff UI MUST show the proposed action, preconditions, expected blast radius, approve/reject controls, and persisted audit decision state for approval-gated or high-risk actions.
- **FR-011**: Remediation creation MUST fail validation or surface an early structured error when the target execution is missing or not visible.
- **FR-012**: Target reruns after remediation starts MUST preserve and display the pinned target snapshot rather than silently retargeting.
- **FR-013**: Historical targets with only merged logs MUST remain diagnosable and show degraded evidence state.
- **FR-014**: Missing or partial artifact references MUST be represented with bounded unavailable-evidence details while preserving safe available evidence.
- **FR-015**: Lock conflicts MUST expose the owning mutation-lock state and permitted operator outcome instead of allowing concurrent mutation by default.
- **FR-016**: Non-runaway forced termination attempts MUST require approval or be rejected by policy rather than being offered as a generic fallback.
- **FR-017**: Precondition failures, stale releases, and already-gone targets MUST surface no-op, precondition-failed, or verification-failed outcomes with evidence.
- **FR-018**: If a remediation task itself fails, Mission Control MUST expose the final remediation summary and lock-release state.
- **FR-019**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-624` and the original Jira preset brief for traceability.

### Key Entities

- **Remediation Creation Request**: The operator-selected target run, step scope, authority mode, live-follow choice, action policy, and evidence preview used to submit remediation.
- **Target Remediation Panel**: The target task detail summary of related remediation work, status, authority, last action, resolution, and active lock state.
- **Remediation Target Panel**: The remediation task detail summary of the pinned target, selected scope, current target state, evidence, allowed actions, approvals, and locks.
- **Live Observation State**: Non-authoritative live-follow metadata including stream availability, sequence position, reconnect state, and epoch boundaries.
- **Approval Handoff**: The operator review state for approval-gated or high-risk actions, including proposed action, preconditions, blast radius, decision controls, and persisted audit outcome.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of specified remediation entry surfaces expose a remediation creation path that preserves the required creation choices before submission.
- **SC-002**: 100% of representative target task details with remediation links show status, authority, last action, resolution, and lock state.
- **SC-003**: 100% of representative remediation task details show target link, pinned run, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
- **SC-004**: 100% of live-follow representative cases label observation state, preserve sequence position, show reconnect/epoch state where applicable, and fall back to durable artifacts when streaming is unavailable.
- **SC-005**: 100% of approval-gated or high-risk representative actions show proposed action, preconditions, expected blast radius, approve/reject controls, and persisted decision evidence.
- **SC-006**: 100% of degraded target, evidence, live-follow, lock, and precondition cases surface bounded operator-visible states without silent success or hidden relationship loss.
- **SC-007**: Traceability review confirms `MM-624`, the original Jira preset brief, linked issue context, and source coverage IDs DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-028 are preserved in MoonSpec artifacts.
