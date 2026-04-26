# Quickstart: Executing Text Brightening Sweep

## Focused Validation

```bash
npm run ui:typecheck
npm run ui:lint
npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/mission-control.test.tsx
```

If the managed workspace path prevents `npm run` from resolving local binaries, use the equivalent repository-local binaries:

```bash
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx frontend/src/entrypoints/mission-control.test.tsx
```

## Full Unit Validation

```bash
./tools/test_unit.sh
```

## Expected Evidence

- Executing task-list table and card pills have `data-effect="shimmer-sweep"`, `aria-label="executing"`, `.status-letter-wave[aria-hidden="true"]`, and one `.status-letter-wave__glyph` per visible letter.
- Non-executing task-list pills have no executing shimmer metadata and no glyph-wave markup.
- CSS keeps the existing physical sweep and adds `mm-executing-letter-brighten` with the shared duration token, faster active text-sweep ratios, and an inactive tail before the next cycle.
- Reduced-motion CSS disables glyph animation, text shadow, and filter.
