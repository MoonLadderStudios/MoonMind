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
npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx
```

Expected evidence:

- The Tasks List entrypoint renders one control deck and one data slab.
- Live updates, polling copy, disabled notice, page-size, and pagination surfaces remain available.
- Data requests use the boot payload API base and MoonMind `/api/executions` route.

## Final Unit Wrapper

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

Expected evidence:

- Required Python unit suite passes.
- Focused Tasks List UI suite passes through the repository test wrapper.
