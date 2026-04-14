# Quickstart: Jira UI Test Coverage

## Prerequisites

- Python test dependencies available in the MoonMind managed agent environment.
- Node/npm dependencies prepared by `./tools/test_unit.sh` or `npm ci`.
- No Jira credentials are required for the required validation path.

## Focused Backend Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 pytest \
  tests/unit/api/routers/test_jira_browser.py \
  tests/unit/integrations/test_jira_browser_service.py \
  tests/unit/api/routers/test_task_dashboard_view_model.py \
  -q
```

Expected result: Jira browser router, Jira service normalization, policy/redaction, and runtime config tests pass.

## Focused Frontend Validation

Use the repo wrapper when frontend dependencies may be missing or stale:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

For a faster direct rerun after dependencies are prepared:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx
```

Expected result: Create page tests cover Jira disabled controls, browser open targets, board columns, issue preview, imports, template detachment, preset reapply signaling, provenance, failure isolation, and unchanged submission shape.

## Final Required Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: the full unit suite passes. Existing unrelated deprecation warnings may appear but should not fail the run.

## Manual Review Checklist

- Confirm tests do not require live Jira credentials.
- Confirm no raw credentials or secret-like values are checked into fixtures.
- Confirm Jira failures are asserted local to the browser.
- Confirm submitted task payload assertions exclude Jira-specific required fields.
