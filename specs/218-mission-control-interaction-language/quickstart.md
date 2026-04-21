# Quickstart: Mission Control Shared Interaction Language

## Unit Strategy - Focused CSS Contract Tests

```bash
npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result: shared Mission Control tests pass, including MM-427 interaction token/no-lift assertions and task-list behavior regressions.

## Integration Strategy - Existing UI Render Regression

The story does not change backend contracts or persistent data, so no compose-backed integration test is required. Existing Mission Control app-shell and task-list render tests serve as the integration-style regression for route behavior, filters, sorting, pagination, and mobile card rendering.

## Managed-Agent Wrapper Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result: Python unit suite and targeted UI tests pass through the project runner.
