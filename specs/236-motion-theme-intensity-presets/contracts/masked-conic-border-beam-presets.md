# Contract: MaskedConicBorderBeam Presets

The frontend continues to export `MaskedConicBorderBeam`, `MaskedConicBorderBeamProps`, and `MASKED_CONIC_BORDER_BEAM_TRACEABILITY` from `frontend/src/components/MaskedConicBorderBeam.tsx`.

## Public Props

`MaskedConicBorderBeamProps` includes:

- `speed?: 'slow' | 'medium' | 'fast' | `${number}s` | `${number}ms` | number`
- `direction?: 'clockwise' | 'counterclockwise'`
- `theme?: 'neutral' | 'brand' | 'success' | 'custom'`
- `intensity?: 'subtle' | 'normal' | 'vivid'`
- `trail?: 'none' | 'soft' | 'defined'`
- `glow?: 'off' | 'low' | 'medium'`
- `variant?: 'precision' | 'energized' | 'dualPhase'`
- `reducedMotion?: 'auto' | 'off' | 'minimal'`

## Rendered Contract

The root element:

- has class `masked-conic-border-beam`
- preserves `data-theme`, `data-intensity`, `data-direction`, `data-trail`, `data-glow`, `data-variant`, and `data-reduced-motion`
- sets `--beam-speed` according to the speed mapping
- allows caller-supplied CSS custom properties to override custom theme tokens

## CSS Contract

- Orbit animation uses `masked-conic-border-beam-orbit var(--beam-speed) linear infinite`.
- Counterclockwise direction reverses animation direction without changing speed.
- Enter and exit transition tokens are exposed as `--beam-enter-duration` and `--beam-exit-duration`.
- Theme and intensity selectors only adjust token values and do not remove border-ring masks.
- Variant selectors map precision, energized, and dual-phase outcomes without filling the content area.
- Dual-phase variant may render an additional decorative companion layer.

