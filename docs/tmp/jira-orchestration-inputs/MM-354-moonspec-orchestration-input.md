# MM-354 MoonSpec Orchestration Input

## Source

- Jira issue: MM-354
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Expose merge automation status, settings, and artifacts
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-354 from MM project
Summary: Expose merge automation status, settings, and artifacts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-354 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-354: Expose merge automation status, settings, and artifacts

User Story
As an operator watching Mission Control, I need merge automation settings, blockers, resolver attempts, workflow links, and durable artifacts so I can understand why a PR-publishing task is waiting, merged, failed, or canceled.

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 20. Visibility and Artifacts
- 21. UI Contract
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-006
- DESIGN-REQ-018
- DESIGN-REQ-026
- DESIGN-REQ-027
- DESIGN-REQ-029

Story Metadata
- Story ID: STORY-005
- Dependencies: STORY-001, STORY-002, STORY-003, STORY-004

Independent Test
Run API/UI contract tests against a task projection containing merge automation metadata and artifact refs. Assert that Mission Control receives status, blockers, PR link, head SHA, cycles, resolver child links, and run summary data without a separate dependency/schedule resource.

Acceptance Criteria
- PR publish settings can enable automatic resolve/merge, configure external review signal trigger, optional Jira gate, and optional review providers.
- Parent task detail exposes status, PR URL, current blockers, latest head SHA, current cycle, resolver attempt history, and child workflow links.
- MoonMind.MergeAutomation writes `reports/merge_automation_summary.json`.
- MoonMind.MergeAutomation writes `artifacts/merge_automation/gate_snapshots/<cycle>.json` and `artifacts/merge_automation/resolver_attempts/<attempt>.json`.
- Parent `reports/run_summary.json` includes mergeAutomation enabled, status, prNumber, prUrl, childWorkflowId, resolverChildWorkflowIds, and cycles.
- Mission Control does not expose merge automation as a separate dependency or scheduling surface.

Requirements
- Operator-visible state must explain waiting and failed merge automation outcomes.
- Artifacts must be durable and inspectable.
- UI settings must remain scoped to PR publish configuration.

Implementation Notes
- Add or update the PR publish settings surface so merge automation configuration remains part of PR publishing, including automatic resolve/merge, trigger configuration, optional Jira gate, and optional review-provider configuration.
- Surface merge automation state on the parent task detail as compact operator-facing metadata: status, PR URL, current blockers, latest head SHA, cycle count, resolver attempt history, and child workflow links.
- Persist merge automation child artifacts at `reports/merge_automation_summary.json`, `artifacts/merge_automation/gate_snapshots/<cycle>.json`, and `artifacts/merge_automation/resolver_attempts/<attempt>.json`.
- Include merge automation summary data in the parent `reports/run_summary.json`, including enabled state, status, PR number, PR URL, child workflow id, resolver child workflow ids, and cycle count.
- Keep merge automation out of separate dependency and scheduling surfaces; it is parent-owned PR publish behavior.
- Cover the API projection, UI contract, artifact references, and run summary shape with tests that exercise the real task-detail or dashboard boundary.

Source Design Coverage
- DESIGN-REQ-006
- DESIGN-REQ-018
- DESIGN-REQ-026
- DESIGN-REQ-027
- DESIGN-REQ-029

Needs Clarification
- None
