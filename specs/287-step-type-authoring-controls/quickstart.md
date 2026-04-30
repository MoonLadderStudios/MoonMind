# Quickstart: Add Step Type Authoring Controls

1. Run TypeScript validation for the Create page state and test harness:

   ```bash
   ./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
   ```

2. Run the focused executable Create page Step Type regression through the repo wrapper:

   ```bash
   ./tools/test_unit.sh --dashboard-only --ui-args entrypoints/task-create-step-type.test.tsx
   ```

3. Run the dashboard regression suite:

   ```bash
   ./tools/test_unit.sh --dashboard-only
   ```

4. Run the broader required unit suite before finalizing when time and environment allow:

   ```bash
   ./tools/test_unit.sh
   ```

5. Manual UI smoke path:
   - Open the Create page.
   - Confirm each step has one `Step Type` selector.
   - Enter shared instructions and Skill-specific values.
   - Change Step Type to Tool.
   - Confirm instructions remain, Skill values are removed, and the discard notice is visible.
   - Confirm Tool, Skill, and Preset panels show only for their selected Step Type.
