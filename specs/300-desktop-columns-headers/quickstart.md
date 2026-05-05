# Quickstart: Desktop Columns and Compound Headers

1. Run the focused UI tests:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx
```

2. Run the focused API test:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py
```

3. Run the full unit suite before finalizing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

4. Manual browser check:

- Open `/tasks/list`.
- Confirm headers show separate label sort controls and filter controls.
- Open Runtime, Repository, and Status filters from headers.
- Confirm active filter chips reopen their matching popovers.
- Confirm Kind, Workflow Type, Entry, and Started remain absent.
