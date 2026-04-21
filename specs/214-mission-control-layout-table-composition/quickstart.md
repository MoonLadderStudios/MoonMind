# Quickstart: Mission Control Layout and Table Composition Patterns

## Focused UI Verification

```bash
npm run ui:test -- frontend/src/entrypoints/tasks-list.test.tsx
```

If the managed container cannot resolve the npm script binary, run the equivalent direct command:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result:

- task-list control deck and data slab structure is present,
- active filter chips render and clear,
- table wrapper scroll and sticky header posture is covered,
- existing task-list behavior tests still pass.

For masthead source-design coverage, run:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/mission-control.test.tsx
```

Expected result: the desktop masthead keeps brand content left, navigation visually centered, and version/utility metadata aligned right.

## Final Unit Wrapper

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result: Python unit suite and targeted UI tests pass through the canonical wrapper.

## Integration Strategy

No compose-backed integration test is required for MM-426 because backend contracts, persistence, and Temporal behavior are unchanged. The integration-style validation is the task-list browser component boundary in `frontend/src/entrypoints/tasks-list.test.tsx`, which exercises rendered filters, request/query shape, routing links, pagination, mobile cards, active-filter chips, and table structure together.

## Manual Inspection

1. Open `/tasks/list`.
2. Confirm filters and live updates are in the upper control deck.
3. Confirm page summary, page size, pagination, desktop table, and mobile cards are grouped in the result slab.
4. Apply a status or repository filter and confirm chips appear.
5. Clear filters and confirm values reset without navigation or route changes.
