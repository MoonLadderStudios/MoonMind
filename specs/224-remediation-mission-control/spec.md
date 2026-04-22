# Feature Specification: Remediation Mission Control Surfaces

**Feature Branch**: `224-remediation-mission-control`
**Created**: 2026-04-22
**Status**: Draft
**Input**: Jira Orchestrate for MM-437.

Source story: STORY-007.
Source summary: Show remediation creation, evidence, links, and approvals in Mission Control.
Source Jira issue: unknown.
Original brief reference: not provided.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside the breakdown task.

## User Story - Remediation Mission Control Surfaces

**Summary**: As a Mission Control operator, I want to create remediation tasks from target execution views and inspect remediation links, evidence, and approvals in task detail so I can understand and govern remediation work without leaving Mission Control.

**Goal**: Mission Control exposes the remediation relationship as an operator-visible workflow: target executions can start remediation, target and remediation task detail pages show the relationship in both directions, remediation evidence artifacts are directly reachable, and approval-gated remediation actions show review state and decision affordances.

**Independent Test**: Render a target execution with inbound remediation links and a remediation execution with outbound target metadata, context evidence, action artifacts, and approval events. The story passes when Mission Control shows create remediation entrypoints, bidirectional links, evidence artifact access, approval state, and safe degraded states without changing the underlying task execution contract.

**Acceptance Scenarios**:

1. **Given** an operator views a failed, attention-required, stuck, provider-slot, or session-problem execution, **When** the task detail page renders, **Then** Mission Control shows a Create remediation task action that starts the canonical remediation create flow for that target workflow.
2. **Given** a target execution has remediation tasks, **When** the target task detail page renders, **Then** Mission Control shows a Remediation Tasks panel with links, status, authority mode, latest action, resolution, active lock state, and last-updated time for each remediation.
3. **Given** a remediation execution targets another execution, **When** the remediation task detail page renders, **Then** Mission Control shows a Remediation Target panel with the target link, pinned run ID, selected steps, current target state, evidence bundle link, allowed actions summary, approval state, and lock state.
4. **Given** remediation context, decision log, action request/result, verification, or summary artifacts are linked, **When** an operator reviews remediation evidence, **Then** Mission Control presents direct artifact links and labels them by remediation evidence type without exposing raw storage paths, presigned URLs, or unbounded log bodies.
5. **Given** a remediation task is approval-gated or requests a high-risk action, **When** the task detail page renders, **Then** Mission Control shows the proposed action, preconditions, blast-radius summary, approval state, and approve/reject affordances backed by the existing audit trail.
6. **Given** remediation link or evidence data is partial, missing, or degraded, **When** Mission Control renders the remediation surfaces, **Then** it shows bounded empty/degraded states without hiding the task detail page or implying approval/action success.

### Edge Cases

- Target executions without remediation links show an unobtrusive empty state and still expose the create remediation action when the target status supports remediation.
- Remediation executions without a linked context artifact show the target relationship and a missing-evidence state rather than a broken artifact link.
- Approval affordances are disabled or replaced with read-only status when the current operator lacks approval permission.
- Inbound and outbound link fetch failures show retryable degraded states without losing existing task detail content.
- Artifact links use existing artifact preview/download authorization and never surface raw storage identifiers.
- Long workflow IDs, run IDs, action labels, and artifact names remain readable and contained on mobile and desktop.

## Assumptions

- This story implements Mission Control visibility and operator handoff surfaces only; it does not create a new remediation execution workflow type.
- Existing remediation create, context artifact, and evidence-tool slices provide the canonical backend foundations where already implemented.
- Approval decision persistence may reuse an existing audit/control-event surface if one is available; otherwise this story adds only the narrow API/UI contract needed for approval-gated remediation display and decision submission.
- The source Jira issue key is unknown in the task instruction, so artifacts preserve MM-437 as the orchestration target while recording the issue field as unknown.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/Tasks/TaskRemediation.md` section 15.1): Mission Control should expose Create remediation task from task detail, failed task banners, attention-required surfaces, stuck task surfaces, and provider-slot or session problem surfaces. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-002** (`docs/Tasks/TaskRemediation.md` section 15.1): The create UI should let operators choose the pinned target run, selected steps, authority mode, live-follow mode, action policy, and evidence preview. Scope: in scope, mapped to FR-002 and FR-003.
- **DESIGN-REQ-003** (`docs/Tasks/TaskRemediation.md` sections 14.4 and 15.2): Target task detail should show inbound remediation metadata including links, status, authority mode, latest action, resolution, active lock, and last-updated information. Scope: in scope, mapped to FR-004.
- **DESIGN-REQ-004** (`docs/Tasks/TaskRemediation.md` section 15.3): Remediation task detail should show the target link, pinned run ID, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state. Scope: in scope, mapped to FR-005.
- **DESIGN-REQ-005** (`docs/Tasks/TaskRemediation.md` sections 14.1 and 15.4): Mission Control should provide direct operator access to remediation context, decision log, action request/result, verification, and summary artifacts while honoring artifact presentation safety. Scope: in scope, mapped to FR-006 and FR-007.
- **DESIGN-REQ-006** (`docs/Tasks/TaskRemediation.md` sections 14.5 and 15.6): Approval-gated remediation should show proposed action, preconditions, expected blast radius, approve/reject controls, and audit-trail-backed decision state. Scope: in scope, mapped to FR-008 and FR-009.
- **DESIGN-REQ-007** (`docs/Tasks/TaskRemediation.md` sections 16.3 through 16.5): Partial evidence, unavailable live follow, or missing artifact refs should degrade safely and visibly. Scope: in scope, mapped to FR-010.
- **DESIGN-REQ-008** (`docs/UI/MissionControlDesignSystem.md` section 12): Remediation panels, evidence links, and approval controls must preserve readable contrast, visible focus, reduced-motion behavior, and fallback rendering. Scope: in scope, mapped to FR-011.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mission Control task detail MUST expose a Create remediation task action for eligible target executions, including failed, attention-required, stuck, provider-slot, and session problem states.
- **FR-002**: The Create remediation action MUST use the canonical remediation create route or task-shaped create contract and MUST prefill the target workflow ID, current run ID, selected-step context, authority mode, live-follow mode, action policy, and evidence preview where available.
- **FR-003**: The create surface MUST distinguish troubleshooting-only remediation from admin remediation and show the evidence and policy choices before submission.
- **FR-004**: Target execution detail MUST show inbound remediation tasks with remediation task links, status, authority mode, latest action, resolution, active lock state, and last-updated time.
- **FR-005**: Remediation execution detail MUST show outbound target metadata including target execution link, pinned run ID, selected steps, current target state, evidence context link, allowed actions summary, approval state, and lock state.
- **FR-006**: Remediation detail surfaces MUST show direct links to remediation context, decision log, action request, action result, verification, and summary artifacts when those refs are present.
- **FR-007**: Remediation evidence links MUST use existing artifact authorization and preview behavior and MUST NOT expose raw storage paths, presigned URLs, storage keys, local filesystem paths, or unbounded raw logs in UI state.
- **FR-008**: Approval-gated remediation surfaces MUST show proposed action kind, preconditions, expected blast radius, risk tier, current approval decision, and timestamps from the audit trail.
- **FR-009**: Operators with approval permission MUST be able to approve or reject pending remediation action requests from Mission Control, and operators without permission MUST see read-only status without enabled decision controls.
- **FR-010**: Missing remediation links, context artifacts, evidence refs, live-follow support, or approval audit rows MUST render explicit degraded or empty states without breaking task detail rendering.
- **FR-011**: Remediation creation, link, evidence, and approval surfaces MUST retain Mission Control accessibility, reduced-motion, mobile containment, and fallback styling guarantees.
- **FR-012**: Existing task-list, task-detail, artifact, timeline, live-log, and execution create behavior MUST remain unchanged for non-remediation executions.
- **FR-013**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-437, STORY-007, the source summary, and the unknown Jira issue status from the orchestration input.

### Key Entities

- **Remediation Create Surface**: The task-detail action and modal or form state that expands operator choices into the canonical remediation create contract.
- **Inbound Remediation Link**: A target-to-remediator relationship shown on target task detail.
- **Outbound Remediation Target**: A remediation-to-target relationship shown on remediation task detail.
- **Remediation Evidence Link**: An artifact-backed evidence reference shown through existing artifact authorization and preview behavior.
- **Remediation Approval State**: The proposed action, decision state, permission state, and audit-trail metadata for approval-gated remediation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: UI tests prove eligible target detail states expose Create remediation task and submit the canonical remediation create payload with pinned target context.
- **SC-002**: UI/API tests prove target task detail renders inbound remediation links with status, authority, latest action, resolution, lock, and update metadata.
- **SC-003**: UI/API tests prove remediation task detail renders outbound target metadata, selected steps, context evidence link, allowed actions, approval state, and lock state.
- **SC-004**: UI tests prove remediation evidence artifact links are labeled by evidence type and routed through existing artifact presentation behavior without raw storage identifiers.
- **SC-005**: UI/API tests prove approval-gated remediation shows proposed action, preconditions, blast radius, risk tier, current decision, and permission-correct approve/reject controls.
- **SC-006**: UI tests prove missing or degraded remediation data renders explicit empty/degraded states and does not break existing task detail rendering.
- **SC-007**: Existing task-list, task-detail, artifact, live-log, and create-page tests continue to pass for non-remediation executions.
- **SC-008**: Traceability verification confirms MM-437, STORY-007, source summary, and DESIGN-REQ-001 through DESIGN-REQ-008 are preserved in the MoonSpec artifacts.
