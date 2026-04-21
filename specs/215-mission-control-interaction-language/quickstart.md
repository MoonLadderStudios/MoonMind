# Quickstart: Mission Control Shared Interaction Language

## Focused CSS contract and UI behavior tests

```bash
npm run ui:test -- frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result: shared Mission Control tests pass, including MM-427 interaction token/no-lift assertions and task-list behavior regressions.

## Managed-agent wrapper validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/mission-control.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result: Python unit suite and targeted UI tests pass through the project runner.
