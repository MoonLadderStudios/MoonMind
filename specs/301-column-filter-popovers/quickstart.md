# Quickstart: Column Filter Popovers

## Preconditions

- Use the active feature directory: `specs/301-column-filter-popovers`.
- Preserve `MM-588`, `MM-594`, and their canonical Jira preset briefs in downstream artifacts.
- Run frontend tests with local Node dependencies prepared by `./tools/test_unit.sh` or `npm ci --no-fund --no-audit` when needed.

## Unit Test Strategy

1. Use focused UI unit tests in `frontend/src/entrypoints/tasks-list.test.tsx` for staged Apply, Cancel, Escape/outside dismissal, include/exclude status, blank handling, Skill/date filters, chip remove, and canonical URL encoding.
2. Run focused UI tests:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

3. Run focused Python route-boundary unit tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py
```

4. Before finalizing, run the required full unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

No compose-backed `integration_ci` suite is required for this planning refresh because the mapped story changes only the Tasks List UI and task-scoped `/api/executions` query behavior. Integration coverage is the route-boundary test strategy above: focused API tests verify canonical filter parameters remain constrained to ordinary task scope without external credentials.

## End-to-End Story Check

Render `/tasks/list` with representative task rows and verify:

- Popover edits do not apply until Apply.
- Cancel, Escape, and outside click discard draft edits.
- `Status: not canceled` is represented as exclude mode.
- Runtime values store raw identifiers and display readable labels.
- Repository value selection and legacy exact text behavior both work.
- Scheduled/Finished support bounds and blanks; Created supports bounds without blanks.
- Chips reopen popovers and individual chip removal clears only one filter.
- Clear filters restores the default task-run view.
- Task-scope safety is preserved for all emitted filter requests.
