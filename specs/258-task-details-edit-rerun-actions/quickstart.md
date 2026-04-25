# Quickstart: Task Details Edit and Rerun Actions

1. Run API tests:
   `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
2. Run frontend focused tests:
   `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
3. Run TypeScript verification:
   `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`
4. Regenerate OpenAPI frontend types:
   `npm run api:types`
5. Integration strategy:
   No new `integration_ci` command is required for this story because no workflow, activity, persistence, or compose-backed service boundary changes. If a future fixture exercises failed execution details end to end, run `./tools/test_integration.sh` after adding that fixture.
6. Manual end-to-end check:
   Open a failed `MoonMind.Run` Task Details page with an original input snapshot and confirm **Edit task** and **Rerun** are both shown.
