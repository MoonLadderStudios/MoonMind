# MM-488 MoonSpec Orchestration Input

## Source

- Jira issue: MM-488
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Attach executing shimmer as a shared status-pill modifier
- Labels: `moonmind-workflow-mm-2691d3b4-70f7-4d6a-9e26-d65a4265c17a`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-488 from MM project
Summary: Attach executing shimmer as a shared status-pill modifier
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-488 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-488: Attach executing shimmer as a shared status-pill modifier

Source Reference
- Source document: `docs/UI/EffectShimmerSweep.md`
- Source title: Shimmer Sweep Effect - Declarative Design
- Source sections:
  - Intent
  - Scope
  - Host Contract
  - State Matrix
  - Implementation Shape
  - Non-Goals
  - Hand-off Note
- Coverage IDs:
  - DESIGN-REQ-001
  - DESIGN-REQ-002
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-011
  - DESIGN-REQ-013
  - DESIGN-REQ-016

User Story
As a Mission Control user, I need executing status pills to opt into one shared shimmer modifier so active workflow progress is visible consistently without changing status text, icons, task row layout, or update behavior.

Acceptance Criteria
- Executing status-pill hosts can activate the shimmer through the preferred data-state/data-effect selector.
- Existing .is-executing hosts can activate the same shared modifier when needed.
- The modifier does not mutate host text content, casing, icon choice, task row layout, polling, or live-update behavior.
- The shared modifier is available to list, card, and detail status-pill surfaces rather than being page-local.
- Non-executing states never inherit the shimmer accidentally.

Requirements
- Provide one reusable executing-state shimmer treatment for existing status pills.
- Keep the story limited to effect activation and host integration.
- Attach without adding wrappers that change layout or pill dimensions.
- Preserve explicit non-goals for broader workflow-state UI behavior.

Relevant Implementation Notes
- Preserve MM-488 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for shimmer intent, scope, host contract, state matrix, implementation shape, non-goals, and hand-off guidance.
- Attach the shared shimmer as a reusable status-pill modifier rather than a page-local implementation.
- Prefer the data-state/data-effect host selector path for executing status pills.
- Keep existing `.is-executing` host support wired to the same shared modifier when needed.
- Do not change status text content, text casing, icon selection, task row layout, polling behavior, or live-update behavior.
- Keep list, card, and detail status-pill surfaces able to use the shared executing shimmer.
- Ensure non-executing states do not receive the shimmer by inheritance, broad selectors, or default styling.
- Avoid adding wrappers or layout-affecting elements that change pill dimensions.

Non-Goals
- Changing workflow-state semantics, labels, icons, task row layout, polling, or live-update behavior.
- Creating a page-local shimmer implementation for only one status-pill surface.
- Adding wrappers or DOM structure solely to support the effect.
- Applying shimmer to non-executing states.

Validation
- Verify executing status-pill hosts can activate the shimmer through the preferred data-state/data-effect selector.
- Verify existing `.is-executing` hosts can activate the same shared modifier when needed.
- Verify the modifier does not mutate host text content, casing, icon choice, task row layout, polling, or live-update behavior.
- Verify list, card, and detail status-pill surfaces can use the shared modifier.
- Verify non-executing states never inherit the shimmer accidentally.
- Verify the implementation does not add wrappers or change pill dimensions.
- Verify MM-488 remains visible in downstream spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-488 is blocked by MM-489, whose embedded status is Backlog.

Needs Clarification
- None
