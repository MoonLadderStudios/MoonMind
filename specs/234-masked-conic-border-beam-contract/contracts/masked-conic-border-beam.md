# Contract: MaskedConicBorderBeam

## Public Surface

The frontend exports `MaskedConicBorderBeam` and `MaskedConicBorderBeamProps` from `frontend/src/components/MaskedConicBorderBeam.tsx`.

## Inputs

| Input | Values | Default |
| --- | --- | --- |
| `active` | boolean | `true` |
| `borderRadius` | CSS length or number | `16px` |
| `borderWidth` | CSS length or number | `1.5px` |
| `speed` | `slow`, `medium`, `fast`, CSS time, or number seconds | `medium` |
| `intensity` | `subtle`, `normal`, `vivid` | `normal` |
| `theme` | `neutral`, `brand`, `success`, `custom` | `neutral` |
| `direction` | `clockwise`, `counterclockwise` | `clockwise` |
| `trail` | `none`, `soft`, `defined` | `soft` |
| `glow` | `off`, `low`, `medium` | `low` |
| `reducedMotion` | `auto`, `off`, `minimal` | `auto` |

## Rendered Contract

- Root element has class `masked-conic-border-beam`.
- Root element exposes stable `data-*` attributes for active, intensity, theme, direction, trail, glow, and reducedMotion.
- Root style variables include `--beam-border-radius`, `--beam-border-width`, and `--beam-speed`.
- Active mode renders hidden decorative beam layers with `data-testid` values for tests.
- Inactive mode renders no moving beam or glow layers.
- Child content is rendered inside `.masked-conic-border-beam__content`.

## Exclusions

- The component does not render status text.
- The component does not render a spinner.
- The component does not animate the content area.
- The component does not provide success or completion effects.
