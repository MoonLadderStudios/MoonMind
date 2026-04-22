# UI Contract: Mission Control Accessibility, Performance, and Fallback Posture

## Scope

This contract describes the observable UI resilience required by MM-429. It does not change backend APIs, task submission payloads, Temporal workflows, or Jira integration behavior.

## Contrast And Focus

- Representative labels, table text, placeholder text, chips, buttons, focus states, and glass-over-gradient surfaces use readable foreground/background token pairs.
- Buttons, button-like links, inputs, selects, textareas, icon buttons, table sort controls, task controls, live-log links, attachment controls, and task-detail toggles expose visible `:focus-visible` or focus-within treatment.
- Forced-colors mode preserves explicit outlines for representative focusable controls.

## Reduced Motion

- `@media (prefers-reduced-motion: reduce)` removes or significantly softens nonessential motion for routine controls.
- Running/live pulse effects, shimmer/scanner/highlight-drift-style effects, and premium surface animation do not keep continuous motion in reduced-motion mode.
- State remains visible through color, border, text, or static iconography when motion is suppressed.

## Backdrop-Filter Fallback

- Glass controls, control panels, floating panels, utility panels, liquid hero surfaces, and floating bars have a `@supports not ((backdrop-filter...) ...)` fallback.
- Fallback surfaces use near-opaque token-based panel backgrounds, borders, and shadows rather than becoming transparent or low-contrast.

## liquidGL Fallback

- liquidGL target surfaces are progressive enhancements over complete CSS shells.
- When liquidGL is unavailable, disabled, or not initialized, queue floating controls keep layout, border, shadow, contrast, focus, and accessible controls.
- Initialized liquidGL state may adjust enhancement-specific styling only after the base shell exists.

## Performance Posture

- Heavy blur, glow, sticky glass, and liquidGL are limited to strategic elevated surfaces.
- Dense reading, table, form, evidence, log, and editing regions do not use liquidGL or competing premium effects.
- Existing task-list, create, navigation, filtering, pagination, and task-detail/evidence behavior remains unchanged.
