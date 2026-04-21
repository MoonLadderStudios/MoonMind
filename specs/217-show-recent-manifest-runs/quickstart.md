# Quickstart: Show Recent Manifest Runs

## Focused UI Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx
```

Expected result:

- The Manifests page requests `/api/executions?entry=manifest&limit=200`.
- Recent Runs renders below Run Manifest.
- Manifest rows show run ID/details link, manifest label, action, status with stage detail, started time, duration, and View details action.
- Status, manifest, and search filters update visible rows.
- Empty states point users to the Run Manifest form above.

## Runner-Integrated UI Validation

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx
```

## Full Unit Validation

```bash
./tools/test_unit.sh
```
