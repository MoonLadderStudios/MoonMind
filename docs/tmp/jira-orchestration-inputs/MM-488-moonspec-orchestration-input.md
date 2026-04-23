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
- Source Document: docs/UI/EffectShimmerSweep.md
- Source Title: Shimmer Sweep Effect - Declarative Design
- Source Sections:
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
- Existing `.is-executing` hosts can activate the same shared modifier when needed.
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
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for the shimmer effect intent, host contract, state matrix, implementation shape, and non-goals.
- Keep the work focused on activating one shared executing-state shimmer modifier for existing status-pill surfaces.
- Support both preferred data-state/data-effect selectors and existing `.is-executing` hosts where needed.
- Do not add wrappers or otherwise change status-pill layout, dimensions, text, casing, icon selection, polling behavior, or live-update behavior.
- Ensure non-executing states do not inherit the shimmer accidentally.
- Keep the shared modifier reusable across list, card, and detail status-pill surfaces rather than page-local.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-488 is blocked by MM-489, whose embedded status is Selected for Development.

Needs Clarification
- None
