# MM-365 MoonSpec Orchestration Input

## Source

- Jira issue: MM-365
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [CreatePage] Add merge automation option and wire it to PR publish
- Labels: `create-page`, `mission-control`, `pr-merge-automation`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-365 from MM project
Summary: [CreatePage] Add merge automation option and wire it to PR publish
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-365 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-365: [CreatePage] Add merge automation option and wire it to PR publish

Short Name
create-page-merge-automation

User Story
As a MoonMind operator, I want the Create page to expose a merge automation option for PR-publishing tasks so I can create implementation runs that publish a PR and then automatically route merge handling through MoonMind.MergeAutomation and pr-resolver.

Acceptance Criteria
- Update `docs/UI/CreatePage.md` to document the merge automation option in the Execution context section, including when it is visible, how it affects the submitted payload, and how it relates to PR publish mode.
- Add a Create page control for merge automation that is only available when `Publish Mode` is `pr` and is hidden or disabled for `branch` and `none`.
- The submitted task payload must include `mergeAutomation.enabled=true` when the option is selected, using the shape consumed by `MoonMind.Run` today.
- The submitted payload must preserve the existing top-level/normalized `publishMode=pr` contract and `task.publish.mode=pr`; do not make merge automation a separate top-level task type.
- The UI must make clear that merge automation uses `pr-resolver` after the PR readiness gate opens; it must not imply direct auto-merge or a bypass around pr-resolver.
- Resolver-style skills such as `pr-resolver` and `batch-pr-resolver` must continue to force `publish.mode=none`; the merge automation option must not be available for those direct resolver tasks.
- Add or update frontend tests covering the visible/hidden states, submitted payload with merge automation enabled, and absence of merge automation fields when disabled.
- Add or update backend/request-shape tests if needed so the create endpoint preserves the merge automation payload through to `MoonMind.Run` parameters.
- Ensure the Jira Orchestrate preset remains explicit: either it still owns PR creation with `publish.mode=none`, or it is deliberately updated in a separate story to use parent-owned PR publishing. Do not silently change Jira Orchestrate behavior in this story.

Requirements
- Document the Create page merge automation option in `docs/UI/CreatePage.md`, including visibility, payload effects, and PR publish relationship.
- Expose a merge automation control in the Create page only when `Publish Mode` is `pr`.
- Hide or disable the merge automation control when `Publish Mode` is `branch` or `none`.
- Submit `mergeAutomation.enabled=true` when the option is selected.
- Preserve the existing top-level and normalized PR publish contracts: `publishMode=pr` and `task.publish.mode=pr`.
- Keep merge automation as configuration for PR-publishing task runs, not as a separate top-level task type.
- Explain in the UI that merge automation routes through `pr-resolver` after the PR readiness gate opens.
- Prevent resolver-style skills, including `pr-resolver` and `batch-pr-resolver`, from exposing the merge automation option and preserve their forced `publish.mode=none` behavior.
- Preserve the existing Jira Orchestrate preset behavior unless a separate story deliberately changes it.

Independent Test
Create page tests cover merge automation visible and selectable when `Publish Mode` is `pr`, hidden or disabled when `Publish Mode` is `branch` or `none`, payload submission with `mergeAutomation.enabled=true` when enabled, absence of merge automation fields when disabled, and resolver-style task creation continuing to force `publish.mode=none`.

Backend Test
Add or update backend/request-shape tests if the create endpoint or run payload normalization changes, verifying that the merge automation payload is preserved through to `MoonMind.Run` parameters while `publishMode=pr` and `task.publish.mode=pr` remain intact.

Relevant Implementation Notes
- Current Create page implementation surface: `frontend/src/entrypoints/task-create.tsx`.
- Current Create page desired-state doc: `docs/UI/CreatePage.md`.
- `MoonMind.Run` currently detects merge automation from `mergeAutomation`, `task.mergeAutomation`, or `task.publish.mergeAutomation` when `enabled` is true.
- The active merge automation workflow launches a child `MoonMind.Run` for `pr-resolver` with `publishMode=none`; this invariant must remain visible in docs and tests.

Out of Scope
- Implementing Jira Orchestrate automatic merge automation behavior.
- Replacing or changing the `pr-resolver` skill.
- Changing merge readiness policy semantics beyond UI configuration fields required for v1.
- Cleaning up the dead legacy merge automation code path, which is tracked separately.

Verification
- Run focused frontend tests for `frontend/src/entrypoints/task-create.test.tsx`.
- Run focused backend/unit tests for task creation or run payload normalization if changed.
- Run `./tools/test_unit.sh` before completion.

Needs Clarification
- None
