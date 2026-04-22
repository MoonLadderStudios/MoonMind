# MM-457 MoonSpec Orchestration Input

## Source

- Jira issue: MM-457
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Show task remediation creation, evidence, locks, approvals, and links in Mission Control
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-457 from MM project
Summary: Show task remediation creation, evidence, locks, approvals, and links in Mission Control
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-457 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-457: Show task remediation creation, evidence, locks, approvals, and links in Mission Control

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 15. Mission Control UX
  - 8.5 Reverse lookup API
  - 14.4 Target-side linkage summary
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-020
  - DESIGN-REQ-021
  - DESIGN-REQ-022
  - DESIGN-REQ-023

User Story
As an operator in Mission Control, I can create remediation tasks from relevant target surfaces and inspect target/remediator relationships, evidence, live observation, lock state, actions, approvals, and outcomes.

Acceptance Criteria
- Operators can start remediation from target task/problem surfaces and the submission payload matches the canonical contract.
- Target detail shows inbound remediators and active lock/action/resolution metadata.
- Remediation detail shows target identity, pinned run, selected steps, current state, evidence, action policy, approval, and lock information.
- Evidence links open through artifact/log APIs and honor redaction and visibility rules.
- Live follow UI is clearly non-authoritative, resumes sequence position where possible, and falls back to durable artifacts.
- Approval decisions for gated/high-risk actions are captured and visible in the audit trail.

Requirements
- Mission Control makes remediation relationships visible in both directions.
- UI cannot imply raw host/admin shell access; action surfaces are typed and policy-bound.
- Partial evidence and live-follow unavailability must be visible without deadlocking the user flow.

Relevant Implementation Notes
- Preserve MM-457 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation task creation, reverse lookup, target-side linkage summaries, remediation detail surfaces, evidence presentation, live follow behavior, approvals, and bounded operator handoff.
- Expose create-remediation entry points from task detail, failed task banners, attention-required surfaces, stuck task surfaces, and provider-slot or session problem surfaces where applicable.
- Ensure the create flow lets operators choose the pinned target run, choose all or selected steps, choose troubleshooting-only or admin remediation, choose live-follow mode, choose or review the action policy, and preview attached evidence.
- Show target-side remediation relationships through a Remediation Tasks panel with links, status, authority mode, last action, resolution, and active lock state.
- Show remediation-side target context with target execution link, pinned target run id, selected steps, current target state, evidence bundle link, allowed actions, approval state, and lock state.
- Provide direct access to remediation context artifacts, referenced target logs and diagnostics, remediation decision logs, action request/result artifacts, and verification artifacts through existing artifact/log APIs.
- Clearly label live-follow data as non-authoritative live observation, preserve sequence position where possible, make managed-session epoch boundaries explicit, and fall back to durable artifacts when streaming is unavailable.
- Capture approval-gated and high-risk action decisions with proposed action, preconditions, expected blast radius, approve/reject result, and audit-trail visibility.
- Implement reverse lookup surfaces for inbound and outbound remediation relationships, such as `GET /api/executions/{workflowId}/remediations?direction=inbound` and `GET /api/executions/{workflowId}/remediations?direction=outbound`, or equivalent repository-local API patterns.
- Keep host/admin shell capability out of the UI language and behavior; action surfaces must remain typed, policy-bound, and auditable.
- Surface partial evidence and unavailable live-follow streams as degraded states instead of blocking navigation or hiding remediation context.

Non-Goals
- Granting raw host/admin shell access from Mission Control remediation surfaces.
- Treating live-follow streams as authoritative durable evidence.
- Hiding partial evidence or unavailable streams behind loading states that deadlock the operator flow.
- Replacing artifact/log APIs or audit trails with remediation-specific ad hoc data paths.
- Dropping bidirectional target/remediator visibility when only one side of the relationship is currently active.

Validation
- Verify operators can start remediation from target task/problem surfaces and the submission payload matches the canonical contract.
- Verify target detail shows inbound remediators and active lock/action/resolution metadata.
- Verify remediation detail shows target identity, pinned run, selected steps, current state, evidence, action policy, approval, and lock information.
- Verify evidence links open through artifact/log APIs and honor redaction and visibility rules.
- Verify live follow UI is clearly non-authoritative, resumes sequence position where possible, and falls back to durable artifacts.
- Verify approval decisions for gated/high-risk actions are captured and visible in the audit trail.
- Verify inbound and outbound remediation lookup surfaces expose target/remediator relationships without relying on raw workflow history inspection.
- Verify partial evidence and unavailable live-follow states are visible to operators without deadlocking the user flow.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-457 blocks MM-456, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-457 is blocked by MM-458, whose embedded status is Backlog.

Needs Clarification
- None
