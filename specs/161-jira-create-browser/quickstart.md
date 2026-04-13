# Quickstart: Jira Create Browser

## Prerequisites

- Repository dependencies are installed.
- Runtime config exposes Jira Create page browser sources when the Jira UI feature flag is enabled.
- Browser clients call only MoonMind-owned `/api/jira/...` endpoints.

## Focused Frontend Validation

Run the Create page test file:

```bash
node node_modules/vitest/vitest.mjs run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
```

Expected result:

- Jira browser controls are hidden when the feature is disabled.
- The browser opens from preset instructions.
- The browser opens from step instructions.
- Board columns render in order.
- Column switching changes visible issues.
- Issue selection loads normalized preview text.

## Type and Lint Validation

Run:

```bash
node node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
node node_modules/eslint/bin/eslint.js -c frontend/eslint.config.mjs frontend/src
```

Expected result: both commands complete without errors.

## Runtime Config Validation

Run the runtime config tests:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_task_dashboard_view_model.py
```

Expected result:

- Jira browser config is absent when disabled.
- Trusted Jira tooling enablement alone does not expose the Create page browser.
- Jira browser sources and default project or board values are present when the feature is enabled.

## Final Unit Verification

Run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: required Python unit tests and frontend unit tests pass.
