# Story Breakdown: Masked Conic Border Beam

- Source design: `docs/UI/EffectBorderBeam.md`
- Original source reference path: `docs/UI/EffectBorderBeam.md`
- Story extraction date: `2026-04-22T16:53:50Z`
- Requested output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines a standalone MaskedConicBorderBeam status effect for executing state. It uses a masked rotating conic-gradient glint confined to a rounded border ring, with optional glow and trail layers, configurable motion/color controls, reduced-motion behavior, accessibility guardrails, and performance constraints. The effect is explicitly decorative and must not become a full-card shimmer, spinner replacement, completion signal, or content-area overlay.

## Coverage Points

- `DESIGN-REQ-001` (requirement): **Standalone executing status border effect** — Define MaskedConicBorderBeam as an isolated active/executing visual treatment for wrapping rectangular UI surfaces. Source: Goal; Component contract.
- `DESIGN-REQ-002` (requirement): **Intentional perimeter-only motion** — The effect must convey active execution through directional perimeter motion without reading as a full-card shimmer, spinner, error pulse, or primary focus treatment. Source: Design intent; Effect summary.
- `DESIGN-REQ-003` (integration): **Configurable public inputs** — The component contract exposes active, radius, width, speed, intensity, theme, direction, trail, glow, and reduced-motion controls. Source: Component contract / Inputs.
- `DESIGN-REQ-004` (state-model): **Layered visual model** — The effect is composed from host content, resting border, animated conic beam, optional glow, and optional trailing beam layers. Source: Visual model; Pseudostructure.
- `DESIGN-REQ-005` (constraint): **Border-ring geometry and masking** — The animated beam must be clipped to the outer-minus-inner rounded rectangle ring, using borderWidth as the inset and preserving corner geometry. Source: Geometry and masking; Declarative rendering rules.
- `DESIGN-REQ-006` (requirement): **Beam arc composition** — The conic gradient uses mostly transparent stops, a soft lead-in, a narrow bright head, trailing fade, and recommended/default head and tail arc sizes. Source: Geometry and masking; Gradient composition.
- `DESIGN-REQ-007` (requirement): **Continuous linear orbit and direction** — The primary beam rotates around the center linearly and continuously, defaults clockwise, and avoids easing for orbital movement. Source: Motion behavior.
- `DESIGN-REQ-008` (requirement): **Timing, entry, exit, and subtle modulation** — Named speed presets and default 3.6s orbit are defined, with fade-in/fade-out timings and optional low-amplitude opacity breathing. Source: Motion behavior / Recommended timing; Entry / exit; Optional secondary modulation.
- `DESIGN-REQ-009` (requirement): **Themeable color tokens and intensity** — Resting border, head, tail, glow, opacity, theme guidance, and default color token roles must support dark and light surfaces. Source: Color behavior; Recommended default tuning.
- `DESIGN-REQ-010` (state-model): **Declarative active and inactive states** — Inactive state shows no moving beam, while active state shows the resting border, masked rotating beam, and optional glow companion. Source: Declarative rendering rules.
- `DESIGN-REQ-011` (constraint): **Subtlety and optical continuity guardrails** — The effect must preserve content readability, avoid outshining higher-priority UI states, honor border radius, and keep consistent thickness around corners. Source: Declarative rendering rules.
- `DESIGN-REQ-012` (requirement): **Motion variants and MoonMind default** — Precision glint, energized beam, and dual-phase orbit variants are defined, with Variant A plus soft trail preferred for MoonMind execution state. Source: Motion variants.
- `DESIGN-REQ-013` (accessibility): **Reduced motion behavior** — Reduced-motion auto stops orbital rotation and uses a static illuminated segment or gentle pulse; minimal uses no movement and a brighter static ring. Source: Reduced motion behavior.
- `DESIGN-REQ-014` (accessibility): **Accessibility and UX constraints** — The visual effect cannot be the only execution indicator, should pair with text, avoid warning colors, remain low distraction, and stay legible at small sizes. Source: Accessibility and UX constraints.
- `DESIGN-REQ-015` (constraint): **Performance and degradation guidance** — Prefer a single composited rotating layer, avoid layout-triggering animation, keep glow modest, and disable glow first on lower-power devices. Source: Performance guidance.
- `DESIGN-REQ-016` (non-goal): **Explicit non-goals** — Exclude full-card shimmer, background-gradient fill, spinner replacement, completion pulse, success burst, and content-area masking beyond the border ring. Source: Non-goals.

## Ordered Story Candidates

### STORY-001: Define MaskedConicBorderBeam border-only contract

- Short name: `border beam contract`
- Source reference: `docs/UI/EffectBorderBeam.md` sections Goal, Design intent, Component contract, Declarative rendering rules, Non-goals
- Description: As a UI engineer, I need a standalone MaskedConicBorderBeam contract that exposes the required execution-state controls while rendering no content-area animation, so product surfaces can apply a consistent executing treatment without coupling it to a specific card implementation.
- Independent test: Render the component with active true and false across representative input combinations and assert that the public contract maps to the expected static or animated border-only layers without changing host content.
- Dependencies: None
- Needs clarification: None
- Scope:
  - Define the standalone MaskedConicBorderBeam delivery surface and accepted input values.
  - Map active and inactive states to decorative border-only output.
  - Codify that this is status decoration and not a spinner, full-card shimmer, completion signal, or content overlay.
- Out of scope:
  - Implementing a specific product card integration.
  - Creating Jira issues or downstream Moon Spec files during breakdown.
- Acceptance criteria:
  - A MaskedConicBorderBeam surface exists with active, borderRadius, borderWidth, speed, intensity, theme, direction, trail, glow, and reducedMotion inputs.
  - When active is false, no moving beam is rendered while a host/static border may remain available.
  - When active is true, the resting border and masked beam treatment are rendered as a wrapper for rectangular UI content.
  - The component documentation or tests explicitly reject full-card shimmer, background fills, spinner replacement, completion pulse, success burst, and content-area masking.
- Requirements:
  - Expose all declared component inputs with deterministic defaults.
  - Keep the effect standalone and suitable for wrapping arbitrary rectangular UI surfaces.
  - Represent executing state decoratively without becoming the only execution indicator.
- Source design coverage:
  - `DESIGN-REQ-001`: Owns the standalone component scope and output contract.
  - `DESIGN-REQ-002`: Owns the perimeter-motion intent and anti-spinner/anti-shimmer framing.
  - `DESIGN-REQ-003`: Owns the complete public input contract.
  - `DESIGN-REQ-010`: Owns active and inactive rendering state semantics.
  - `DESIGN-REQ-016`: Owns the explicit exclusions as guardrails.
- Assumptions:
  - Downstream implementation may choose CSS, SVG, canvas, or another rendering system as long as the contract is preserved.

### STORY-002: Render masked conic beam geometry and layers

- Short name: `masked beam geometry`
- Source reference: `docs/UI/EffectBorderBeam.md` sections Visual model, Geometry and masking, Pseudostructure, Gradient composition, Declarative rendering rules
- Description: As a UI engineer, I need the beam rendered as layered conic-gradient geometry clipped to the border ring, so the active execution glint travels around the perimeter while the content area remains unaffected.
- Independent test: Mount the effect around content and inspect computed structure or visual snapshots to confirm the interior area is masked out, the border ring remains visible, and the beam/glow honor the configured radius and width at corners.
- Dependencies: STORY-001
- Needs clarification: None
- Scope:
  - Implement or specify the host, resting border, animated beam, optional glow, and optional trailing-beam layer model.
  - Apply an outer-minus-inner rounded-rectangle mask using borderWidth as the inset.
  - Compose the main conic beam from transparent, tail, bright-head, and fade stops.
  - Preserve radius, thickness, and corner continuity.
- Out of scope:
  - Choosing product-specific colors or state labels.
  - Reduced-motion replacement behavior, except that geometry must still support a static segment.
- Acceptance criteria:
  - The animated beam is visible only in the border ring defined by the outer rounded rectangle minus the inset inner rounded rectangle.
  - The inner inset equals borderWidth, and corner radius is adjusted so the ring remains optically consistent.
  - The beam uses a mostly transparent conic gradient with a soft tail, narrow bright head, and fade back to transparency.
  - Optional glow derives from the beam footprint, remains lower opacity and blurred, and may straddle or expand slightly beyond the border.
  - Content readability is preserved because the animated treatment never fills or overlays the interior content area.
- Requirements:
  - Render a static resting border ring while active.
  - Support default bright head and trailing tail arcs of 12deg and 28deg, with acceptable ranges described by the source design.
  - Support optional soft or defined trailing beam behavior without changing orbital speed.
  - Maintain smooth motion continuity around all sides and rounded corners.
- Source design coverage:
  - `DESIGN-REQ-004`: Owns the layered visual model.
  - `DESIGN-REQ-005`: Owns border-ring masking and inset geometry.
  - `DESIGN-REQ-006`: Owns conic-gradient arc and stop composition.
  - `DESIGN-REQ-011`: Owns content readability, radius honoring, and consistent thickness constraints.
- Assumptions:
  - Visual regression or DOM/CSS assertions will be available in the downstream implementation environment.

### STORY-003: Tune motion, theme, and intensity presets

- Short name: `beam motion presets`
- Source reference: `docs/UI/EffectBorderBeam.md` sections Motion behavior, Color behavior, Recommended default tuning, Motion variants, Declarative rendering rules
- Description: As a product designer or UI engineer, I need the beam to provide predictable speed, direction, color, intensity, trail, glow, and variant tuning, so the same effect can communicate execution across dark and light MoonMind surfaces without overpowering other UI states.
- Independent test: Render active examples for each speed preset, direction, theme, intensity, trail, and glow mode and assert that tokens, duration, direction, and layer opacity/visibility match the declared contract.
- Dependencies: STORY-002
- Needs clarification: None
- Scope:
  - Define and validate slow, medium, fast, and explicit-second speed behavior.
  - Support clockwise and counterclockwise rotation with linear continuous motion.
  - Map neutral, brand, success, custom, subtle, normal, vivid, trail, and glow controls to tokenized visual output.
  - Preserve the MoonMind-style default of precision glint with a soft trail.
- Out of scope:
  - Runtime performance degradation policy beyond glow controls.
  - Accessibility reduced-motion substitutions.
- Acceptance criteria:
  - Default active motion is a 3.6s linear infinite clockwise orbit.
  - Slow, medium, and fast presets map to 4.8s, 3.6s, and 2.8s per orbit, while explicit seconds are honored.
  - Orbital animation avoids easing and reads as a continuous circulation path.
  - Activation fades beam opacity to target over 160-240ms and deactivation fades beam and glow out over 120-180ms.
  - Theme and intensity controls set resting border, head, tail, glow, and opacity token values that work on dark and light surfaces.
  - The preferred default is Variant A precision glint with a soft trail and low glow.
- Requirements:
  - Support optional secondary opacity breathing at plus/minus 8% over 1.8-2.4s without breaking continuous motion.
  - Keep the effect subtle enough that it does not outshine primary buttons, selected states, or error states.
  - Support precision glint, energized beam, and dual-phase orbit variants as configurable outcomes or documented mappings.
- Source design coverage:
  - `DESIGN-REQ-007`: Owns primary orbit, direction, and linearity.
  - `DESIGN-REQ-008`: Owns speed presets, entry/exit transitions, and optional breathing.
  - `DESIGN-REQ-009`: Owns color token roles, themes, and dark/light compatibility.
  - `DESIGN-REQ-012`: Owns motion variants and preferred MoonMind default.
  - `DESIGN-REQ-011`: Owns the subtlety threshold against higher-priority UI states.
- Assumptions:
  - The implementation platform can represent either transform rotation or an equivalent animatable angle variable.

### STORY-004: Support reduced motion, accessibility, and performance guardrails

- Short name: `beam guardrails`
- Source reference: `docs/UI/EffectBorderBeam.md` sections Reduced motion behavior, Accessibility and UX constraints, Performance guidance, Acceptance criteria, Non-goals
- Description: As an operator and end user, I need the border beam to respect reduced-motion preferences, remain accessible as a secondary status cue, and avoid expensive animation behavior, so execution state is visible without harming usability or dense-list performance.
- Independent test: Simulate reduced-motion preference and lower-power/degraded settings, then verify orbital movement stops or is minimized, the active state remains meaningful, text status remains available, and no layout-triggering animation is required.
- Dependencies: STORY-003
- Needs clarification: None
- Scope:
  - Implement reducedMotion auto, off, and minimal semantics.
  - Require the beam to be paired with a non-visual execution indicator such as text.
  - Keep warning-like colors, rapid pulsing, and noisy small-size behavior out of the effect.
  - Prefer compositor-friendly animation and graceful glow degradation.
- Out of scope:
  - Designing the full parent status component or copy system.
  - Implementing device benchmarking infrastructure.
- Acceptance criteria:
  - With reducedMotion auto and user preference enabled, orbital rotation stops and a static illuminated segment or very gentle 2.4-3.2s opacity pulse remains.
  - With reducedMotion minimal, there is no movement and the active state is represented by a slightly brighter static border ring only.
  - The effect is never the only indicator of execution state and is paired with an accessible label such as Executing.
  - The visual treatment avoids strong red/orange pulses that imply warning and keeps average luminance low for dense lists.
  - Animation uses transform/rotation or an equivalent composited angle where possible and avoids layout-triggering animation.
  - Glow remains modest and is the first optional layer disabled for lower-power degradation.
- Requirements:
  - Respect reducedMotion modes without replacing the beam with rapid pulsing.
  - Remain distinguishable at small sizes without becoming noisy.
  - Keep all non-goals enforced under accessibility and performance modes.
- Source design coverage:
  - `DESIGN-REQ-013`: Owns reduced-motion active-state substitutions.
  - `DESIGN-REQ-014`: Owns accessibility and UX constraints.
  - `DESIGN-REQ-015`: Owns performance and degradation guidance.
  - `DESIGN-REQ-016`: Owns non-goal enforcement under guardrail modes.
- Assumptions:
  - The parent surface or downstream consumer provides the non-visual execution label when integrating the effect.

## Coverage Matrix

- `DESIGN-REQ-001` Standalone executing status border effect: STORY-001
- `DESIGN-REQ-002` Intentional perimeter-only motion: STORY-001
- `DESIGN-REQ-003` Configurable public inputs: STORY-001
- `DESIGN-REQ-004` Layered visual model: STORY-002
- `DESIGN-REQ-005` Border-ring geometry and masking: STORY-002
- `DESIGN-REQ-006` Beam arc composition: STORY-002
- `DESIGN-REQ-007` Continuous linear orbit and direction: STORY-003
- `DESIGN-REQ-008` Timing, entry, exit, and subtle modulation: STORY-003
- `DESIGN-REQ-009` Themeable color tokens and intensity: STORY-003
- `DESIGN-REQ-010` Declarative active and inactive states: STORY-001
- `DESIGN-REQ-011` Subtlety and optical continuity guardrails: STORY-002, STORY-003
- `DESIGN-REQ-012` Motion variants and MoonMind default: STORY-003
- `DESIGN-REQ-013` Reduced motion behavior: STORY-004
- `DESIGN-REQ-014` Accessibility and UX constraints: STORY-004
- `DESIGN-REQ-015` Performance and degradation guidance: STORY-004
- `DESIGN-REQ-016` Explicit non-goals: STORY-001, STORY-004

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-002
- `STORY-004` depends on: STORY-003

## Out Of Scope

- Creating or modifying Moon Spec spec.md files during breakdown.
- Creating directories under specs/ during breakdown.
- Implementing the UI component in this breakdown step.
- Replacing text or semantic execution indicators with the visual effect alone.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
