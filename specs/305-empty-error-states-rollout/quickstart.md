# Quickstart: Empty/Error States and Regression Coverage for Final Rollout

## Focused UI Validation

```bash
node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/tasks-list.test.tsx
```

Expected result: Tasks List UI tests pass, including MM-592 loading, structured API error, empty first-page recovery, empty later-page recovery, facet fallback, invalid-filter recovery, old-control absence, and non-goal safety coverage.

## Full Unit Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full required unit suite passes. If this managed active-skill snapshot still blocks unrelated skill-file tests, record the exact blocker in `verification.md` and keep focused UI evidence.

## Story Review

1. Confirm `spec.md`, `plan.md`, `tasks.md`, and `verification.md` preserve `MM-592`.
2. Confirm source IDs DESIGN-REQ-006, DESIGN-REQ-024, DESIGN-REQ-026, DESIGN-REQ-027, and DESIGN-REQ-028 are preserved.
3. Confirm `/tasks/list` has no old top Scope, Workflow Type, Status, Entry, or Repository controls.
4. Confirm the final rollout does not add non-goal spreadsheet, pivot, raw Temporal query, direct Temporal browser calls, saved views, pagination replacement, Live updates removal, or system workflow browsing behavior.
