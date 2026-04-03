# Quickstart: remove-runtime-globals

1. Install frontend dependencies so the packaged markdown parser is available:
   ```bash
   npm install
   ```
2. Verify the affected entrypoint still typechecks:
   ```bash
   npm run ui:typecheck
   ```
3. Run the focused skills entrypoint test:
   ```bash
   npm run ui:test -- frontend/src/entrypoints/skills.test.tsx
   ```
4. Verify the production bundle and manifest path:
   ```bash
   npm run ui:build:check
   ```
5. Run the repo unit-test gate before finalizing:
   ```bash
   ./tools/test_unit.sh
   ```
