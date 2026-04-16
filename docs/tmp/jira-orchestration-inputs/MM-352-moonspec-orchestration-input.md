# MM-352 MoonSpec Orchestration Input

## Source

- Jira issue: MM-352
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Run pr-resolver children and re-gate after resolver pushes
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`

## Canonical MoonSpec Feature Request

Jira issue: MM-352 from MM project
Summary: Run pr-resolver children and re-gate after resolver pushes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-352 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-352: Run pr-resolver children and re-gate after resolver pushes

Story ID: STORY-003

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 6.3 Why the resolver itself is a child MoonMind.Run
- 13. Resolver Child Workflow Strategy
- 14. Post-Resolver Re-Gating
- 15. Shared Gate Semantics Between Gate and Resolver
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-005
- DESIGN-REQ-014
- DESIGN-REQ-016
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-029

User Story
As a maintainer relying on merge automation, I need each resolver attempt to run through the existing pr-resolver skill substrate and return a disposition so MoonMind can merge, retry through the gate, or stop for manual review.

Independent Test
Use a workflow-boundary test where the gate opens, a stub child MoonMind.Run returns each allowed mergeAutomationDisposition, and the parent MergeAutomation workflow either completes, re-enters awaiting_external with an incremented cycle, or fails with the expected blocker outcome.

Acceptance Criteria
- When the gate opens, MoonMind.MergeAutomation starts a child MoonMind.Run rather than directly invoking the pr-resolver skill.
- Resolver child initialParameters.publishMode is exactly none.
- Resolver child task.tool is exactly {type: skill, name: pr-resolver, version: 1.0}.
- A resolver result with merged or already_merged completes merge automation successfully.
- A resolver result with reenter_gate returns to gate evaluation and does not treat the prior review/check signal as final authority after a new push.
- A resolver result with manual_review or failed produces a non-success merge automation outcome with blockers or failure summary.
- Gate and resolver contract tests use the same logical blocker categories and head-SHA freshness rules.

Requirements
- Resolver execution must reuse MoonMind.Run substrate for workspace, runtime setup, artifacts, logs, and skill routing.
- Resolver-generated pushes must not allow immediate merge unless external readiness is fresh for the new head SHA.
- Resolver disposition must be explicit so the workflow does not infer high-level outcomes from free-form logs.

Dependencies
- STORY-001
- STORY-002

Implementation Notes
- Implement resolver execution as a child MoonMind.Run, not a direct pr-resolver skill invocation inside MoonMind.MergeAutomation.
- Configure the resolver child with `initialParameters.publishMode` set exactly to `none`.
- Configure the resolver child task tool as `{type: skill, name: pr-resolver, version: 1.0}`.
- Define and consume explicit `mergeAutomationDisposition` values for at least `merged`, `already_merged`, `reenter_gate`, `manual_review`, and `failed`.
- Treat `merged` and `already_merged` as successful merge automation completion dispositions.
- Treat `reenter_gate` as a signal to repeat gate evaluation, increment the gate cycle, and require fresh readiness for the new head SHA rather than trusting prior external review or check signals.
- Treat `manual_review` and `failed` as non-success merge automation outcomes with clear blockers or failure summaries.
- Keep gate and resolver boundary tests aligned on shared logical blocker categories and head-SHA freshness rules.

Source Design Coverage
- DESIGN-REQ-005
- DESIGN-REQ-014
- DESIGN-REQ-016
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-029
