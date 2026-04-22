# Data Model: Reduced Motion, Accessibility, and Performance Guardrails

This story does not introduce persistent data. The relevant runtime entities are component-level configuration values and rendered accessibility/state markers.

## MaskedConicBorderBeam Props

- `active`: boolean execution-state decoration flag.
- `reducedMotion`: `auto`, `off`, or `minimal`.
- `glow`: `off`, `low`, or `medium`; optional visual layer that must degrade before the primary active cue.
- `variant`: `precision`, `energized`, or `dualPhase`; dual-phase can add a companion layer that must degrade before the primary active cue.
- `statusLabel`: string, null, or undefined; defaults to `Executing` while active, can be customized, and can be suppressed with null.

## Rendered State

- Active default: primary beam, optional glow, content wrapper, and hidden status label.
- Active auto reduced motion with reduced user preference: primary static illuminated border segment remains; glow and companion layers are hidden.
- Active minimal reduced motion: primary beam, glow, and companion layers are hidden; static border ring is brightened.
- Inactive: visual active layers are hidden by existing inactive rules and the hidden execution label is omitted.

## Validation Rules

- `statusLabel` defaults to `Executing`.
- `statusLabel={null}` suppresses the component-provided label for callers that provide an equivalent accessible cue elsewhere.
- Decorative beam, glow, and companion layers remain `aria-hidden`.
- Reduced-motion modes must not introduce rapid pulse replacement behavior.
- Performance degradation disables glow before removing the primary active-state cue.

## State Transitions

- `active=false` to `active=true`: hidden status label appears and active visual layers follow existing enter transitions.
- `reducedMotion=auto` plus reduced user preference: orbit animation stops through CSS media query.
- `reducedMotion=minimal`: movement is disabled and active state falls back to a static border ring only.
