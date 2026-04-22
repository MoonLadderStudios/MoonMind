# MM-437 MoonSpec Orchestration Input

## Source

- Jira issue: unknown
- Jira orchestration target: MM-437
- Source story: STORY-007
- Summary: Show remediation creation, evidence, links, and approvals in Mission Control.
- Original brief reference: not provided.
- Canonical source: task instruction supplied to the managed Jira Orchestrate run.

## Canonical MoonSpec Feature Request

Jira Orchestrate for MM-437.

Source story: STORY-007.
Source summary: Show remediation creation, evidence, links, and approvals in Mission Control.
Source Jira issue: unknown.
Original brief reference: not provided.

Use the existing Jira Orchestrate workflow for this Jira issue. Do not run implementation inline inside the breakdown task.

## Source Reference

- Source Document: `docs/Tasks/TaskRemediation.md`
- Source Title: Task Remediation
- Source Sections:
  - 14.1 Required remediation artifacts
  - 14.4 Target-side linkage summary
  - 14.5 Control-plane audit events
  - 15.1 Create flow
  - 15.2 Target task detail
  - 15.3 Remediation task detail
  - 15.4 Evidence presentation
  - 15.6 Operator handoff
  - 16.3 Historical target has only merged logs
  - 16.4 Missing or partial artifact refs
  - 16.5 Live follow unavailable
- Additional Source Document: `docs/UI/MissionControlDesignSystem.md`
- Additional Source Section:
  - 12. Accessibility and performance

## User Story

As a Mission Control operator, I want to create remediation tasks from target execution views and inspect remediation links, evidence, and approvals in task detail so I can understand and govern remediation work without leaving Mission Control.

## Acceptance Criteria

- Mission Control exposes Create remediation task from eligible target detail/problem surfaces and expands submissions into the canonical remediation create contract.
- Target task detail shows inbound remediation task links, status, authority mode, latest action, resolution, active lock, and update metadata.
- Remediation task detail shows target execution link, pinned target run ID, selected steps, current target state, evidence bundle, allowed actions, approval state, and lock state.
- Remediation evidence artifacts are directly reachable through existing artifact presentation and never expose raw storage paths, presigned URLs, storage keys, local paths, or unbounded logs.
- Approval-gated remediation shows proposed action, preconditions, expected blast radius, approve/reject controls when permitted, and audit-backed decision state.
- Missing or partial remediation evidence degrades visibly without breaking task detail rendering.

## Implementation Notes

- Preserve MM-437, STORY-007, source summary, and unknown Jira issue status in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use existing remediation create, link, context artifact, evidence tool, artifact presentation, and Mission Control task-detail surfaces where possible.
- Scope implementation to Mission Control visibility and narrow trusted API/read-model or approval handoff surfaces required by the UI.
- Do not implement automatic self-healing, new raw admin action kinds, unbounded evidence import, or direct raw storage access.
