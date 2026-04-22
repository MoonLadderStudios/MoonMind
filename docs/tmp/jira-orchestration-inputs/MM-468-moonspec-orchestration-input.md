# MM-468 MoonSpec Orchestration Input

## Source

- Jira issue: MM-468
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Support reduced motion, accessibility, and performance guardrails
- Labels: `moonmind-workflow-mm-ac6fd0b8-6632-4551-8572-95a1d6e25f20`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-468 from MM project
Summary: Support reduced motion, accessibility, and performance guardrails
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-468 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-468: Support reduced motion, accessibility, and performance guardrails

Short Name
reduced-motion-accessibility-performance

Source Reference
- Source document: `docs/UI/EffectBorderBeam.md`
- Source title: Masked Conic Border Beam - Declarative Design
- Source sections: Reduced motion behavior, Accessibility and UX constraints, Performance guidance, Acceptance criteria, Non-goals
- Coverage IDs: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016

User Story
As an operator and end user, I need the border beam to respect reduced-motion preferences, remain accessible as a secondary status cue, and avoid expensive animation behavior, so execution state is visible without harming usability or dense-list performance.

Acceptance Criteria
- With reducedMotion auto and user preference enabled, orbital rotation stops and a static illuminated segment or very gentle 2.4-3.2s opacity pulse remains.
- With reducedMotion minimal, there is no movement and the active state is represented by a slightly brighter static border ring only.
- The effect is never the only indicator of execution state and is paired with an accessible label such as Executing.
- The visual treatment avoids strong red/orange pulses that imply warning and keeps average luminance low for dense lists.
- Animation uses transform/rotation or an equivalent composited angle where possible and avoids layout-triggering animation.
- Glow remains modest and is the first optional layer disabled for lower-power degradation.

Requirements
- Respect reducedMotion modes without replacing the beam with rapid pulsing.
- Stop orbital rotation when reducedMotion is auto and the user prefers reduced motion.
- Preserve a meaningful static active state through a static illuminated segment, very gentle opacity pulse, or slightly brighter static border ring depending on reducedMotion mode.
- Keep the effect distinguishable at small sizes without becoming noisy.
- Ensure the effect is decorative status communication only and is paired with a non-visual execution indicator.
- Avoid strong red or orange pulses that imply warning.
- Keep average luminance low enough for dense-list usage.
- Prefer composited transform or angle animation paths where possible.
- Avoid layout-triggering animation.
- Keep glow modest and degrade lower-power behavior by disabling glow before removing the primary active-state cue.
- Keep all non-goals enforced under accessibility and performance modes.

Relevant Implementation Notes
- Use reducedMotion auto to honor user reduced-motion preferences.
- In reducedMotion auto with reduced-motion preference enabled, stop orbit animation and keep a static illuminated segment on one edge or corner, with only an optional gentle 2.4-3.2s opacity pulse.
- In reducedMotion minimal, disable movement entirely and represent active state with a slightly brighter static border ring.
- Do not use rapid pulsing as the reduced-motion replacement.
- Pair the visual effect with accessible text or status semantics such as Executing so the beam is never the only indicator of execution state.
- Preserve content readability and keep the animated beam constrained to the border ring.
- Avoid warning-like red or orange motion treatments for execution state.
- Keep dense-list behavior calm by maintaining low average luminance and modest glow.
- Prefer a single rotating pseudo-element or composited layer for active animation.
- Animate transform/rotation or an equivalent composited angle where possible.
- Avoid animating properties that trigger layout.
- Disable optional glow first when degrading for lower-power devices.
- Preserve MM-468 and coverage IDs DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, and DESIGN-REQ-016 in downstream MoonSpec artifacts and final implementation evidence.

Non-Goals
- No rapid pulsing as a substitute for reduced-motion behavior.
- No full-card shimmer.
- No filling background gradient.
- No spinner icon replacement.
- No completion pulse or success burst.
- No content-area masking beyond the border ring.
- No effect state that makes the beam the only indicator of execution.
- No expensive glow behavior retained when lower-power degradation is needed.

Validation
- Verify reducedMotion auto stops orbital rotation when the user prefers reduced motion.
- Verify reducedMotion auto keeps a static illuminated segment or only a very gentle 2.4-3.2s opacity pulse.
- Verify reducedMotion minimal disables movement and uses a slightly brighter static border ring.
- Verify the execution state has an accessible non-visual indicator such as Executing.
- Verify the visual treatment avoids strong red and orange warning-like pulses.
- Verify dense-list presentation keeps average luminance low and remains distinguishable without becoming noisy.
- Verify animation uses transform/rotation or an equivalent composited angle where possible.
- Verify implementation avoids layout-triggering animation.
- Verify glow remains modest and is the first optional layer disabled during lower-power degradation.
- Verify accessibility and performance modes still enforce the border-only mask and other source non-goals.

Needs Clarification
- None
