# Quickstart: Jira Preset Reapply Signaling

## Purpose

Validate that Jira import into applied preset instructions is explicit and non-destructive, and that Jira import into template-bound steps warns before manual customization.

## Prerequisites

- Frontend dependencies installed from `package-lock.json`.
- Jira Create page integration enabled in the runtime config used by the test fixture.
- Existing Create page test mocks for presets, Jira board browsing, issue preview, and issue import.

## Focused Validation

1. Run the Create page test file:

   ```bash
   ./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
   ```

2. Run frontend type checking:

   ```bash
   ./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
   ```

3. Run the dashboard-only unit wrapper for the targeted Create page test:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/task-create.test.tsx
   ```

4. Run the full unit wrapper without xdist if parallel collection hits local static-directory races:

   ```bash
   MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --no-xdist
   ```

## Manual Smoke Path

1. Open the Create page with Jira integration enabled.
2. Select and apply a preset that expands into multiple steps.
3. Open the Jira browser from `Feature Request / Initial Instructions`.
4. Select a Jira issue and replace the preset instructions text.
5. Confirm the message says: `Preset instructions changed. Reapply the preset to regenerate preset-derived steps.`
6. Confirm the expanded steps are still unchanged.
7. Confirm the preset action is clearly presented as a reapply action while the message is visible.
8. Restore the preset instructions to the last applied value and confirm the reapply-needed message clears.
9. Open the Jira browser from a still-template-bound preset step.
10. Confirm the browser warns that the step will become manually customized.
11. Import Jira text into that step and confirm only that step changes.
12. Submit or inspect the draft and confirm the edited step no longer carries template-bound instruction identity.

## Expected Results

- Jira import into applied preset instructions never silently rewrites already-expanded steps.
- Reapply-needed messaging appears only when the applied preset instructions actually change.
- The reapply-needed state is non-blocking.
- Template-bound step warning appears only for steps still bound by instruction identity.
- Jira import into a template-bound step is still allowed.
- Step import updates only the targeted step and detaches template instruction identity when instructions diverge.
- Jira failures remain local to the browser and manual task creation remains available.
- Submitted task payload shape remains unchanged.
