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
