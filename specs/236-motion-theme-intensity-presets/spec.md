# Feature Specification: Motion, Theme, and Intensity Presets

**Feature Branch**: `236-motion-theme-intensity-presets`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-467 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

```text
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
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable UI tuning story.
- Selected mode: Runtime.
- Source design: `docs/UI/EffectBorderBeam.md` is treated as runtime source requirements because the brief describes product behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-467 were found under `specs/`; specification is the first incomplete stage.

## User Story - Tune Border Beam Presets

**Summary**: As a product designer or UI engineer, I want MaskedConicBorderBeam to expose predictable motion, theme, intensity, trail, glow, and variant tuning so execution state remains consistent across dark and light MoonMind surfaces.

**Goal**: Product surfaces can use one reusable border-beam effect with stable defaults and named presets for speed, direction, theme, intensity, trail, glow, and motion variant without overpowering content or other UI states.

**Independent Test**: Render MaskedConicBorderBeam with default, speed preset, explicit duration, direction, theme, intensity, trail, glow, and variant states, then inspect DOM attributes and CSS rules to verify default timing, linear orbit, transition timings, token mappings, and traceability for MM-467.

**Acceptance Scenarios**:

1. **Given** MaskedConicBorderBeam is active with no tuning props, **When** it renders, **Then** it uses a 3.6s linear infinite clockwise orbit, normal intensity, neutral theme, soft trail, low glow, and reducedMotion auto.
2. **Given** slow, medium, fast, numeric seconds, or explicit CSS duration values are supplied, **When** the surface renders, **Then** the orbit duration resolves to 4.8s, 3.6s, 2.8s, numeric seconds, or the explicit duration respectively.
3. **Given** clockwise or counterclockwise direction is supplied, **When** the beam animates, **Then** the orbit remains linear and continuous and counterclockwise reverses direction without changing speed or footprint.
4. **Given** activation or deactivation state changes, **When** beam and glow layers enter or exit, **Then** opacity transitions use the accepted timing ranges while the orbit itself remains linear.
5. **Given** neutral, brand, success, or custom theme and subtle, normal, or vivid intensity are supplied, **When** the surface renders, **Then** border, head, tail, glow, beam opacity, and glow opacity tokens resolve to usable values for dark and light surfaces without overpowering primary, selected, or error states.
6. **Given** precision glint, energized beam, or dual-phase orbit variants are supplied, **When** the surface renders, **Then** each variant maps to documented tuning outcomes while preserving border-only rendering and content readability.

### Edge Cases

- `speed` is a number, named preset, seconds string, or milliseconds string.
- `glow` is off while active motion remains visible.
- `theme` is custom and callers provide CSS custom properties through `style`.
- `intensity` is vivid but must still remain below primary, selected, and error state prominence.
- `variant` changes the visual footprint or companion layer without changing the semantic active state.
- Reduced-motion behavior remains compatible with tuned speed and variant values.

## Assumptions

- MM-465 and MM-466 established the reusable MaskedConicBorderBeam component, border-only contract, and masked conic geometry; this story tunes that existing runtime surface rather than creating a second component.
- Dark and light surface compatibility can be validated through stable token mappings and CSS contract tests without full visual screenshot automation.
- The preferred default variant is precision glint with a soft trail and low glow.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-007 | `docs/UI/EffectBorderBeam.md` Motion behavior | Active beam motion is a linear continuous orbit; default is 3.6s clockwise, slow/medium/fast map to 4.8s/3.6s/2.8s, and entry/exit opacity transitions use defined timing ranges. | In scope | FR-001, FR-002, FR-003, FR-004 |
| DESIGN-REQ-008 | `docs/UI/EffectBorderBeam.md` Color behavior | Theme and token roles define resting border, head, tail, glow, beam opacity, and glow opacity values that remain usable on dark and light surfaces. | In scope | FR-005, FR-006, FR-007 |
| DESIGN-REQ-009 | `docs/UI/EffectBorderBeam.md` Recommended default tuning | Default tuning is active, 16px radius, 1.5px border, 3.6s speed, clockwise direction, normal intensity, soft trail, low glow, reducedMotion auto, 12deg head, and 28deg tail. | In scope | FR-001, FR-008 |
| DESIGN-REQ-012 | `docs/UI/EffectBorderBeam.md` Motion variants | Precision glint, energized beam, and dual-phase orbit variants are supported as configurable or documented outcomes, with precision glint as the MoonMind default. | In scope | FR-009, FR-010 |
| DESIGN-REQ-011 | `docs/UI/EffectBorderBeam.md` Declarative rendering rules | Tuning controls preserve active rendering as a resting border, masked conic beam, optional glow, and readable content area. | In scope | FR-011 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST default active MaskedConicBorderBeam motion to a 3.6s linear infinite clockwise orbit.
- **FR-002**: The system MUST map speed presets exactly: slow to 4.8s, medium to 3.6s, and fast to 2.8s per orbit.
- **FR-003**: The system MUST honor explicit numeric seconds, seconds strings, and milliseconds strings as explicit orbit durations.
- **FR-004**: The system MUST support clockwise and counterclockwise direction without changing orbit speed, easing, or beam footprint.
- **FR-005**: The system MUST expose theme token mappings for resting border, beam head, beam tail, glow, beam opacity, and glow opacity.
- **FR-006**: The system MUST support neutral, brand, success, and custom theme outcomes that remain compatible with dark and light surfaces.
- **FR-007**: The system MUST support subtle, normal, and vivid intensity outcomes while keeping the effect below primary buttons, selected states, and error states.
- **FR-008**: Activation and deactivation opacity transitions for beam and glow MUST remain within the 160-240ms enter and 120-180ms exit ranges while the orbit itself remains linear.
- **FR-009**: The system MUST expose precision glint, energized beam, and dual-phase orbit variants as configurable outcomes or documented mappings.
- **FR-010**: The default variant MUST be precision glint with a soft trail and low glow.
- **FR-011**: All tuning controls MUST preserve the border-only rendering behavior, optional glow separation, and content readability established by DESIGN-REQ-011.
- **FR-012**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-467.

### Key Entities

- **MaskedConicBorderBeam Surface**: A reusable visual wrapper that owns active state decoration, motion timing, theme tokens, intensity tokens, variants, and decorative layers.
- **Motion Preset**: A named or explicit duration value that determines orbit speed without changing the beam footprint.
- **Theme Token Set**: The resolved custom properties for resting border, beam head, tail, glow, and opacity values.
- **Motion Variant**: A named tuning profile that changes visual emphasis while preserving the same border-only status effect contract.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Component tests verify default active motion resolves to 3.6s, clockwise, normal intensity, neutral theme, soft trail, low glow, and reducedMotion auto.
- **SC-002**: Component tests verify slow, medium, fast, numeric seconds, seconds strings, and milliseconds strings resolve to the expected `--beam-speed` values.
- **SC-003**: CSS contract tests verify the orbit animation uses `linear infinite`, counterclockwise reverses direction, and trail/variant rules do not override orbit speed.
- **SC-004**: CSS contract tests verify enter and exit opacity transition durations are inside the accepted timing ranges.
- **SC-005**: Component and CSS tests verify theme and intensity token mappings for neutral, brand, success, custom, subtle, normal, and vivid outcomes.
- **SC-006**: Component and CSS tests verify precision glint is the default variant and energized and dual-phase variants map to distinct tuning outcomes while preserving border-only content readability.
- **SC-007**: MM-467 appears in the spec, plan, tasks, verification evidence, traceability export, and final implementation summary.

