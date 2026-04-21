# Quickstart: Surface Hierarchy and liquidGL Fallback Contract

## Focused Validation

1. Run the focused Mission Control UI tests:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
   ```

2. Verify the shared stylesheet contains the MM-425 surface hierarchy:

   ```bash
   rg -n "panel--satin|panel--floating|panel--utility|surface--liquidgl-hero|surface--accent-live|surface--nested-dense" frontend/src/styles/mission-control.css
   ```

3. Run the full unit suite before final completion:

   ```bash
   ./tools/test_unit.sh
   ```

## Expected Result

- All five surface roles are represented by stable selectors.
- Glass control surfaces have token-driven CSS glass and near-opaque fallback.
- liquidGL remains opt-in on bounded hero targets.
- Task-list data slabs and Create page controls keep existing behavior.
- MM-425 remains preserved in MoonSpec artifacts and verification evidence.
