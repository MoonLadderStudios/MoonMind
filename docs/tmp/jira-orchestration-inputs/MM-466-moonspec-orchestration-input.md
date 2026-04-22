# MM-466 MoonSpec Orchestration Input

## Source

- Jira issue: MM-466
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Render masked conic beam geometry and layers
- Labels: `moonmind-workflow-mm-ac6fd0b8-6632-4551-8572-95a1d6e25f20`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-466 from MM project
Summary: Render masked conic beam geometry and layers
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-466 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-466: Render masked conic beam geometry and layers

Short Name
masked-conic-beam-geometry-layers

Source Reference
- Source document: `docs/UI/EffectBorderBeam.md`
- Source title: Masked Conic Border Beam - Declarative Design
- Source sections: Visual model, Geometry and masking, Pseudostructure, Gradient composition, Declarative rendering rules
- Coverage IDs: DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-011

User Story
As a UI engineer, I need the beam rendered as layered conic-gradient geometry clipped to the border ring, so the active execution glint travels around the perimeter while the content area remains unaffected.

Acceptance Criteria
- The animated beam is visible only in the border ring defined by the outer rounded rectangle minus the inset inner rounded rectangle.
- The inner inset equals borderWidth, and corner radius is adjusted so the ring remains optically consistent.
- The beam uses a mostly transparent conic gradient with a soft tail, narrow bright head, and fade back to transparency.
- Optional glow derives from the beam footprint, remains lower opacity and blurred, and may straddle or expand slightly beyond the border.
- Content readability is preserved because the animated treatment never fills or overlays the interior content area.

Requirements
- Render a static resting border ring while active.
- Render one animated conic-gradient beam masked to the border ring.
- Mask out the full interior content area at all times.
- Support default bright head and trailing tail arcs of 12deg and 28deg, with acceptable ranges described by the source design.
- Support optional glow derived from the beam footprint without making the glow obscure content.
- Support optional soft or defined trailing beam behavior without changing orbital speed.
- Maintain smooth motion continuity around all sides and rounded corners.
- Preserve content readability and avoid overlaying text with the animated beam.

Relevant Implementation Notes
- Use the existing design source model: render a conic-gradient highlight over the full box, then mask it down to just the border ring.
- Model the visual stack as host surface, static border ring, animated conic beam layer, and optional blurred glow layer.
- Use a mask equivalent to outer rounded rectangle minus inner rounded rectangle, with inner inset equal to borderWidth and radius adjusted for optical consistency.
- Construct the main beam gradient with a large transparent region, soft lead-in, bright narrow head, soft trailing fade, and return to transparency.
- Align default geometry with the design guidance: bright head around 12deg, trailing tail around 28deg, and a large transparent gap for most of the orbit.
- Treat the conceptual gradient stop sequence as guidance for the visual footprint: transparent majority, soft tail rise, bright head, then fade back to transparent.
- Derive the glow from the same angular footprint as the beam, using lower opacity, slight blur, and optional 1-2px expansion beyond the border bounds.
- Keep the resting border visible while active so the perimeter remains defined even when the beam is on the far side.
- Prefer composited transform or equivalent angle animation and avoid layout-triggering animation.
- Preserve MM-466 and coverage IDs DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, and DESIGN-REQ-011 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- No full-card shimmer.
- No filling background gradient.
- No spinner icon replacement.
- No completion pulse or success burst.
- No content-area masking beyond the border ring.
- No beam or glow treatment that overlays or fills the interior content area.
- No design that makes the beam the only execution-state indicator.

Validation
- Verify the animated beam is rendered only in the border ring produced by subtracting the inset inner rounded rectangle from the outer rounded rectangle.
- Verify the inner inset equals borderWidth and corner radii remain optically consistent.
- Verify the conic gradient includes a mostly transparent region, soft tail, narrow bright head, and fade back to transparency.
- Verify optional glow follows the beam footprint with lower opacity and blur, and does not obscure content.
- Verify the static resting border ring remains visible while active.
- Verify the content area pixels, text, and child UI remain unaffected by the animated beam.
- Verify smooth motion continuity around all sides and rounded corners.

Needs Clarification
- None
