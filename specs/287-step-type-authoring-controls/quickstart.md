# Quickstart: Add Step Type Authoring Controls

1. Run the focused Create page Step Type tests through the repo wrapper:

   ```bash
   ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx -t "Step Type"
   ```

2. Run the full required unit suite before finalizing when time and environment allow:

   ```bash
   ./tools/test_unit.sh
   ```

3. Manual UI smoke path:
   - Open the Create page.
   - Confirm each step has one `Step Type` selector.
   - Enter shared instructions and Skill-specific values.
   - Change Step Type to Tool.
   - Confirm instructions remain, Skill values are removed, and the discard notice is visible.
   - Confirm Tool, Skill, and Preset panels show only for their selected Step Type.
