# Quickstart: Masked Conic Beam Geometry and Layers

## Focused Test Loop

Prepare frontend dependencies if needed, then run:

```bash
npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx
```

Expected TDD sequence:
1. Add MM-466 tests for geometry variables, border-ring mask inset/radius, beam footprint, glow footprint, trail speed invariance, content preservation, and traceability.
2. Confirm the focused test fails before production changes.
3. Update `MaskedConicBorderBeam.tsx` and `mission-control.css`.
4. Re-run the focused test until it passes.

## Final Unit Verification

```bash
./tools/test_unit.sh
```

## Story Verification

Verify the single story end to end by confirming:
- active rendering includes static border, beam layer, optional glow layer, and content wrapper
- border-ring masking derives the inner inset from borderWidth and inner radius from radius minus width
- the default beam footprint exposes 12deg head and 28deg tail values
- conic gradients preserve transparent majority, tail, head, and fade back to transparency
- glow remains lower-opacity, blurred, decorative, and separate from content
- trail variants do not change orbital speed
- MM-466 appears in spec, plan, tasks, traceability, and verification evidence
