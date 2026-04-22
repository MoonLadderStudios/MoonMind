# Feature Specification: Masked Conic Beam Geometry and Layers

**Feature Branch**: `235-masked-conic-beam-geometry-layers`  
**Created**: 2026-04-22  
**Status**: Draft  
**Input**:

```text
Use the Jira preset brief for MM-466 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
```

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-466-moonspec-orchestration-input.md`

## Original Jira Preset Brief

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
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable UI engineering story.
- Selected mode: Runtime.
- Source design: `docs/UI/EffectBorderBeam.md` is treated as runtime source requirements because the brief describes product behavior, not documentation-only work.
- Resume decision: No existing Moon Spec artifacts for MM-466 were found under `specs/`; specification is the first incomplete stage.

## User Story - Render Border-Ring Beam Geometry

**Summary**: As a UI engineer, I want the MaskedConicBorderBeam surface to render layered conic-gradient beam geometry clipped to the border ring so active execution motion remains on the perimeter and never covers content.

**Goal**: Product surfaces can show an active execution glint with static border, animated beam, optional glow, and optional trailing treatment while preserving interior readability and smooth rounded-corner continuity.

**Independent Test**: Render MaskedConicBorderBeam in active, inactive, glow, trail, and custom geometry states, then inspect DOM attributes and CSS rules to verify the beam and glow are layered, conic-gradient based, masked to the border ring, and excluded from the content area.

**Acceptance Scenarios**:

1. **Given** MaskedConicBorderBeam is active with default geometry, **When** it renders, **Then** it shows a static resting border ring plus one animated conic beam layer clipped to the border ring.
2. **Given** borderWidth and borderRadius are supplied, **When** the surface renders, **Then** the inner mask inset equals the border width and the inner radius is derived so rounded corners remain visually consistent.
3. **Given** the default beam footprint is inspected, **When** the conic gradient is defined, **Then** most of the orbit is transparent, the bright head is approximately 12 degrees, the trailing tail is approximately 28 degrees, and the beam fades back to transparency.
4. **Given** glow is enabled, **When** the surface renders, **Then** the glow uses the beam footprint at lower opacity with blur and may straddle or slightly expand beyond the border without covering content.
5. **Given** child text or controls are wrapped by the surface, **When** the beam is active, **Then** the animated beam and glow do not fill, mask, overlay, or animate the interior content area.
6. **Given** trail behavior is soft or defined, **When** the beam animates, **Then** the trailing treatment changes only the beam footprint and does not change the orbital speed.

### Edge Cases

- `borderWidth` is supplied as a token, px value, or number.
- `borderRadius` is smaller than or close to the border width.
- Glow is disabled while the beam remains active.
- Trail is disabled, soft, or defined.
- Direction changes while the same beam footprint and speed are preserved.
- Arbitrary nested child content must remain readable and outside the animated layers.

## Assumptions

- MM-465 established the reusable MaskedConicBorderBeam contract; this story completes the source-design geometry and layering details on that existing component.
- Visual geometry can be validated through stable DOM attributes, CSS variables, CSS contract tests, and component-level rendering tests without adopting the effect on a specific Mission Control card.
- The default head and tail arc values may be represented as CSS custom properties as long as tests can verify the default values and conic gradient uses them.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-004 | `docs/UI/EffectBorderBeam.md` Visual model | The active effect is composed from a host surface, static resting border ring, animated conic beam, optional outer glow, and optional trailing beam. | In scope | FR-001, FR-002, FR-007 |
| DESIGN-REQ-005 | `docs/UI/EffectBorderBeam.md` Geometry and masking | The beam is visible only in the border ring formed by subtracting an inset inner rounded rectangle from the outer rounded rectangle; the inner inset equals borderWidth and the radius is adjusted for optical consistency. | In scope | FR-003, FR-004, FR-005 |
| DESIGN-REQ-006 | `docs/UI/EffectBorderBeam.md` Gradient composition | The main beam uses a mostly transparent conic gradient with soft lead-in, narrow bright head, soft trailing fade, and return to transparency; the default footprint uses a 12deg bright head and 28deg tail. | In scope | FR-006, FR-007 |
| DESIGN-REQ-011 | `docs/UI/EffectBorderBeam.md` Declarative rendering rules, Pseudostructure | Active rendering keeps the resting border, masked beam, and optional glow as separate layers while preserving content readability and excluding interior animation. | In scope | FR-001, FR-002, FR-003, FR-008, FR-009 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST render active MaskedConicBorderBeam as layered visual geometry containing host content, static resting border, animated beam layer, and optional glow layer.
- **FR-002**: The animated beam and optional glow layers MUST be rendered separately from host content and marked decorative so they do not alter content semantics.
- **FR-003**: The animated beam MUST be visible only in the border ring produced by subtracting an inset inner rounded rectangle from the outer rounded rectangle.
- **FR-004**: The border-ring mask MUST derive its inner inset from the configured borderWidth.
- **FR-005**: The border-ring mask MUST adjust inner corner radius so rounded corners remain optically consistent across supported borderWidth and borderRadius values.
- **FR-006**: The default beam footprint MUST expose a bright head of 12deg and trailing tail of 28deg, with configurable CSS variables or rendered attributes for verification.
- **FR-007**: The main beam MUST use a conic-gradient footprint with a mostly transparent orbit, soft lead-in, narrow bright head, soft trailing fade, and return to transparency.
- **FR-008**: Optional glow MUST derive from the same beam footprint at lower opacity with blur and MUST NOT obscure or overlay interior content.
- **FR-009**: Optional trail behavior MUST change only the beam footprint or opacity distribution and MUST NOT change orbital speed.
- **FR-010**: Interior content MUST remain fully readable and unaffected by beam, glow, mask, and trail layers.
- **FR-011**: Moon Spec artifacts, verification evidence, commit text, and pull request metadata for this work MUST preserve Jira issue key MM-466.

### Key Entities

- **MaskedConicBorderBeam Surface**: A reusable visual wrapper that owns active state decoration, border geometry, beam layers, mask variables, and optional glow or trail behavior.
- **Border Ring Mask**: The visible region between an outer rounded rectangle and an inset inner rounded rectangle.
- **Beam Footprint**: The angular conic-gradient distribution containing transparent orbit, soft tail, bright head, and fade back to transparency.
- **Host Content**: Arbitrary child content that remains visually and semantically unaffected by decorative layers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Component tests verify active rendering includes static border, animated beam, optional glow, and content layers as separate observable elements or CSS contracts.
- **SC-002**: CSS contract tests verify the border-ring mask uses configured borderWidth for the inner inset and derives an adjusted inner radius.
- **SC-003**: CSS contract tests verify the default beam exposes 12deg head and 28deg tail values and uses them in a mostly transparent conic-gradient footprint.
- **SC-004**: Component or CSS tests verify glow derives from the beam footprint with lower opacity and blur without covering content.
- **SC-005**: Component-level rendering tests verify child text or controls remain readable and outside animated or masked layers while active.
- **SC-006**: MM-466 appears in the spec, plan, tasks, verification evidence, and final implementation summary.
