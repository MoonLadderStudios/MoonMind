# Contract: MaskedConicBorderBeam Guardrails

The frontend continues to export `MaskedConicBorderBeam`, `MaskedConicBorderBeamProps`, and `MASKED_CONIC_BORDER_BEAM_TRACEABILITY` from `frontend/src/components/MaskedConicBorderBeam.tsx`.

## Public Props

`MaskedConicBorderBeamProps` includes the existing public props and adds:

- `statusLabel?: string | null`

## Accessibility Contract

- Active surfaces render a visually hidden status label.
- The default active status label is `Executing`.
- Callers can pass a custom status label.
- Callers can pass `statusLabel={null}` only when they provide an equivalent accessible cue elsewhere.
- The active status label is not rendered when `active=false`.
- Beam, glow, and companion layers remain `aria-hidden="true"`.

## Reduced Motion Contract

- `reducedMotion="off"` keeps normal orbit behavior.
- `reducedMotion="auto"` uses `@media (prefers-reduced-motion: reduce)` to stop orbital animation.
- Auto reduced motion keeps the primary layer as a static illuminated border segment and disables glow/companion layers first.
- `reducedMotion="minimal"` disables movement and hides beam/glow/companion layers so only the brighter static border ring communicates active state.
- Reduced-motion behavior must not introduce rapid pulse replacement behavior.

## Performance Contract

- Normal orbit animation uses `masked-conic-border-beam-orbit var(--beam-speed) linear infinite`.
- `@keyframes masked-conic-border-beam-orbit` animates transform/rotation.
- Border-beam animation rules do not animate layout-triggering properties such as width, height, margin, padding, top, right, bottom, left, inset, border-width, or border-radius.
- Glow remains modest in normal mode and is disabled before the primary cue in reduced/degraded modes.

## Non-Goal Guardrails

- The border-beam contract excludes rapid pulse replacement, strong red/orange warning pulse treatments, full-card shimmer, background fill, spinner replacement, completion pulse, success burst, and content-area masking.
