# MM-489 MoonSpec Orchestration Input

## Source

- Jira issue: MM-489
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Render the themed shimmer band and halo layers
- Labels: `moonmind-workflow-mm-2691d3b4-70f7-4d6a-9e26-d65a4265c17a`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-489 from MM project
Summary: Render the themed shimmer band and halo layers
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-489 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-489: Render the themed shimmer band and halo layers

Source Reference
- Source document: `docs/UI/EffectShimmerSweep.md`
- Source title: Shimmer Sweep Effect - Declarative Design
- Source sections:
  - Design Principles
  - Visual Model
  - Theme Binding
  - Isolation Rules
  - Semantic Feel
  - Implementation Shape
  - Suggested Token Block
- Coverage IDs:
  - DESIGN-REQ-005
  - DESIGN-REQ-006
  - DESIGN-REQ-008
  - DESIGN-REQ-009
  - DESIGN-REQ-012
  - DESIGN-REQ-015

User Story
As a Mission Control user, I need the executing pill shimmer to look like a premium active progress treatment in both light and dark themes, with a luminous diagonal band and subtle halo that keep the status text readable.

Acceptance Criteria
- The executing pill keeps its normal base appearance underneath the overlay.
- The shimmer core appears as a soft diagonal bright band and the halo appears wider and dimmer behind it.
- The effect derives color roles from existing MoonMind tokens, with no disconnected one-off palette.
- Text renders above the overlay and remains readable in light and dark themes.
- Overlay hit testing and pointer events are disabled.
- Reusable effect token names cover the suggested tunable values or equivalent implementation variables.

Requirements
- Implement the three-layer visual model for base, sweep band, and trailing halo.
- Bind effect roles to existing theme tokens.
- Honor inside-fill and inside-border placement.
- Apply stacking and hit-testing isolation so text and interactions remain primary.

Relevant Implementation Notes
- Preserve MM-489 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for shimmer design principles, visual model, theme binding, isolation rules, semantic feel, implementation shape, and suggested effect tokens.
- Keep the pill's normal base appearance visible beneath the overlay rather than replacing the base state styling.
- Implement the shimmer as a soft diagonal bright band with a wider, dimmer trailing halo so the effect reads as premium active progress instead of an error or loading skeleton.
- Derive shimmer roles from existing MoonMind theme tokens instead of introducing disconnected one-off colors.
- Ensure status text renders above the overlay and remains readable in both light and dark themes.
- Disable overlay hit testing and pointer events so the effect cannot interfere with interaction.
- Expose reusable effect token names or equivalent implementation variables for the shimmer core, halo, and related tunable values.
- Respect the source design's inside-fill and inside-border placement expectations when applying the effect to the pill.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-489 is blocked by MM-488, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-489 blocks MM-490, whose embedded status is Selected for Development.

Needs Clarification
- None
