# Quickstart: Temporal Edit UpdateInputs

## Scope

This feature is a runtime implementation. Completion requires production code changes plus validation tests. Docs-only or spec-only changes do not satisfy the feature.

## Preconditions

- Current branch: `168-temporal-edit-update-inputs`
- Feature flag path remains `features.temporalDashboard.temporalTaskEditing`
- Existing Phase 2 edit draft reconstruction is available
- Initial supported workflow type remains `MoonMind.Run`

## Focused Validation

Run focused frontend tests for the shared edit form and detail success notice:

```bash
./node_modules/.bin/vitest run --config frontend/vite.config.ts \
  frontend/src/entrypoints/task-create.test.tsx \
  frontend/src/entrypoints/task-detail.test.tsx
```

Run TypeScript type checking:

```bash
./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json
```

If backend update contract behavior changes, run the targeted backend tests:

```bash
pytest tests/unit/api/routers/test_executions.py \
  tests/unit/workflows/temporal/test_temporal_service.py \
  tests/unit/workflows/temporal/workflows/test_run_signals_updates.py \
  -q
```

## Final Verification

Run the repository unit wrapper:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Manual Smoke Path

1. Enable `temporalTaskEditing` in the local runtime configuration.
2. Open a supported active `MoonMind.Run` detail page with `actions.canUpdateInputs = true`.
3. Click **Edit** and verify `/tasks/new?editExecutionId=<workflowId>` loads a reconstructed draft.
4. Change supported fields such as instructions, runtime/model/effort, repository, branch, publish mode, or primary skill.
5. Save changes.
6. Verify the request updates the same execution with `UpdateInputs`.
7. Verify successful saves return to `/tasks/<workflowId>?source=temporal` and show outcome-specific copy.
8. Verify artifact-backed edits create a new input artifact reference.
9. Verify stale-state or validation rejection stays on the edit page with a clear error.

## Regression Guard

Search primary edit surfaces for forbidden queue-era fallback:

```bash
rg -n "editJobId|/tasks/queue/new|queue resubmit|queue update" \
  frontend/src/entrypoints/task-create.tsx \
  frontend/src/entrypoints/task-detail.tsx \
  frontend/src/lib/temporalTaskEditing.ts
```

Expected result: no primary Temporal edit flow uses those terms or routes.
