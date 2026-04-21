# Quickstart: Run Manifest Page Form

## Targeted Validation

1. Run frontend tests:

   ```bash
   ./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/manifests.test.tsx
   ```

2. Run through the repository test wrapper:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/manifests.test.tsx
   ```

3. Final validation:

   ```bash
   ./tools/test_unit.sh
   ```

4. Final MoonSpec verification:

   ```text
   /moonspec-verify
   ```

## Manual Scenario

1. Open `/tasks/manifests`.
2. Confirm the `Run Manifest` form appears above `Recent Runs`.
3. Confirm `Advanced options` is collapsed and `Run Manifest` is visible.
4. Submit Registry Manifest mode with a valid name and action; confirm no inline YAML is required.
5. Submit Inline YAML mode with a valid name, YAML content, and action; confirm recent runs refresh in place.
6. Enter `0`, `-1`, `1.5`, and `abc` in Max Docs; confirm each is rejected before submit.
7. Enter a raw secret-shaped value such as a token assignment in Inline YAML; confirm it is rejected before submit and guidance points to env/Vault references.
8. Enter an env/Vault-style reference in Inline YAML; confirm the UI does not reject it as a raw secret-shaped value.
