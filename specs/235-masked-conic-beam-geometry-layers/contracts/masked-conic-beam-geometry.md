# Contract: MaskedConicBorderBeam Geometry and Layers

## Public Surface

The frontend continues to export `MaskedConicBorderBeam`, `MaskedConicBorderBeamProps`, and `MASKED_CONIC_BORDER_BEAM_TRACEABILITY` from `frontend/src/components/MaskedConicBorderBeam.tsx`.

MM-466 does not add a second component. It extends the observable geometry contract for the existing component.

## Root Element

The root element:
- has class `masked-conic-border-beam`
- exposes `data-active`, `data-trail`, `data-glow`, and existing contract attributes
- sets `--beam-border-radius`
- sets `--beam-border-width`
- sets `--beam-speed`
- sets or inherits default geometry variables:
  - `--beam-head-arc: 12deg`
  - `--beam-tail-arc: 28deg`
  - `--beam-inner-inset: var(--beam-border-width)`
  - `--beam-inner-radius: calc(var(--beam-border-radius) - var(--beam-border-width))`

## Active Layers

When `active` is true:
- `.masked-conic-border-beam__layer` renders as an `aria-hidden` decorative layer.
- `.masked-conic-border-beam__glow` renders only when glow is not `off`.
- `.masked-conic-border-beam__content` wraps children and remains separate from decorative layers.

When `active` is false:
- moving beam and glow layers do not render.
- child content remains rendered.

## Border Ring Mask

Beam and glow layers must:
- use `padding: var(--beam-inner-inset)` or an equivalent border-width-derived inset
- use a mask equivalent to outer rounded rectangle minus inner rounded rectangle
- preserve `mask-composite: exclude` and `-webkit-mask-composite: xor` behavior
- derive inner corner radius from `--beam-inner-radius`

## Beam Footprint

The main beam layer must use a conic-gradient footprint containing:
- a large transparent region
- a soft trailing tail using `--beam-tail-color`
- a bright narrow head using `--beam-head-color`
- a fade back to transparency
- default `--beam-head-arc` of `12deg`
- default `--beam-tail-arc` of `28deg`

Trail variants may change footprint distribution but must not change `--beam-speed` or use a separate animation duration.

## Glow Footprint

The glow layer must:
- derive from the same angular footprint family as the beam
- use lower opacity through `--beam-glow-opacity`
- use blur
- remain decorative and unable to cover semantic content

## Traceability

`MASKED_CONIC_BORDER_BEAM_TRACEABILITY` must preserve:
- `MM-465` and its design requirements from the existing component contract
- `MM-466`
- `DESIGN-REQ-004`
- `DESIGN-REQ-005`
- `DESIGN-REQ-006`
- `DESIGN-REQ-011`
