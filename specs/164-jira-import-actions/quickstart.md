# Quickstart: Jira Import Actions

## Purpose

Validate that Jira issue text can be explicitly imported into Create page preset objective text or step instructions without changing task submission semantics.

## Prerequisites

- Frontend dependencies installed from `package-lock.json`.
- Jira Create page integration enabled in the runtime config used by the test fixture.
- Existing Jira browser mock responses for projects, boards, columns, issue lists, and issue detail.

## Focused Validation

1. Run the Create page test file:

   ```bash
   ./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
   ```

2. Run frontend type checking:

   ```bash
   ./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
   ```

3. Run frontend lint for the changed files:

   ```bash
   ./node_modules/.bin/eslint -c frontend/eslint.config.mjs frontend/src/entrypoints/task-create.tsx frontend/src/entrypoints/task-create.test.tsx
   ```

4. Run the repo unit wrapper with local test mode and targeted UI args:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
   ```

## Manual Smoke Path

1. Open the Create page with Jira integration enabled.
2. Open the Jira browser from Feature Request / Initial Instructions.
3. Select a board column and issue.
4. Confirm selecting the issue does not change the draft.
5. Choose Preset brief and Replace target text.
6. Confirm the preset objective field changes and the primary step remains unchanged.
7. Reopen the browser from a secondary step.
8. Choose Execution brief and Replace target text.
9. Confirm only that step changes.
10. Apply a preset, import into the preset objective, and confirm the page reports that reapply is needed without rewriting expanded steps.

## Expected Results

- Issue preview remains read-only until Replace or Append is clicked.
- Replace writes only the selected import text.
- Append preserves existing text and inserts a clear separator before imported text.
- Preset objective imports preserve objective precedence.
- Step imports update only the selected step.
- Template-bound step imports detach template identity when instructions diverge.
- Jira failures remain local to the browser and manual task creation still works.
- Submitted task payload shape remains unchanged.
