# Feature Specification: Create Task Publish Controls

**Feature Branch**: `208-create-task-publish-controls`
**Created**: 2026-04-18
**Status**: Draft
**Input**:

```text
Use the Jira preset brief for MM-412 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-412 MoonSpec Orchestration Input

## Source

- Jira issue: MM-412
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Create Task Publish Controls
- Labels: none
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-412 from MM project
Summary: Create Task Publish Controls
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-412 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-412: Create Task Publish Controls

User Story
As a task author, I can author publish intent from one compact control group in the Create Task Steps card, with merge automation presented as a PR-specific publish choice, so repository, branch, publish mode, and merge automation intent are grouped where task execution intent is defined.

Summary
Update the Create Task UI so publishing controls are authored together in the Steps card instead of being split across multiple sections.

Specifically:
- Move Publish Mode so it appears next to Branch within the Steps card footer/control bar.
- Remove the separate Enable merge automation checkbox from Execution context.
- Expose merge automation through the Publish Mode UI as a PR-specific option, for example:
  - None
  - Branch
  - PR
  - PR with Merge Automation
- Keep this as a UI and authoring-layer change, not a worker contract rewrite.
- Preserve the existing backend and runtime payload shape.

Current State
The current frontend implementation is still split in a way that does not match desired-state authoring:
- The Steps section still contains separate Starting Branch and Target Branch text inputs.
- Publish Mode is rendered later in Execution context.
- Merge automation is rendered as a separate checkbox.

That creates several problems:
- Publish-related choices are not grouped where the user is thinking about repo and branch intent.
- Merge automation looks like a separate orthogonal feature when it only makes sense for PR publishing.
- The Create page feels larger and more fragmented than necessary.
- The implementation is drifting from the desired-state Create Page contract.

Desired Outcome
The Create page should author publish intent from one compact control group in the Steps card.

Target UX:
- GitHub Repo
- Branch
- Publish Mode

Publish Mode should sit immediately to the right of Branch when there is room, and wrap responsively on narrower widths while staying within the same Steps-card control group.

The merge automation checkbox should be removed and represented as a Publish Mode choice instead.

Recommended Publish Mode options:
- None
- Branch
- PR
- PR with Merge Automation

Important Contract Decision
Do not introduce a new backend publish-mode enum just to support this UI.

Keep the existing runtime contract and treat the combined option as a UI projection:
- None -> `publishMode=none`
- Branch -> `publishMode=branch`
- PR -> `publishMode=pr`
- PR with Merge Automation -> `publishMode=pr` plus `mergeAutomation.enabled=true`

This keeps workflow and worker-side merge automation behavior stable while simplifying the Create page.

Scope

In scope:
- Move Publish Mode into the Steps card next to Branch.
- Remove the separate merge automation checkbox from Execution context.
- Represent merge automation as a PR-specific Publish Mode option.
- Preserve existing submission payload semantics.
- Update edit/rerun reconstruction so legacy drafts still hydrate correctly.
- Update Create Page docs and tests to match the new UI contract.
- Ensure resolver-style skills still constrain publishing correctly.

Out of scope:
- Redesigning merge automation behavior or merge gating logic.
- Changing worker-side merge automation orchestration semantics.
- Introducing a brand-new public API contract for publish mode.
- Broader PR resolver redesign.

Proposed UI Behavior

1. Steps card control group
- The Steps footer/control bar should own publish authoring.
- It should render compact inline controls for repository, branch, and publish mode.

2. Publish Mode values
- Suggested visible values:
  - None
  - Branch
  - PR
  - PR with Merge Automation
- The exact label can be adjusted slightly for brevity, such as PR + Merge Automation, but the behavior must be unambiguous.

3. Merge automation availability rules
- PR with Merge Automation should only be available when merge automation is semantically allowed.
- At minimum:
  - Ordinary task authoring may use it.
  - Direct pr-resolver / batch-pr-resolver tasks must not surface it.
  - If current runtime or skill constraints force publish mode to none, the combined PR+merge option must not remain selected silently.

4. Accessible labeling
- The compact inline controls may omit large visible labels above the chrome, but they must still expose accessible names.

5. Responsive behavior
- On wide layouts, Branch and Publish Mode should appear side-by-side.
- On narrow layouts, they may wrap, but must remain in the same Steps-card control group.

Edit / Rerun / Migration Behavior
This story must handle existing state safely.

Existing create/edit states to normalize:
- `publishMode=none` -> None
- `publishMode=branch` -> Branch
- `publishMode=pr` and no merge automation -> PR
- `publishMode=pr` and `mergeAutomation.enabled=true` -> PR with Merge Automation

This should apply consistently for:
- Fresh create.
- Edit.
- Rerun.
- Legacy snapshots reconstructed into the Create page.

Technical Work Breakdown

1. Update Create page state model
- Refactor the Create page UI state so Publish Mode can represent the combined PR+merge choice without changing the worker payload contract.
- Keep internal submission logic capable of emitting `publishMode` and `mergeAutomation.enabled`.
- Add a UI-layer derived enum/value for the combined picker state.
- Centralize mapping between UI selection and submission payload.
- Centralize mapping between stored payload and hydrated UI selection.

2. Move Publish Mode rendering into the Steps card
- Update the Steps-card footer/control group so Publish Mode is rendered adjacent to Branch.
- Relocate the control from Execution context.
- Remove the old duplicate control placement.
- Ensure layout, spacing, and wrapping are intentional.

3. Remove separate merge automation checkbox
- Delete the separate checkbox from Execution context and replace its behavior with the combined Publish Mode option.

4. Preserve submission semantics
- Submission logic must continue to emit the existing payload shape:
  - `publishMode`
  - `task.publish.mode`
  - optional `mergeAutomation.enabled=true`
- No worker-side API churn should be required for this story.

5. Preserve gating behavior for resolver skills
- Current safeguards that force or constrain publish mode for resolver-style skills must continue to work with the new UI representation.

6. Update tests
- Add or update Create page tests for inline Publish Mode rendering in the Steps card.
- Confirm there is no standalone merge automation checkbox.
- Cover correct payload mapping for all Publish Mode choices.
- Cover correct edit/rerun hydration for existing task inputs.
- Cover behavior when resolver skills or other constraints disallow PR publish choices.

7. Update docs
- Update desired-state docs so implementation and docs stay aligned.
- At minimum, update `docs/UI/CreatePage.md`.
- If needed, also update related UI/test contract docs that still describe the old placement.

Likely File Targets

Frontend / tests:
- `frontend/src/entrypoints/task-create.tsx`
- `frontend/src/entrypoints/task-create.test.tsx`

Docs:
- `docs/UI/CreatePage.md`

Potential supporting areas if needed:
- Create-page styling assets used by `task-create.tsx`.
- Task editing draft reconstruction helpers if hydration logic needs a shared normalization path.

Acceptance Criteria
- Publish Mode is rendered in the Steps card next to Branch rather than in Execution context.
- The separate Enable merge automation checkbox is removed.
- The Publish Mode UI includes a PR-specific merge automation option.
- Selecting the merge automation option still submits `publishMode=pr` plus `mergeAutomation.enabled=true` rather than introducing a new backend publish-mode contract.
- Edit/rerun hydration maps existing PR + merge automation task inputs back to the combined Publish Mode selection correctly.
- Resolver-style skills and similar guardrails still prevent invalid publish selections.
- Responsive layout keeps Branch and Publish Mode grouped within the Steps card.
- Accessibility names remain correct for the compact controls.
- Tests cover both submission mapping and reconstruction behavior.
- `docs/UI/CreatePage.md` reflects the new control placement and semantics.

Implementation Notes
- This story is intentionally about Create-page authoring UX alignment, not merge automation orchestration changes.
- Merge automation is not a separate publish category at the backend contract layer, but it should be presented as a first-class PR publishing choice in the UI.
- Keep workflow and worker-side merge automation behavior stable.
- Preserve MM-412 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Jira link metadata at fetch time indicates no issue links for MM-412.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Consolidate Publish Controls

**Summary**: As a task author, I want repository, branch, publish mode, and merge automation intent authored together in the Steps card so that publishing choices are grouped with the execution plan they affect.

**Goal**: Task authors can choose a publish mode, including a PR-specific merge automation choice, from the Steps card without a separate merge automation checkbox or duplicate execution-context publish controls, while existing runtime submission semantics remain unchanged.

**Independent Test**: Open the Create page for create, edit, and rerun flows; select each publish option from the Steps card control group; verify the page exposes no separate merge automation checkbox, preserves resolver-style restrictions, hydrates legacy PR-with-merge drafts into the combined option, and submits the existing publish and merge automation payload semantics.

**Acceptance Scenarios**:

1. **Given** a task author is creating an ordinary task, **when** the Steps card footer renders repository publishing controls, **then** GitHub Repo, Branch, and Publish Mode appear together in one compact control group.
2. **Given** there is room in the Steps footer, **when** Branch and Publish Mode render, **then** Publish Mode appears immediately to the right of Branch; on narrow layouts it may wrap while remaining in the same control group.
3. **Given** the task is an ordinary PR-publishing task, **when** the author opens Publish Mode, **then** a PR-specific merge automation choice is available alongside None, Branch, and PR.
4. **Given** the author selects the PR-specific merge automation choice, **when** the task is submitted, **then** the runtime request preserves PR publishing and includes merge automation enabled without creating a new backend publish category.
5. **Given** publish mode is None or Branch, **when** the task is submitted, **then** merge automation is not submitted.
6. **Given** a direct pr-resolver or batch-pr-resolver task constrains publishing, **when** Publish Mode renders, **then** invalid PR or merge automation choices are not surfaced or cannot remain selected silently.
7. **Given** edit or rerun reconstructs a legacy task with PR publishing and merge automation enabled, **when** the Create page hydrates the draft, **then** Publish Mode displays the combined PR-with-merge selection.
8. **Given** edit or rerun reconstructs legacy publish states without merge automation, **when** the draft hydrates, **then** None, Branch, and PR states map deterministically to their visible Publish Mode choices.
9. **Given** the Execution context section renders, **when** the author reviews runtime options, **then** it does not contain a separate Enable merge automation checkbox or duplicate Publish Mode control.
10. **Given** compact publishing controls omit large visible labels above the dropdown chrome, **when** assistive technology inspects them, **then** Branch and Publish Mode still expose clear accessible names.

### Edge Cases

- The selected runtime or task skill can force publish mode to None.
- A stored draft can contain PR publishing with merge automation enabled from a previous UI shape.
- A stored draft can contain merge automation enabled while the current publish choice or skill constraints no longer allow it.
- The Steps footer can be too narrow for all publishing controls to fit on one row.
- Repository or branch values can be absent while the author is still drafting a text-only task.
- Jira Orchestrate preset behavior remains separate from this Create-page publish option.

## Assumptions

- Runtime mode is selected; this story changes Create-page behavior and tests rather than only documentation.
- The existing backend/runtime publish and merge automation contract is still the source of truth for task submission.
- The visible label for the combined choice may be `PR with Merge Automation` or a shorter equivalent such as `PR + Merge Automation` if behavior and accessibility remain unambiguous.
- The existing desired-state Create Page document is a runtime source requirement because the Jira brief points at it.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/UI/CreatePage.md`, section 5): Branch and publish authoring lives in the Steps card, is not duplicated in Execution context, and the Create page exposes no separate target branch field. Scope: in scope. Mapped to FR-001, FR-002, FR-008.
- **DESIGN-REQ-002** (`docs/UI/CreatePage.md`, section 7.6): The Steps footer contains GitHub Repo, Branch, and Publish Mode; Branch and Publish Mode use compact inline controls with accessible names; Publish Mode appears immediately to the right of Branch when possible. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-009.
- **DESIGN-REQ-003** (`docs/UI/CreatePage.md`, section 7.6): Create-page authoring must not require a user-authored target branch and submit logic must preserve the canonical publishing contract for none, branch, and PR publishing. Scope: in scope as a contract preservation guardrail. Mapped to FR-004, FR-006.
- **DESIGN-REQ-004** (`docs/UI/CreatePage.md`, section 10): Publish mode is authored in the Steps card, resolver-style skills may force publish mode to None, merge automation is available only for ordinary PR-publishing tasks, and merge automation must not be submitted for Branch, None, direct pr-resolver, or batch-pr-resolver tasks. Scope: in scope. Mapped to FR-004, FR-005, FR-007, FR-008.
- **DESIGN-REQ-005** (`docs/UI/CreatePage.md`, section 10): Merge automation copy must explain that MoonMind waits for PR readiness and uses pr-resolver, must not imply direct auto-merge, and Jira Orchestrate behavior remains unchanged by this option. Scope: in scope. Mapped to FR-010, FR-011.
- **DESIGN-REQ-006** (`docs/UI/CreatePage.md`, section 13): Edit and rerun reconstruct runtime, repository, branch, and publish settings from authoritative task input snapshots. Scope: in scope. Mapped to FR-012.
- **DESIGN-REQ-007** (`docs/UI/CreatePage.md`, section 14): Create-page submission maps authored branch and publish intent into canonical task publishing fields before runtime launch. Scope: in scope. Mapped to FR-004, FR-006.
- **DESIGN-REQ-008** (`docs/UI/CreatePage.md`, section 16): Repository, branch, and legacy publish migration failures must preserve unrelated draft state and fail explicitly rather than silently rewriting semantics. Scope: in scope. Mapped to FR-007, FR-012.
- **DESIGN-REQ-009** (`docs/UI/CreatePage.md`, section 17): Compact Branch and Publish Mode controls must have accessible names even without visible labels above the dropdown chrome. Scope: in scope. Mapped to FR-009.
- **DESIGN-REQ-010** (`docs/UI/CreatePage.md`, section 18): Create-page tests should cover a single Branch dropdown, no Target Branch control, branch mapping, Publish Mode adjacent to Branch, and edit/rerun normalization. Scope: in scope. Mapped to FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST render repository, Branch, and Publish Mode authoring controls together in the Steps card footer/control group.
- **FR-002**: The Create page MUST NOT render a duplicate Publish Mode control or a separate Enable merge automation checkbox in Execution context.
- **FR-003**: Publish Mode MUST appear immediately to the right of Branch when layout space allows and MUST remain in the same responsive Steps-card control group when wrapping is needed.
- **FR-004**: Publish Mode MUST include a PR-specific merge automation choice for ordinary PR-publishing tasks where merge automation is semantically allowed.
- **FR-005**: Selecting the PR-specific merge automation choice MUST preserve PR publish behavior and submit merge automation as enabled without introducing a new backend publish category.
- **FR-006**: None, Branch, and PR choices MUST continue to submit the existing publishing semantics without merge automation unless the PR-specific merge automation choice is selected.
- **FR-007**: If runtime, skill, or task constraints disallow PR publishing or merge automation, the UI MUST hide, disable, or clear invalid choices visibly rather than leaving an invalid combined selection active.
- **FR-008**: Direct pr-resolver and batch-pr-resolver task authoring MUST NOT surface merge automation as an available publish choice.
- **FR-009**: Compact Branch and Publish Mode controls MUST expose clear accessible names even when visible labels above the dropdown chrome are omitted.
- **FR-010**: Merge automation copy MUST explain that MoonMind waits for PR readiness and uses pr-resolver behavior rather than direct auto-merge.
- **FR-011**: Jira Orchestrate preset behavior MUST remain unchanged by this Create-page publish control change.
- **FR-012**: Create, edit, and rerun draft hydration MUST deterministically map stored publish and merge automation state into the visible Publish Mode selection, including PR-with-merge legacy states.
- **FR-013**: Automated coverage MUST preserve MM-412 traceability and validate Steps-card placement, absence of the standalone checkbox, all publish-choice submission mappings, edit/rerun hydration, resolver-style restrictions, responsive grouping, and accessible names.

### Key Entities

- **Publish Mode Selection**: The visible authoring choice that represents None, Branch, PR, or PR with Merge Automation.
- **Merge Automation Intent**: A PR-specific task authoring choice that enables MoonMind's existing PR readiness and resolver flow without changing publish category.
- **Steps Card Publish Control Group**: The compact group in the Steps card footer that owns repository, Branch, and Publish Mode authoring.
- **Stored Publish State**: Existing task input state reconstructed during create, edit, and rerun flows and normalized into a visible Publish Mode selection.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated Create-page tests find exactly one Publish Mode control and it is located in the Steps card control group for 100% of covered create/edit/rerun render paths.
- **SC-002**: Automated tests confirm the separate Enable merge automation checkbox is absent in 100% of covered ordinary and constrained task render paths.
- **SC-003**: Automated request-shape tests cover all four visible Publish Mode choices and confirm 100% of submissions preserve the expected publish and merge automation semantics.
- **SC-004**: Automated hydration tests cover stored None, Branch, PR, and PR-with-merge states and map each to the correct visible Publish Mode selection.
- **SC-005**: Automated constrained-task tests confirm direct pr-resolver and batch-pr-resolver tasks cannot submit merge automation.
- **SC-006**: Accessibility tests confirm Branch and Publish Mode each expose an accessible name in the compact Steps-card control group.
- **SC-007**: The desired-state Create Page documentation reflects the new control placement and semantics with no remaining statement that merge automation is a standalone Execution context checkbox.
