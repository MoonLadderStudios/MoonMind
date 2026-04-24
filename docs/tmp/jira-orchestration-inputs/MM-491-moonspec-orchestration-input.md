# MM-491 MoonSpec Orchestration Input

## Source

- Jira issue: MM-491
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Guard shimmer quality across states, themes, and layouts
- Labels: `moonmind-workflow-mm-2691d3b4-70f7-4d6a-9e26-d65a4265c17a`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-491 from MM project
Summary: Guard shimmer quality across states, themes, and layouts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-491 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-491: Guard shimmer quality across states, themes, and layouts

Source Reference
- Source Document: `docs/UI/EffectShimmerSweep.md`
- Source Title: Shimmer Sweep Effect - Declarative Design
- Source Sections:
  - Host Contract
  - Isolation Rules
  - Reduced Motion Behavior
  - State Matrix
  - Acceptance Criteria
  - Non-Goals
- Coverage IDs:
  - DESIGN-REQ-004
  - DESIGN-REQ-009
  - DESIGN-REQ-011
  - DESIGN-REQ-014
  - DESIGN-REQ-016

User Story
As a MoonMind maintainer, I need regression coverage for the shimmer effect so future UI changes cannot make it unreadable, layout-shifting, out-of-bounds, or accidentally active on non-executing states.

Acceptance Criteria
- Automated checks cover executing and every listed non-executing state in the state matrix.
- The executing label remains readable at all sampled points during the sweep.
- The pill dimensions and surrounding layout do not shift when the effect activates or animates.
- The shimmer is clipped to rounded pill bounds and does not interact with scrollbars.
- Light and dark theme snapshots or style assertions show an intentional active treatment.
- Reduced-motion checks prove the static active fallback is present without animation.

Requirements
- Verify the full acceptance criteria set from the declarative design.
- Protect state isolation, layout stability, text legibility, theme behavior, bounds clipping, and reduced-motion behavior.
- Keep explicit non-goals from being introduced as substitutes for the shimmer sweep.

Relevant Implementation Notes
- Preserve MM-491 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for the host contract, isolation rules, reduced-motion behavior, state matrix, acceptance criteria, and non-goals.
- Build regression coverage around executing and every listed non-executing state in the state matrix.
- Verify executing-label readability at sampled points during the shimmer sweep.
- Protect pill dimensions and surrounding layout from shifting when the effect activates or animates.
- Ensure the shimmer remains clipped to rounded pill bounds and does not interact with scrollbars.
- Cover intentional active treatment expectations in both light and dark themes.
- Cover reduced-motion behavior with a static active fallback and no animation.
- Keep explicit non-goals from the declarative design out of the implementation.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-491 blocks MM-490, whose embedded status is In Progress.

Needs Clarification
- None
