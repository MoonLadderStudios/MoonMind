# Quickstart: MaskedConicBorderBeam Border-Only Contract

## Focused Red-First Command

```bash
npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx
```

Expected before implementation: tests fail because the component does not exist.

## Implementation Verification

```bash
npm run ui:test -- frontend/src/components/MaskedConicBorderBeam.test.tsx
```

Expected after implementation: focused component and CSS contract tests pass.

## Final Verification

```bash
./tools/test_unit.sh
```

Expected: full required unit suite passes, including UI tests prepared by the repo runner.
