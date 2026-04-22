# Data Model: MaskedConicBorderBeam Border-Only Contract

## MaskedConicBorderBeam Surface

- `active`: boolean; defaults to `true`.
- `borderRadius`: CSS length or number interpreted as pixels; defaults to `16px`.
- `borderWidth`: CSS length or number interpreted as pixels; defaults to `1.5px`.
- `speed`: `slow`, `medium`, `fast`, CSS time string, or numeric seconds; defaults to `medium`.
- `intensity`: `subtle`, `normal`, or `vivid`; defaults to `normal`.
- `theme`: `neutral`, `brand`, `success`, or `custom`; defaults to `neutral`.
- `direction`: `clockwise` or `counterclockwise`; defaults to `clockwise`.
- `trail`: `none`, `soft`, or `defined`; defaults to `soft`.
- `glow`: `off`, `low`, or `medium`; defaults to `low`.
- `reducedMotion`: `auto`, `off`, or `minimal`; defaults to `auto`.
- `children`: arbitrary host content.

## State Transitions

- inactive -> active: beam and optional glow layers appear and CSS controls entry opacity.
- active -> inactive: moving beam and glow layers are removed; static host border may remain.
- auto motion -> user prefers reduced motion: CSS stops orbital animation and leaves static active illumination.
- any motion -> minimal: component marks minimal mode and CSS stops animation.

## Validation Rules

- Numeric radius and width values are converted to pixel CSS variables.
- Numeric speed values are converted to seconds.
- Beam and glow layers are `aria-hidden`.
- Content remains in a dedicated content wrapper outside the animated layers.
