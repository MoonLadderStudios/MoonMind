# MM-465 MoonSpec Orchestration Input

## Source

- Jira issue: MM-465
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Define MaskedConicBorderBeam border-only contract
- Labels: `moonmind-workflow-mm-ac6fd0b8-6632-4551-8572-95a1d6e25f20`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-465 from MM project
Summary: Define MaskedConicBorderBeam border-only contract
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-465 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-465: Define MaskedConicBorderBeam border-only contract

Short Name
masked-conic-border-beam-contract

Source Reference
- Source document: `docs/UI/EffectBorderBeam.md`
- Source title: Masked Conic Border Beam - Declarative Design
- Source sections: Goal, Design intent, Component contract, Declarative rendering rules, Non-goals
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-016

User Story
As a UI engineer, I need a standalone MaskedConicBorderBeam contract that exposes the required execution-state controls while rendering no content-area animation, so product surfaces can apply a consistent executing treatment without coupling it to a specific card implementation.

Acceptance Criteria
- A MaskedConicBorderBeam surface exists with active, borderRadius, borderWidth, speed, intensity, theme, direction, trail, glow, and reducedMotion inputs.
- When active is false, no moving beam is rendered while a host/static border may remain available.
- When active is true, the resting border and masked beam treatment are rendered as a wrapper for rectangular UI content.
- The component documentation or tests explicitly reject full-card shimmer, background fills, spinner replacement, completion pulse, success burst, and content-area masking.

Requirements
- Expose all declared component inputs with deterministic defaults.
- Keep the effect standalone and suitable for wrapping arbitrary rectangular UI surfaces.
- Represent executing state decoratively without becoming the only execution indicator.
- Render the animated treatment only in the border ring by masking out the full interior content area.
- Preserve content readability and avoid overlaying text with the animated beam.
- Support dark and light surfaces, directional motion, reduced-motion behavior, optional glow, and optional trailing beam behavior.

Relevant Implementation Notes
- Use the existing design source model: render a conic-gradient highlight over the full box, then mask it down to just the border ring.
- Model the component as a border-only wrapper around arbitrary rectangular UI content, not as a card-specific implementation.
- Defaults should align with the design guidance: medium speed around 3.6 seconds, clockwise direction, normal intensity, soft trail, low glow, and automatic reduced-motion handling.
- Keep the resting border visible while active so the perimeter remains defined even when the beam is on the far side.
- Use a mask equivalent to outer rounded rectangle minus inner rounded rectangle, with inner inset equal to borderWidth and radius adjusted for optical consistency.
- Treat the bright beam head as a narrow premium glint, with a default head arc around 12 degrees and optional softer tail around 28 degrees.
- Prefer composited transform or equivalent angle animation and avoid layout-triggering animation.
- Reduced-motion behavior should stop orbital rotation and provide a static but meaningful active state, not rapid pulsing.
- Preserve MM-465 and coverage IDs DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, and DESIGN-REQ-016 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- No full-card shimmer.
- No filling background gradient.
- No spinner icon replacement.
- No completion pulse or success burst.
- No content-area masking beyond the border ring.
- No design that makes the beam the only execution-state indicator.
- No coupling to a specific card, dashboard surface, or Mission Control page.

Validation
- Verify the MaskedConicBorderBeam API exposes active, borderRadius, borderWidth, speed, intensity, theme, direction, trail, glow, and reducedMotion controls with deterministic defaults.
- Verify inactive state renders no moving beam while allowing a static host border where appropriate.
- Verify active state renders the resting border ring and animated conic beam only in the border ring around arbitrary rectangular content.
- Verify content area pixels, text, and child UI remain unaffected by the animated beam.
- Verify tests or documentation explicitly reject full-card shimmer, background fills, spinner replacement, completion pulse, success burst, and content-area masking.
- Verify reduced-motion behavior provides a static meaningful active state without rapid pulsing.

Needs Clarification
- None
