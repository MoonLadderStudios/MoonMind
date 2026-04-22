# MM-467 MoonSpec Orchestration Input

## Source

- Jira issue: MM-467
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Tune motion, theme, and intensity presets
- Labels: `moonmind-workflow-mm-ac6fd0b8-6632-4551-8572-95a1d6e25f20`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-467 from MM project
Summary: Tune motion, theme, and intensity presets
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-467 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-467: Tune motion, theme, and intensity presets

Short Name
motion-theme-intensity-presets

Source Reference
- Source document: `docs/UI/EffectBorderBeam.md`
- Source title: Masked Conic Border Beam - Declarative Design
- Source sections: Motion behavior, Color behavior, Recommended default tuning, Motion variants, Declarative rendering rules
- Coverage IDs: DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-012, DESIGN-REQ-011

User Story
As a product designer or UI engineer, I need the beam to provide predictable speed, direction, color, intensity, trail, glow, and variant tuning, so the same effect can communicate execution across dark and light MoonMind surfaces without overpowering other UI states.

Acceptance Criteria
- Default active motion is a 3.6s linear infinite clockwise orbit.
- Slow, medium, and fast presets map to 4.8s, 3.6s, and 2.8s per orbit, while explicit seconds are honored.
- Orbital animation avoids easing and reads as a continuous circulation path.
- Activation fades beam opacity to target over 160-240ms and deactivation fades beam and glow out over 120-180ms.
- Theme and intensity controls set resting border, head, tail, glow, and opacity token values that work on dark and light surfaces.
- The preferred default is Variant A precision glint with a soft trail and low glow.

Requirements
- Support configurable speed values: slow, medium, fast, and explicit seconds.
- Use the default 3.6s linear infinite clockwise orbit for active motion.
- Keep orbital motion linear and continuous with no easing on the orbit itself.
- Support direction control for clockwise and counterclockwise orbit behavior.
- Support activation and deactivation fades within the issue-defined timing ranges.
- Support optional glow ramping after activation without disrupting primary beam motion.
- Support optional secondary opacity breathing at plus/minus 8% over 1.8-2.4s without breaking continuous motion.
- Support theme token mappings for resting border, beam head, beam tail, glow, beam opacity, and glow opacity.
- Support neutral, brand, success, and custom theme outcomes compatible with dark and light surfaces.
- Support subtle, normal, and vivid intensity outcomes while keeping the effect below primary buttons, selected states, and error states.
- Support precision glint, energized beam, and dual-phase orbit variants as configurable outcomes or documented mappings.
- Use Variant A precision glint with a soft trail and low glow as the preferred default.
- Preserve border-only rendering behavior from DESIGN-REQ-011 so tuning controls do not allow the beam or glow to fill or obscure the content area.

Relevant Implementation Notes
- Align default tuning with the source design: active true, borderRadius 16px, borderWidth 1.5px, speed 3.6s, direction clockwise, intensity normal, trail soft, glow low, and reducedMotion auto.
- Map speed presets exactly: slow to 4.8s, medium to 3.6s, and fast to 2.8s per orbit.
- Honor explicit seconds without coercing them to the named preset values.
- Keep orbit animation linear; only entry and exit opacity transitions may use easing.
- Use enter and exit transition defaults of 200ms ease-out and 140ms ease-in when the implementation needs concrete defaults inside the accepted ranges.
- Model token roles for border base, head color, tail color, glow color, beam opacity, and glow opacity.
- For neutral themes, use white or cool silver on dark surfaces and charcoal or silver on light surfaces.
- For brand themes, use a single brand hue with a white-hot center.
- For success themes, prefer a cool green or cyan hybrid that communicates execution without implying completion.
- Treat Variant A as the MoonMind default: narrow bright head, minimal or soft tail, and subtle glow.
- Treat Variant B as a more noticeable energized beam with wider tail, more visible outer bloom, and optionally faster orbit.
- Treat Variant C as a dual-phase orbit with one bright head and one faint companion segment offset behind it.
- Ensure all tuning preserves the declarative active-state rendering rules: resting border ring, one conic-gradient beam masked to the ring, continuous center rotation, and optional softened glow companion layer.
- Preserve MM-467 and coverage IDs DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-012, and DESIGN-REQ-011 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- No easing on the orbital animation itself.
- No strong red or orange warning-style pulse for execution state.
- No theme or intensity setting that outshines primary buttons, selected states, or error states.
- No variant that turns the beam into a spinner, full-card shimmer, or background fill.
- No motion, glow, or trail option that overlays or obscures the content area.
- No removal of the resting border while active.

Validation
- Verify the default active motion is a 3.6s linear infinite clockwise orbit.
- Verify slow, medium, and fast speed presets resolve to 4.8s, 3.6s, and 2.8s per orbit.
- Verify explicit seconds are honored as explicit duration values.
- Verify the orbit remains linear and continuous without easing.
- Verify activation fades beam opacity to target over 160-240ms and deactivation fades beam and glow out over 120-180ms.
- Verify theme and intensity controls set resting border, head, tail, glow, and opacity token values that remain usable on dark and light surfaces.
- Verify Variant A precision glint with soft trail and low glow is the preferred default.
- Verify configured motion, theme, intensity, trail, glow, and variant values do not compromise the border-only mask or content readability.

Needs Clarification
- None
