# MM-350 MoonSpec Orchestration Input

## Source

- Jira issue: MM-350
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Start and await child merge automation after PR publish
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`

## Canonical MoonSpec Feature Request

Jira issue: MM-350 from MM project
Summary: Start and await child merge automation after PR publish
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-350 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-350: Start and await child merge automation after PR publish

User Story
As an operator running a PR-publishing task, I need the original MoonMind.Run to own and await merge automation as a child workflow so downstream tasks can depend on the original workflowId and receive the correct completion signal.

Source Document
docs/Tasks/PrMergeAutomation.md

Source Sections
- 1. Purpose
- 2. Design Decision
- 3. Goals
- 5. Summary of the Strategy
- 6. Why This Uses Child Workflows
- 9. Parent Workflow Behavior
- 16. Dependency Semantics
- 22. Rejected Alternatives

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-028
- DESIGN-REQ-029

Independent Test
Run a workflow-boundary test for a PR-publishing MoonMind.Run with mergeAutomation.enabled=true and a stub publish result. Assert that PublishContext is persisted, MoonMind.MergeAutomation is started as a child, the parent remains awaiting_external while the child is running, and a downstream dependency target remains the parent workflowId.

Acceptance Criteria
- Given publishMode is pr and mergeAutomation.enabled is true, when publish succeeds, the parent starts exactly one MoonMind.MergeAutomation child for that publish context.
- The worker-bound MoonMind.Run input keeps publishMode as a top-level field and does not require a nested publish-only replacement contract.
- The publish step emits repository, prNumber, prUrl, baseRef, headRef, headSha, publishedAt, optional jiraIssueKey, and a compact artifact reference.
- The parent records the merge automation child workflow id in compact metadata and uses mm_state awaiting_external while waiting.
- The parent cannot reach terminal success while MoonMind.MergeAutomation is still running.
- No new top-level task dependency, fixed-delay follow-up task, or specs/ artifact is introduced by this story.

Requirements
- Parent-owned subordinate work is represented by a child workflow, not a later-created top-level workflow.
- PublishContext must be durable before the child workflow can depend on it.
- Dependency satisfaction remains tied to the original parent task terminal success.
- Any dedicated merge automation stage marker must be updated through the standard MoonMind.Run search-attribute path.

Dependencies
- None

Implementation Notes
- Preserve the existing top-level MoonMind.Run publishMode contract while adding mergeAutomation configuration support.
- Extend publish result/state handling so repository, prNumber, prUrl, baseRef, headRef, headSha, publishedAt, optional jiraIssueKey, and a compact PublishContext artifact reference are available before child workflow start.
- Start MoonMind.MergeAutomation from the parent workflow after successful PR publish when merge automation is enabled.
- Keep the parent in mm_state awaiting_external while the merge automation child is active, and prevent parent terminal success until the child returns a success outcome.
- Record the merge automation child workflow id in compact parent metadata rather than embedding large child state in workflow history.
- Ensure downstream dependency checks continue to target the original parent workflowId and are not redirected to a later top-level task.
- Do not introduce a fixed-delay follow-up task or separate top-level resolver task for this story.
- Cover the worker-bound invocation shape with workflow-boundary tests, including the parent input shape, publish output shape, child workflow start, waiting state, and dependency-target behavior.

Source Design Coverage
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-003
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-028
- DESIGN-REQ-029
