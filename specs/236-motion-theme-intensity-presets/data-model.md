# Data Model: Motion, Theme, and Intensity Presets

This story does not introduce persistent data. The relevant runtime entities are component-level configuration values.

## MaskedConicBorderBeam Props

- `active`: boolean execution-state decoration flag.
- `speed`: `slow`, `medium`, `fast`, numeric seconds, seconds string, or milliseconds string.
- `direction`: `clockwise` or `counterclockwise`.
- `theme`: `neutral`, `brand`, `success`, or `custom`.
- `intensity`: `subtle`, `normal`, or `vivid`.
- `trail`: `none`, `soft`, or `defined`.
- `glow`: `off`, `low`, or `medium`.
- `variant`: `precision`, `energized`, or `dualPhase`.
- `reducedMotion`: `auto`, `off`, or `minimal`.

## Validation Rules

- Named speeds resolve exactly to `slow = 4.8s`, `medium = 3.6s`, and `fast = 2.8s`.
- Numeric speeds resolve to seconds.
- Duration strings pass through unchanged.
- Variant defaults to `precision`.
- `custom` theme relies on caller-provided CSS custom properties through the component style boundary.

## State Transitions

- `active=false`: moving beam and glow layers do not render.
- `active=true`: beam layer renders; glow layer renders when `glow` is not `off`.
- Active visual layer opacity transitions use CSS timing tokens while the orbit animation remains linear.

