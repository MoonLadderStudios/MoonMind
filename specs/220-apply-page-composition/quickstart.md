# Quickstart: MM-428 Page-Specific Task Workflow Composition

## Focused Test-First Commands

1. Add failing route-composition tests:

```bash
npm run ui:test -- frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

If npm cannot resolve `vitest`, use:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

2. Implement the smallest markup/CSS changes needed for failing MM-428 assertions.

3. Run targeted validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/tasks-list.test.tsx
```

4. Run final verification and record results in `verification.md`.

## Expected End State

- `/tasks/list` keeps MM-426 control deck + data slab behavior.
- `/tasks/new` keeps matte/satin step authoring and one bottom launch rail.
- Task detail/evidence pages expose readable separated summary, facts, steps, evidence/log, and action regions.
- MM-428 and all source design IDs are preserved in final evidence.
