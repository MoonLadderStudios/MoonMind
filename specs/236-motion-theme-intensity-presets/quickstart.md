# Quickstart: Motion, Theme, and Intensity Presets

## Focused validation

```bash
npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx
```

## Full unit validation

```bash
./tools/test_unit.sh
```

## Story validation

1. Add MM-467 tests for defaults, speed mapping, direction, transitions, theme/intensity tokens, variants, custom theme pass-through, and border-only preservation.
2. Confirm the focused tests fail before production changes.
3. Update `MaskedConicBorderBeam.tsx` and `mission-control.css`.
4. Rerun the focused UI test.
5. Run the full unit suite.
6. Create final `/moonspec-verify` evidence in `verification.md`.
