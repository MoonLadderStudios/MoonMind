# Quickstart: Task-only Visibility and Diagnostics Boundary

## Focused Backend Validation

```bash
pytest tests/unit/api/test_executions_temporal.py -q
```

Expected evidence:
- Default source-temporal listing queries task runs.
- `scope=all`, `scope=system`, system `workflowType`, and `entry=manifest` fail safe to task-run query semantics for ordinary users.
- Unknown scope values still return validation errors.

## Focused Frontend Validation

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx
```

Expected evidence:
- The normal page request remains task scoped.
- `Scope`, `Workflow Type`, and `Entry` controls are absent.
- Status and Repository filters remain usable.
- Old broad workflow URL parameters are removed from emitted URL state and show a recoverable notice.
- `Kind`, `Workflow Type`, and `Entry` table headers are absent.

## Final Unit Wrapper

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

## End-to-End Story Check

Open `/tasks/list?scope=all&workflowType=MoonMind.ProviderProfileManager&entry=manifest&state=completed&repo=moon%2Fdemo`. The page should show a recoverable notice, preserve the Status and Repository task filters, request `/api/executions` with task scope only plus preserved task filters, and rewrite the browser URL without `scope`, `workflowType`, or `entry`.
