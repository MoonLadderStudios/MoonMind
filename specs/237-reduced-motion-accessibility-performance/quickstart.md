# Quickstart: Reduced Motion, Accessibility, and Performance Guardrails

## Focused Unit and Component Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/components/MaskedConicBorderBeam.test.tsx
```

Expected coverage:

- Active surfaces expose default and custom non-visual execution labels.
- Inactive surfaces omit the component-provided execution label.
- Decorative beam, glow, and companion layers remain hidden from the accessibility tree.
- Auto reduced motion stops orbital animation under `prefers-reduced-motion: reduce`, keeps a static primary cue, and disables glow/companion layers first.
- Minimal reduced motion hides beam/glow/companion layers and brightens the static border ring.
- CSS contract keeps transform-based orbit animation and avoids layout-triggering animated properties.
- Non-goal assertions reject rapid pulse replacement, warning-like red/orange pulse treatments, full-card shimmer, spinner replacement, completion pulse, success burst, and content-area masking.

## Final Unit Suite

```bash
./tools/test_unit.sh
```

Run after focused validation passes.

## Independent Story Verification

Render `MaskedConicBorderBeam` in active, inactive, auto reduced-motion, minimal reduced-motion, default status-label, custom status-label, and suppressed-label states. The story passes when the component remains border-only, provides a non-visual active status cue by default, degrades glow before the primary cue in auto reduced motion, falls back to border-ring-only minimal mode, and keeps all MM-468 non-goals absent.
