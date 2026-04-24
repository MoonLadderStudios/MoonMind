# MM-490 MoonSpec Orchestration Input

## Source

- Jira issue: MM-490
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Animate shimmer motion with reduced-motion fallback
- Labels: `moonmind-workflow-mm-2691d3b4-70f7-4d6a-9e26-d65a4265c17a`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-490 from MM project
Summary: Animate shimmer motion with reduced-motion fallback
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-490 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-490: Animate shimmer motion with reduced-motion fallback

Source Reference
- Source Document: `docs/UI/EffectShimmerSweep.md`
- Source Title: Shimmer Sweep Effect - Declarative Design
- Source Sections:
  - Host Contract
  - Motion Profile
  - Reduced Motion Behavior
  - Acceptance Criteria
  - Non-Goals
- Coverage IDs:
  - DESIGN-REQ-007
  - DESIGN-REQ-010
  - DESIGN-REQ-012
  - DESIGN-REQ-014

User Story
As a user watching an executing workflow, I need the shimmer to move with a calm sweep cadence when motion is allowed and become a static active highlight when reduced motion is requested.

Acceptance Criteria
- The sweep travels left-to-right from the configured off-pill start to off-pill end without escaping visible rounded bounds.
- The animation cadence totals roughly 1.6 to 1.8 seconds per sweep including delay.
- The brightest moment occurs near the center of the pill rather than at either edge.
- The sweep uses soft entry and smooth fade exit with no overlap between cycles.
- When reduced motion is requested, the animated sweep is disabled and a static active highlight remains.
- The reduced-motion treatment still communicates executing as active without requiring animation for comprehension.

Requirements
- Implement the declared motion profile and pacing values.
- Respect motion-preference triggers for normal and reduced-motion behavior.
- Keep the treatment calm and activity-oriented rather than urgent or unstable.
- Preserve the same executing-only activation boundary from the host story.

Relevant Implementation Notes
- Preserve MM-490 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/EffectShimmerSweep.md` as the source design reference for the host contract, motion profile, reduced-motion behavior, acceptance criteria, and non-goals.
- Keep the shimmer cadence calm and activity-oriented rather than urgent or unstable.
- Animate the sweep left-to-right from the configured off-pill start to off-pill end while keeping it inside visible rounded bounds.
- Target a total cadence of roughly 1.6 to 1.8 seconds per sweep, including delay, with the brightest moment near the center of the pill.
- Use soft entry and smooth fade exit with no overlap between cycles.
- When reduced motion is requested, disable the animated sweep and retain a static active highlight that still communicates executing as active.
- Preserve the existing executing-only activation boundary from the host story.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-490 is blocked by MM-491, whose embedded status is Selected for Development.
- Trusted Jira link metadata at fetch time shows MM-490 blocks MM-489, whose embedded status is Code Review.

Needs Clarification
- None
