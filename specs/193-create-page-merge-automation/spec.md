# Feature Specification: Create Page Merge Automation

**Feature Branch**: `193-create-page-merge-automation`
**Created**: 2026-04-16
**Status**: Draft
**Input**:

```text
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
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Configure Merge Automation During PR Publishing

**Summary**: As a MoonMind operator, I want the Create page to offer merge automation only for PR-publishing tasks so that a submitted implementation run can publish a pull request and route readiness and merge handling through MoonMind's resolver workflow.

**Goal**: Operators can opt into merge automation while creating a PR-publishing task without changing the existing publish-mode contract or exposing resolver-only tasks to automatic merge handling.

**Independent Test**: Create task drafts across publish modes and resolver skill choices. The story passes when the merge automation option is visible and submitted only for ordinary PR-publishing tasks, remains absent for `branch` and `none`, is unavailable for resolver-style tasks, and the submitted payload keeps the existing PR publish fields alongside `mergeAutomation.enabled=true` when selected.

**Acceptance Scenarios**:

1. **Given** an operator selects `Publish Mode` `pr` for a normal implementation task, **when** the Create page renders execution context controls, **then** the merge automation option is visible and available.
2. **Given** an operator selects the merge automation option for a PR-publishing task, **when** the task is submitted, **then** the payload includes `mergeAutomation.enabled=true` while preserving `publishMode=pr` and `task.publish.mode=pr`.
3. **Given** an operator selects `Publish Mode` `branch` or `none`, **when** the Create page renders execution context controls, **then** the merge automation option is hidden or disabled and no merge automation fields are submitted.
4. **Given** an operator creates a direct resolver-style task using `pr-resolver` or `batch-pr-resolver`, **when** publish settings are normalized, **then** the task continues to force `publish.mode=none` and the merge automation option is not available.
5. **Given** the merge automation option is visible, **when** the operator reads the control text, **then** the UI explains that merge automation uses `pr-resolver` after the PR readiness gate opens and does not directly auto-merge or bypass resolver behavior.
6. **Given** the Jira Orchestrate preset creates work with `publish.mode=none`, **when** this story is implemented, **then** that preset behavior remains unchanged unless a separate story explicitly changes it.

### Edge Cases

- The operator switches from `pr` to another publish mode after selecting merge automation.
- The selected skill changes from an ordinary implementation skill to `pr-resolver` or `batch-pr-resolver` after merge automation was selected.
- Existing task creation defaults or presets omit publish settings entirely.
- A submitted payload has both top-level `publishMode` and nested `task.publish.mode` fields.
- Merge automation text must distinguish resolver-managed merge handling from direct auto-merge.

## Assumptions

- The Create page already has a publish mode control that can be used as the visibility source for merge automation.
- Resolver-style skills are identifiable from the selected skill id or preset metadata in the existing Create page state.
- Backend changes are only needed if current task submission or normalization strips the merge automation payload before `MoonMind.Run` receives it.
- Jira Orchestrate preset behavior remains out of scope except for verification that this story does not silently change it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST expose a merge automation option only when an ordinary task is configured with `Publish Mode` `pr`.
- **FR-002**: The Create page MUST hide or disable the merge automation option when `Publish Mode` is `branch` or `none`.
- **FR-003**: The Create page MUST make the merge automation option unavailable for direct resolver-style tasks, including `pr-resolver` and `batch-pr-resolver`.
- **FR-004**: Selecting merge automation MUST submit `mergeAutomation.enabled=true` with the task creation payload.
- **FR-005**: Submitting merge automation MUST preserve the existing PR publish contract, including top-level `publishMode=pr` and normalized `task.publish.mode=pr`.
- **FR-006**: Merge automation MUST remain configuration for PR-publishing tasks and MUST NOT become a separate top-level task type.
- **FR-007**: Switching publish mode or selected skill to a state where merge automation is unavailable MUST prevent stale enabled merge automation fields from being submitted.
- **FR-008**: User-facing Create page text MUST explain that merge automation routes through `pr-resolver` after the PR readiness gate opens and MUST NOT imply direct auto-merge or bypassing resolver behavior.
- **FR-009**: Existing Jira Orchestrate preset behavior MUST remain explicit and unchanged by this story unless a separate story changes it.
- **FR-010**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-365.

### Key Entities

- **Create Task Draft**: Operator-edited task creation state containing objective, selected skill or preset, publish mode, and optional merge automation configuration.
- **Publish Configuration**: Task publish settings represented by top-level `publishMode` and normalized nested `task.publish.mode`.
- **Merge Automation Configuration**: Optional task payload object that enables parent-owned PR merge automation for PR-publishing runs.
- **Resolver-Style Task**: Direct task submission that invokes resolver skills such as `pr-resolver` or `batch-pr-resolver` and must force `publish.mode=none`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Frontend tests verify the merge automation option is visible and selectable for ordinary PR-publishing task drafts.
- **SC-002**: Frontend tests verify the merge automation option is hidden or disabled for `branch` and `none` publish modes.
- **SC-003**: Frontend tests verify submitted payloads include `mergeAutomation.enabled=true` only when the option is selected and available.
- **SC-004**: Frontend or backend request-shape tests verify submitted payloads preserve `publishMode=pr` and `task.publish.mode=pr` with merge automation enabled.
- **SC-005**: Tests verify resolver-style tasks continue to force `publish.mode=none` and do not submit merge automation fields.
- **SC-006**: Documentation or UI contract checks verify `docs/UI/CreatePage.md` describes the option visibility, payload effect, and `pr-resolver` relationship.
- **SC-007**: Verification evidence preserves MM-365 as the source Jira issue for the feature.
