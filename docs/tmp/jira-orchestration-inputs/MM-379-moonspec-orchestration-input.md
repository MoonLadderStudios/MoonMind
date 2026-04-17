# MM-379 MoonSpec Orchestration Input

## Source

- Jira issue: MM-379
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Dependencies and Execution Options
- Labels: `moonmind-workflow-mm-5818081f-60f0-45dd-ad16-3f7753de93ae`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-379 from MM project
Summary: Dependencies and Execution Options
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-379 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-379: Dependencies and Execution Options

Short Name
dependencies-execution-options

Source Reference
- Source document: `docs/UI/CreatePage.md`
- Source title: Create Page
- Source sections: 9. Dependency contract, 10. Execution context contract, 5. Canonical page model
- Coverage IDs: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-004

User Story
As a task author, I can select run dependencies and configure runtime, repository, publish, merge automation, priority, attempts, proposals, and schedule options without those controls being weakened by Jira or images.

Acceptance Criteria
- Given dependency search fails, then I can continue manual task creation without losing draft state.
- Given I add dependencies, then no more than 10 direct MoonMind.Run dependencies are accepted and duplicates are rejected client-side.
- Given runtime configuration is loaded, then runtime defaults and provider-profile options come from server-provided config and remain runtime-specific.
- Given publish mode is pr for an ordinary task, then merge automation can be selected and submission preserves publishMode=pr, task.publish.mode=pr, and mergeAutomation.enabled=true.
- Given publish mode is branch or none, or the task is a direct pr-resolver or batch-pr-resolver task, then merge automation is hidden or disabled and is not submitted.
- Given Jira import or image upload occurs, then repository validation, publish validation, and runtime gating are unchanged and still enforced.

Requirements
- Provide a bounded dependency picker for existing MoonMind.Run executions.
- Preserve runtime, provider profile, model, effort, repo, branch, publish mode, priority, max attempts, propose tasks, schedule, and submit controls.
- Use server-provided runtime defaults and runtime-specific profile options.
- Respect resolver-style skill restrictions that force publish mode to none.
- Gate merge automation to ordinary PR-publishing tasks and copy that explains PR readiness gate plus pr-resolver behavior.
- Reject any Jira or image path that bypasses repository, publish, or runtime validation.

Relevant Implementation Notes
- The Create page is a single composition form ordered as Header, Steps, Task Presets, Dependencies, Execution context, Execution controls, Schedule, and Submit.
- The dependency area remains a bounded picker for existing `MoonMind.Run` executions.
- Users may add up to 10 direct dependencies.
- Duplicate dependencies are rejected client-side.
- Dependency fetch failure must not block manual task creation or discard draft state.
- Dependency selection is independent from image attachments, Jira imports, and presets.
- Execution context controls include Runtime, Provider profile when profiles exist for the selected runtime, Model, Effort, GitHub Repo, Starting Branch, Target Branch, Publish Mode, and Enable merge automation when publish mode is `pr` for an ordinary task.
- Runtime defaults and attachment policy come from server-provided runtime configuration.
- Provider-profile options are runtime-specific.
- Repository validation rules are unaffected by attachments or Jira.
- Resolver-style skills may still force publish mode to `none`.
- Merge automation is available only for ordinary PR-publishing tasks.
- When merge automation is selected, the submitted task creation payload must preserve `publishMode=pr`, preserve `task.publish.mode=pr`, and include `mergeAutomation.enabled=true`.
- When publish mode is `branch` or `none`, or when the selected task is a direct `pr-resolver` or `batch-pr-resolver` task, merge automation must be hidden or disabled and must not be submitted.
- Merge automation copy must explain that MoonMind waits for the PR readiness gate and then uses `pr-resolver`; it must not imply direct auto-merge or a bypass around resolver behavior.
- Jira Orchestrate preset behavior remains explicit and unchanged by this Create page option.
- Jira import and image upload must never bypass or weaken repository validation, publish validation, or runtime gating.

Out of Scope
- Changing Jira Orchestrate to parent-owned PR publishing.
- Allowing Jira import or image upload to bypass repository validation, publish validation, or runtime gating.
- Treating dependency selection as an attachment, Jira import, or preset behavior.
- Enabling merge automation for branch, none, pr-resolver, or batch-pr-resolver submissions.
- Direct auto-merge or bypassing `pr-resolver` behavior.

Verification
- Verify dependency fetch failure does not block manual task creation or lose draft state.
- Verify no more than 10 direct `MoonMind.Run` dependencies are accepted.
- Verify duplicate dependencies are rejected client-side.
- Verify runtime defaults and provider-profile options come from server-provided runtime configuration and remain runtime-specific.
- Verify ordinary PR-publishing tasks can submit merge automation with `publishMode=pr`, `task.publish.mode=pr`, and `mergeAutomation.enabled=true`.
- Verify branch, none, direct `pr-resolver`, and direct `batch-pr-resolver` submissions hide or disable merge automation and do not submit it.
- Verify Jira import and image upload paths do not weaken repository validation, publish validation, or runtime gating.
- Run focused Create page frontend tests and `./tools/test_unit.sh` before completion when implementation changes are made.

Needs Clarification
- None
