# Feature Specification: Shared Additive Shimmer Masks

**Feature Branch**: `301-shared-additive-shimmer`
**Created**: 2026-05-05
**Status**: Draft
**Input**:

```text
Implement the document update and the actual shimmer additive effect. Use the best looking additive effect even if it requires a modern browser:

Yes. It is possible, and it would probably be better than the current separate “shimmer + independently timed text brightening” illusion.

MoonMind already has the right pieces: the design describes a physical shimmer layer plus optional foreground text brightening, with ::before / ::after overlays and glyph spans for the label. The current implementation also already splits executing labels into grapheme spans in ExecutionStatusPill.tsx, hides the visual glyphs from assistive tech, and uses executionStatusPillProps() to attach the shimmer metadata only when appropriate. The selector contract is centralized around data-state="executing" / data-effect="shimmer-sweep" for executing/planning pills.

The best implementation would be to treat the shimmer as a single moving light field, then expose that same light field through three masks:

1. Fill shimmer: existing broad sweep across the pill interior.
2. Text shimmer mask: same moving gradient, clipped to the text/glyph shapes.
3. Border shimmer mask: same moving gradient, clipped to the pill border ring.

That gives the visual impression that the shimmer is physically passing over the component and brightening only the pixels it overlaps.

I would not use JavaScript animation for this. The existing spec is right that the browser implementation should stay CSS-driven and avoid rerendering React on a timer. For this upgrade, the implementation should remain CSS-only after render.

Update docs/UI/EffectShimmerSweep.md to replace “optional foreground text brightening” with a stronger desired state:

The executing shimmer is a shared additive light field. The fill, border, and text treatments are masks of the same moving light field, not independent animations. Text brightening and border glint must remain phase-locked to the physical sweep and should visually brighten only where the sweep overlaps the glyph or border region.

Then implement it in mission-control.css as:

layers:
  base_pill: normal executing state
  shared_light_field: moving diagonal shimmer
  fill_mask: light visible across interior
  border_mask: same light clipped to border ring
  text_mask: same light clipped to visible label glyphs
fallback:
  reduced_motion: static active highlight
  unsupported_mask_or_blend: existing glyph brightening

One caveat: true additive compositing depends on browser support. mix-blend-mode: screen is broadly safer; plus-lighter can look closer to actual additive light but should be tested in light and dark themes. Because MoonMind already uses isolation: isolate in its visual system, this is a good fit: the shimmer can brighten the pill’s own text/border without accidentally blending with the entire page.

So: yes, definitely possible. I would model it as “one sweep, multiple masks,” not as a separate text animation.
```

## User Story - Phase-Locked Additive Shimmer

**Summary**: As a Mission Control user, I want executing and planning status pills to show one coherent shimmer light passing through fill, border, and text so active progress looks physical and intentional.

**Goal**: Active status pills communicate progress through one phase-locked additive shimmer treatment without changing labels, layout, accessibility semantics, or update behavior.

**Independent Test**: Render active and inactive status pills, inspect the documented effect contract and stylesheet rules, and verify that only active shimmer pills expose one shared moving light field through fill, border, and text masks, with reduced-motion and unsupported text-mask fallbacks.

**Acceptance Scenarios**:

1. **Given** a status pill opts into the shimmer effect, **When** it renders, **Then** the fill shimmer, border glint, and text shimmer are masks of the same moving light field.
2. **Given** an active status pill contains glyph-rendered label text, **When** the sweep moves across the pill, **Then** text brightening is clipped to the visible label shape and remains phase-locked to the fill sweep.
3. **Given** an active status pill renders in a browser that supports additive blending and text clipping, **When** the shimmer runs, **Then** the effect uses the strongest available additive compositing while keeping the pill isolated from the rest of the page.
4. **Given** reduced motion is requested, **When** an active status pill renders, **Then** the animated light field stops and the pill retains a static active highlight.
5. **Given** text clipping or additive compositing is unsupported, **When** an active status pill renders, **Then** the existing glyph brightening fallback remains available without becoming the primary effect.
6. **Given** a non-active status pill renders, **When** the page updates, **Then** it does not inherit the shimmer, border glint, text mask, or fallback glyph animation.

### Edge Cases

- Active labels may contain multiple graphemes, whitespace, or compact status words.
- Existing hosts may use the preferred data attributes or the approved executing/planning class markers.
- High-contrast or forced-colors modes may reject clipped text and blend effects.
- Browser support may differ between additive blend modes, mask compositing, and text clipping.

## Assumptions

- Planning pills intentionally share the executing shimmer selector contract already used by the implementation.
- A modern-browser primary path is acceptable when reduced-motion, forced-colors, and unsupported text-mask fallbacks remain readable.
- The existing status-pill label and grapheme markup remain the source for visible text.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | User request shared light-field model | Active shimmer must be modeled as one moving light field exposed through fill, text, and border masks. | In scope | FR-001, FR-002 |
| DESIGN-REQ-002 | User request CSS-only animation | The effect must remain CSS-driven after render and must not use JavaScript timer animation. | In scope | FR-005 |
| DESIGN-REQ-003 | User request documentation update | `docs/UI/EffectShimmerSweep.md` must describe the shared additive light-field desired state instead of independent text brightening. | In scope | FR-007 |
| DESIGN-REQ-004 | User request fallback model | Reduced motion must use a static active highlight, and unsupported mask or blend behavior may fall back to existing glyph brightening. | In scope | FR-004, FR-006 |
| DESIGN-REQ-005 | Existing selector contract | Shimmer activation remains centralized around active status-pill selectors and must not leak to inactive states. | In scope | FR-003 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Active shimmer status pills MUST expose the fill, border, and text treatments as masks of one shared moving light field.
- **FR-002**: The text shimmer MUST brighten visible label glyph regions only where the shared light field overlaps the label.
- **FR-003**: Non-active status pills MUST NOT receive the shared light field, text mask, border mask, or fallback glyph animation.
- **FR-004**: Reduced-motion users MUST receive a static active highlight with no animated shimmer, border, text, or fallback glyph motion.
- **FR-005**: The shimmer motion MUST remain CSS-driven after render and MUST NOT rely on JavaScript animation loops or repeated React renders.
- **FR-006**: Browsers without the primary text-mask path MUST retain a readable glyph brightening fallback tied to the shared cycle timing.
- **FR-007**: The shimmer design document MUST state that fill, border, and text are phase-locked masks of the same additive light field.
- **FR-008**: The implementation MUST preserve status label content, accessibility labels, layout dimensions, and existing status metadata attachment.

### Key Entities

- **Active Status Pill**: A status presentation element for executing or planning work that opts into the shimmer-sweep effect.
- **Shared Light Field**: The moving shimmer gradient used as the single source for fill, border, and text highlights.
- **Mask Treatment**: A visual clipping region that exposes the shared light field in the fill, border ring, or visible label text.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Stylesheet verification confirms active shimmer pills define shared fill and border masks using the same moving light-field gradient and animation.
- **SC-002**: Stylesheet verification confirms glyph-rendered labels expose a text-clipped shimmer overlay using the same moving light-field gradient and animation.
- **SC-003**: Verification confirms reduced-motion rules disable all shimmer and text-mask animation while preserving a static active highlight.
- **SC-004**: Verification confirms unsupported text-mask fallback uses the existing glyph brightening animation only as fallback behavior.
- **SC-005**: Documentation verification confirms `docs/UI/EffectShimmerSweep.md` describes the one-sweep, multiple-mask desired state.
