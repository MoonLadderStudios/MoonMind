# Quickstart: Jira Provenance Polish

## Prerequisites

- Frontend dependencies are installed. If needed, run the repo unit wrapper so it can prepare them automatically.
- Jira Create page runtime config is available in the test fixture or local runtime.

## Focused Validation

1. Run the focused Create page test suite:

   ```bash
   ./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
   ```

2. Run TypeScript typecheck:

   ```bash
   ./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
   ```

3. Run the repo unit wrapper before finalizing:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
   ```

## Manual Smoke Path

1. Open the Create page with Jira UI enabled.
2. Open the Jira browser from Feature Request / Initial Instructions.
3. Select a project, board, column, and issue.
4. Replace or append imported text into the preset target.
5. Confirm a compact `Jira: <issue key>` chip appears near the preset field.
6. Open the Jira browser from a step Instructions field and import the same or another issue.
7. Confirm only the targeted step shows its `Jira: <issue key>` chip.
8. Manually edit the preset or step text and confirm the corresponding stale chip disappears.
9. Refresh the page in the same browser session and confirm the last project/board restore only when session memory is enabled.
10. Submit a task draft and confirm the payload shape remains unchanged except for the imported instruction text.

## Failure Checks

- Disable Jira UI runtime config and confirm no Jira controls or provenance chips appear.
- Simulate unavailable browser session storage and confirm Jira browsing/manual task creation still work.
- Simulate Jira browser fetch failures and confirm manual task editing and submission remain available.
