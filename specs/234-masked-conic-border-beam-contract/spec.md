# Feature Specification: MaskedConicBorderBeam Border-Only Contract

**Feature Branch**: `234-masked-conic-border-beam-contract`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-465 as the canonical Moon Spec orchestration input.

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
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable UI engineering story.
- Selected mode: Runtime.
- Source design: `docs/UI/EffectBorderBeam.md` is treated as runtime source requirements because the brief describes product behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-465 were found under `specs/`; specification is the first incomplete stage.

## User Story - Provide Standalone Border Beam Surface

**Summary**: As a UI engineer, I want a reusable MaskedConicBorderBeam surface so execution-state treatments can wrap rectangular content with a border-only animated beam without coupling the effect to a specific card.

**Goal**: Product surfaces can apply a consistent decorative executing treatment through one bounded component contract while preserving content readability and relying on separate text/status indicators for state meaning.

**Independent Test**: Render the surface with active and inactive states, inspect its exposed contract and DOM attributes, and verify CSS confines the moving conic treatment to a masked border ring with reduced-motion behavior.

**Acceptance Scenarios**:

1. **Given** a product surface renders MaskedConicBorderBeam with default props, **When** it is active, **Then** it exposes deterministic defaults for all declared inputs and renders a resting border plus animated border-only beam layers around its children.
2. **Given** MaskedConicBorderBeam is inactive, **When** it wraps content, **Then** no moving beam or glow layer is rendered while a static host border may remain.
3. **Given** custom borderRadius, borderWidth, speed, intensity, theme, direction, trail, glow, and reducedMotion inputs are supplied, **When** the surface renders, **Then** the contract maps them to stable attributes or style variables without changing child content.
4. **Given** the beam CSS is inspected, **When** the animated layer is defined, **Then** it uses a conic gradient masked to the border ring and does not animate the content area.
5. **Given** reduced motion is requested, **When** the surface renders or the user prefers reduced motion, **Then** orbital animation stops and the active state remains visually meaningful without rapid pulsing.

### Edge Cases

- An arbitrary child tree contains text, controls, or nested panels.
- `active` changes from true to false.
- Direction is counterclockwise.
- Trail or glow are disabled independently.
- A custom numeric radius, width, or speed is supplied.
- Reduced-motion mode is `minimal` or derived from user preference.

## Assumptions

- The first runtime slice should deliver a reusable frontend component and CSS contract; adoption by specific Mission Control cards can be a later story.
- The decorative beam is not responsible for rendering the textual `Executing` indicator.
- Existing Mission Control CSS tokens can provide theme colors without adding a new design system package.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/UI/EffectBorderBeam.md` Goal, Design intent | The effect conveys active execution as perimeter motion without becoming a spinner or full-surface shimmer. | In scope | FR-003, FR-009 |
| DESIGN-REQ-002 | `docs/UI/EffectBorderBeam.md` Component contract | The component exposes active, borderRadius, borderWidth, speed, intensity, theme, direction, trail, glow, and reducedMotion inputs. | In scope | FR-001, FR-002 |
| DESIGN-REQ-003 | `docs/UI/EffectBorderBeam.md` Geometry and masking, Declarative rendering rules | The animated beam is visible only in the border ring and never fills or masks the interior content area. | In scope | FR-004, FR-005 |
| DESIGN-REQ-010 | `docs/UI/EffectBorderBeam.md` Reduced motion behavior | Reduced-motion modes provide static or minimal active treatments without rapid pulsing. | In scope | FR-007 |
| DESIGN-REQ-016 | `docs/UI/EffectBorderBeam.md` Non-goals | Full-card shimmer, filling background gradients, spinner replacement, completion pulse, success burst, and content-area masking are excluded. | In scope | FR-008, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a reusable MaskedConicBorderBeam surface for wrapping arbitrary rectangular UI content.
- **FR-002**: The surface MUST accept active, borderRadius, borderWidth, speed, intensity, theme, direction, trail, glow, and reducedMotion inputs with deterministic defaults.
- **FR-003**: When active is true, the surface MUST render a resting border and decorative moving beam treatment around the perimeter.
- **FR-004**: The moving beam treatment MUST be constrained to the border ring and MUST NOT render over or animate the content area.
- **FR-005**: When active is false, the surface MUST NOT render moving beam or glow layers while allowing static host border styling.
- **FR-006**: Custom input values MUST be reflected through stable rendered attributes or style variables so downstream surfaces can test and tune the contract.
- **FR-007**: Reduced-motion behavior MUST stop orbital motion while preserving a static meaningful active state.
- **FR-008**: Tests or contract evidence MUST reject full-card shimmer, background fills, spinner replacement, completion pulse, success burst, and content-area masking as part of this component.
- **FR-009**: The component MUST remain decorative and MUST NOT become the only execution-state indicator.
- **FR-010**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-465.

### Key Entities

- **MaskedConicBorderBeam Surface**: A reusable visual wrapper that owns active state decoration, border geometry, theme attributes, and reduced-motion behavior.
- **Beam Layer**: A decorative conic-gradient layer clipped to the border ring.
- **Host Content**: Arbitrary child content that remains readable and outside the animated treatment.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Component tests verify all declared inputs and deterministic defaults.
- **SC-002**: Inactive-state tests verify no moving beam or glow layers are rendered.
- **SC-003**: CSS contract tests verify the beam uses conic-gradient rendering and border-ring masking, with content-area animation excluded.
- **SC-004**: Reduced-motion tests verify animation is stopped for minimal mode and user preference handling exists.
- **SC-005**: MM-465 appears in the spec, plan, tasks, verification evidence, and final implementation summary.
