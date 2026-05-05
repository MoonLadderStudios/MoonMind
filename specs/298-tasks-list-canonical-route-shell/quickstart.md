# Quickstart: Tasks List Canonical Route and Shell

## Focused Backend Route Validation

```bash
pytest tests/unit/api/routers/test_task_dashboard.py -q
```

Expected evidence:

- `/tasks/list` renders the Mission Control React shell.
- `/tasks` redirects to `/tasks/list`.
- `/tasks/tasks-list` redirects to `/tasks/list`.
- `/tasks/list` boot payload includes dashboard configuration and `dataWidePanel:true`.

## Focused Frontend Shell Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx
```

Expected evidence:

- The Tasks List entrypoint renders one control deck and one data slab.
- Live updates, polling copy, disabled notice, page-size, and pagination surfaces remain available.
- Data requests use the boot payload API base and MoonMind `/api/executions` route.

Developer note: `npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx` is the intended npm wrapper when the shell resolves local binaries. In this managed shell, the direct local Vitest binary is the reliable focused command; the final unit wrapper below also prepares frontend dependencies and runs the same focused UI file.

## Final Unit Wrapper

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

Expected evidence:

- Required Python unit suite passes.
- Focused Tasks List UI suite passes through the repository test wrapper.
