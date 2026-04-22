# MM-456 MoonSpec Orchestration Input

## Source

- Jira issue: MM-456
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish remediation lifecycle phases, artifacts, summaries, and audit events
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-456 from MM project
Summary: Publish remediation lifecycle phases, artifacts, summaries, and audit events
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-456 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-456: Publish remediation lifecycle phases, artifacts, summaries, and audit events

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 13. Runtime lifecycle
  - 14. Artifacts, summaries, and audit
  - 16. Failure modes and edge cases
- Coverage IDs:
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-019
  - DESIGN-REQ-022
  - DESIGN-REQ-023

User Story
As an operator or reviewer, I can inspect a remediation run from evidence collection through verification because each phase, decision, action, result, and final outcome leaves durable artifacts and queryable audit evidence.

Acceptance Criteria
- remediationPhase values reflect collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, or failed as the run progresses.
- Required remediation artifacts are published with expected artifact_type values and obey artifact preview/redaction metadata rules.
- run_summary.json includes the remediation block with target identity, mode, authorityMode, actionsAttempted, resolution, lockConflicts, approvalCount, evidenceDegraded, and escalated fields.
- Target-managed session or workload mutations continue to produce native continuity/control artifacts in addition to remediation audit artifacts.
- Control-plane audit events record actor, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
- Cancellation or remediation failure does not mutate the target except for already-requested actions and attempts final summary publication and lock release.
- Continue-As-New preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.

Requirements
- Existing MoonMind.Run state remains the top-level state source.
- Artifacts remain operator-facing deep evidence; audit rows remain compact queryable trail.
- Target-side linkage summary metadata is available for downstream detail views.

Relevant Implementation Notes
- Preserve MM-456 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation runtime lifecycle phases, artifact publication, final summaries, audit events, cancellation behavior, failure handling, and Continue-As-New preservation.
- Keep MoonMind.Run as the top-level execution state source; remediation-specific phase values should complement that state rather than replace it.
- Publish remediation artifacts with bounded metadata and existing artifact preview/redaction rules.
- Ensure final run summaries include the remediation block with target identity, mode, authority mode, action attempts, resolution, lock conflicts, approval count, degraded evidence, and escalation state.
- Preserve native managed-session or workload continuity/control artifacts when remediation mutates those targets.
- Record compact queryable audit events for remediation actions, approvals, risk tiers, actors, execution principals, target workflow/run identity, timestamps, and bounded metadata.
- On cancellation or remediation failure, avoid new target mutation except for already-requested actions; still attempt final summary publication and lock release.
- Preserve target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor across Continue-As-New.

Non-Goals
- Replacing MoonMind.Run as the top-level state source.
- Treating audit rows as a replacement for operator-facing artifact evidence.
- Dropping native target continuity or control artifacts when remediation touches managed sessions or workloads.
- Mutating targets after cancellation or failure except for already-requested actions.
- Losing remediation context across Continue-As-New.

Validation
- Verify remediationPhase values reflect collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, or failed as the run progresses.
- Verify required remediation artifacts are published with expected artifact_type values and obey artifact preview/redaction metadata rules.
- Verify run_summary.json includes the remediation block with target identity, mode, authorityMode, actionsAttempted, resolution, lockConflicts, approvalCount, evidenceDegraded, and escalated fields.
- Verify target-managed session or workload mutations continue to produce native continuity/control artifacts in addition to remediation audit artifacts.
- Verify control-plane audit events record actor, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
- Verify cancellation or remediation failure does not mutate the target except for already-requested actions and attempts final summary publication and lock release.
- Verify Continue-As-New preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-456 blocks MM-455, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-456 is blocked by MM-457, whose embedded status is Backlog.

Needs Clarification
- None
