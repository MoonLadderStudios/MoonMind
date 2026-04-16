# MM-353 MoonSpec Orchestration Input

## Source

- Jira issue: MM-353
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Map merge automation outcomes to parent completion and cancellation
- Labels: `moonmind-breakdown`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`

## Canonical MoonSpec Feature Request

Jira issue: MM-353 from MM project
Summary: Map merge automation outcomes to parent completion and cancellation
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-353 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-353: Map merge automation outcomes to parent completion and cancellation

Story ID: STORY-004

Source Document
docs/Tasks/PrMergeAutomation.md

Source Title
PR Merge Automation - Child Workflow Resolver Strategy

Source Sections
- 10.2 Output
- 16. Dependency Semantics
- 17. Terminal Outcome Rules
- 18. Cancellation Semantics
- 23. Acceptance Criteria

Coverage IDs
- DESIGN-REQ-002
- DESIGN-REQ-012
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-029

User Story
As a downstream task author, I need parent task completion to faithfully reflect merge automation success, failure, expiration, or cancellation so dependency behavior is deterministic.

Independent Test
Run parent workflow tests with stub MoonMind.MergeAutomation completions for every allowed terminal status and assert the resulting parent terminal state, dependency satisfaction behavior, and cancellation propagation to child workflows.

Acceptance Criteria
- Parent MoonMind.Run succeeds only when the child returns merged or already_merged.
- Parent MoonMind.Run fails when the child returns blocked, failed, or expired.
- Parent MoonMind.Run is canceled when the child returns canceled or when operator-initiated parent cancellation propagates.
- Downstream dependsOn relationships are satisfied only by parent terminal success.
- Canceling the parent requests cancellation of MoonMind.MergeAutomation, and canceling MoonMind.MergeAutomation requests cancellation of any active resolver child run.
- Cancellation and cleanup summaries do not claim success for best-effort cleanup that did not complete.

Requirements
- Terminal outcome mapping must be deterministic and covered at the workflow boundary.
- Cancellation must preserve truthful operator-visible state.
- Non-success merge outcomes must not satisfy dependencies under the current dependency model.

Dependencies
- STORY-001
- STORY-002
- STORY-003

Implementation Notes
- Map child MoonMind.MergeAutomation `merged` and `already_merged` terminal statuses to successful parent MoonMind.Run completion.
- Map child MoonMind.MergeAutomation `blocked`, `failed`, and `expired` terminal statuses to failed parent MoonMind.Run completion.
- Map child MoonMind.MergeAutomation `canceled` terminal status to canceled parent MoonMind.Run completion.
- Propagate operator-initiated parent cancellation to the active MoonMind.MergeAutomation child workflow.
- Propagate MoonMind.MergeAutomation cancellation to any active resolver child run.
- Ensure downstream `dependsOn` relationships are satisfied only when the parent terminal state is successful.
- Keep cancellation and cleanup summaries truthful when best-effort cleanup is incomplete.
- Add workflow-boundary coverage for each allowed terminal status and cancellation path.

Source Design Coverage
- DESIGN-REQ-002
- DESIGN-REQ-012
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-029
