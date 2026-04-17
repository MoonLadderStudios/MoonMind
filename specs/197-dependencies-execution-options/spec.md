# Feature Specification: Dependencies and Execution Options

**Feature Branch**: `197-dependencies-execution-options`
**Created**: 2026-04-17
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-379 as the canonical Moon Spec orchestration input.

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
- None"

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Dependencies and Execution Options

**Summary**: As a task author, I want dependency selection and execution options to remain available, bounded, and policy-safe while using Jira imports or image inputs so I can create valid task runs without losing draft state or bypassing runtime gates.

**Goal**: Task authors can select bounded `MoonMind.Run` dependencies, configure runtime and publish controls from server-provided options, opt into merge automation only for ordinary PR-publishing tasks, and keep all repository, publish, and runtime validation intact across Jira and image workflows.

**Independent Test**: Can be fully tested by exercising the Create page with dependency search success and failure, duplicate and over-limit dependencies, runtime/profile option loading, publish-mode changes, resolver-style skill selection, merge automation submission, Jira import, and image upload; the story passes when the resulting draft and submitted payload preserve dependency and execution options without bypassing validation.

**Acceptance Scenarios**:

1. **Given** dependency search fails, **when** the author continues editing the Create page, **then** manual task creation remains available and the existing draft state is not discarded.
2. **Given** the author adds dependencies, **when** a duplicate dependency is selected or more than 10 direct `MoonMind.Run` dependencies are attempted, **then** duplicates are rejected and the dependency list remains capped at 10.
3. **Given** runtime configuration is loaded, **when** the author changes runtime or provider profile selections, **then** runtime defaults and provider-profile options come from server-provided configuration and remain runtime-specific.
4. **Given** publish mode is `pr` for an ordinary task, **when** the author enables merge automation and submits, **then** the payload preserves `publishMode=pr`, `task.publish.mode=pr`, and `mergeAutomation.enabled=true`.
5. **Given** publish mode is `branch` or `none`, or the selected task is a direct `pr-resolver` or `batch-pr-resolver` task, **when** the Create page renders and submits, **then** merge automation is hidden or disabled and no enabled merge automation field is submitted.
6. **Given** Jira import or image upload occurs, **when** the author submits the task, **then** repository validation, publish validation, runtime gating, dependency limits, and execution controls remain enforced.

### Edge Cases

- Dependency search may fail after dependencies were already selected; selected draft state remains intact while the failure is shown.
- A dependency result may be selected more than once through repeated search results or keyboard actions; the duplicate is rejected.
- Existing drafts or imported presets may contain more than 10 dependencies; submission must not accept more than 10 direct dependencies.
- Switching away from PR publish mode after enabling merge automation must prevent stale enabled merge automation fields from being submitted.
- Selecting a resolver-style skill after enabling merge automation must force publish mode to `none` and clear or omit merge automation.
- Jira imports and image uploads may update instructions or attachment state, but must not alter repository requirements or runtime-specific profile options.

## Assumptions

- The Create page remains the runtime implementation surface for this story.
- Dependency options represent existing `MoonMind.Run` executions available through existing execution/list APIs or fixtures.
- Resolver-style task restrictions are determined from the selected skill or preset identity already present in the Create page state.

## Source Design Requirements

- **DESIGN-REQ-004** (`docs/UI/CreatePage.md`, section 5): The Create page is a single composition form that preserves Dependencies, Execution context, Execution controls, Schedule, and Submit as canonical sections alongside Steps and Task Presets. Scope: in scope. Mapped to FR-001, FR-003, FR-004, FR-006.
- **DESIGN-REQ-013** (`docs/UI/CreatePage.md`, section 9): The dependency area remains a bounded picker for existing `MoonMind.Run` executions; users may add up to 10 direct dependencies, duplicates are rejected client-side, dependency fetch failure does not block manual creation, and dependency selection is independent from image attachments, Jira, and presets. Scope: in scope. Mapped to FR-001, FR-002, FR-007.
- **DESIGN-REQ-014** (`docs/UI/CreatePage.md`, section 10): Execution context controls preserve runtime, provider profile, model, effort, repository, branches, publish mode, and merge automation availability from server-provided runtime configuration and runtime-specific profile options. Scope: in scope. Mapped to FR-003, FR-004, FR-005.
- **DESIGN-REQ-015** (`docs/UI/CreatePage.md`, section 10): Repository validation, publish validation, resolver-style publish restrictions, and runtime gating are unaffected by Jira import or image upload, and merge automation remains available only for ordinary PR-publishing tasks. Scope: in scope. Mapped to FR-004, FR-005, FR-006, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST provide a bounded dependency picker for existing `MoonMind.Run` executions without making dependency selection dependent on Jira import, image upload, or preset behavior.
- **FR-002**: The Create page MUST reject duplicate direct dependencies and MUST prevent submission of more than 10 direct `MoonMind.Run` dependencies.
- **FR-003**: Dependency search or fetch failure MUST preserve the current draft state and MUST allow manual task creation to continue when all non-dependency validation requirements are satisfied.
- **FR-004**: The Create page MUST preserve runtime, provider profile, model, effort, repository, branch, publish mode, priority, max attempts, propose tasks, schedule, and submit controls while Jira import or image upload occurs.
- **FR-005**: Runtime defaults and provider-profile options MUST come from server-provided runtime configuration and MUST remain specific to the selected runtime.
- **FR-006**: Merge automation MUST be available only for ordinary PR-publishing tasks and MUST submit `mergeAutomation.enabled=true` while preserving `publishMode=pr` and `task.publish.mode=pr`.
- **FR-007**: Merge automation MUST be hidden or disabled, and omitted from submitted payloads, when publish mode is `branch` or `none`, or when the selected task is a direct `pr-resolver` or `batch-pr-resolver` task.
- **FR-008**: Jira import and image upload flows MUST NOT bypass or weaken repository validation, publish validation, runtime gating, resolver-style publish restrictions, dependency limits, or duplicate dependency checks.
- **FR-009**: User-facing merge automation copy MUST explain that MoonMind waits for the PR readiness gate and then uses `pr-resolver`, without implying direct auto-merge or a bypass around resolver behavior.
- **FR-010**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-379.

### Key Entities

- **Create Task Draft**: Author-edited task creation state containing instructions, steps, presets, attachments, dependencies, runtime context, publish settings, schedule, and submit controls.
- **Run Dependency**: A direct dependency on an existing `MoonMind.Run` execution selected for the new task.
- **Runtime Configuration**: Server-provided defaults and provider-profile options for each supported runtime.
- **Publish Configuration**: Task publish settings represented by top-level `publishMode` and normalized nested `task.publish.mode`.
- **Merge Automation Configuration**: Optional task payload object that enables parent-owned PR merge handling for ordinary PR-publishing runs.
- **Resolver-Style Task**: Direct task submission invoking resolver skills such as `pr-resolver` or `batch-pr-resolver`, which must force `publish.mode=none`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated tests verify dependency fetch failure preserves draft state and still permits valid manual task creation.
- **SC-002**: Automated tests verify duplicate dependencies are rejected and the direct dependency list cannot exceed 10 entries.
- **SC-003**: Automated tests verify runtime defaults and provider-profile options are derived from server-provided runtime configuration for at least two runtimes or profiles.
- **SC-004**: Automated tests verify ordinary PR-publishing task submissions include `publishMode=pr`, `task.publish.mode=pr`, and `mergeAutomation.enabled=true` when merge automation is selected.
- **SC-005**: Automated tests verify branch, none, direct `pr-resolver`, and direct `batch-pr-resolver` submissions do not submit enabled merge automation fields.
- **SC-006**: Automated tests verify Jira import and image upload do not bypass repository validation, publish validation, runtime gating, dependency limits, or resolver-style restrictions.
- **SC-007**: Verification evidence preserves MM-379 as the source Jira issue for the feature.
