# Quickstart: Preview and Download Task Images by Target

## Focused UI Verification

Run:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx
```

Expected:
- Task detail groups image inputs under Objective and step target headings.
- Detail preview failure leaves metadata and MoonMind-owned download links visible.
- Generic artifact rows still support explicit download URLs for non-input artifacts.
- Edit/rerun tests keep unchanged persisted refs unless explicitly removed.

## Full Unit Verification

Run:

```bash
./tools/test_unit.sh
```

Expected:
- Python unit tests pass.
- Frontend Vitest suite passes after dependencies are prepared.
