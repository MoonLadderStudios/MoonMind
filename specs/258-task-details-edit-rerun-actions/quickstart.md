# Quickstart: Task Details Edit and Rerun Actions

1. Run API tests:
   `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`
2. Run frontend focused tests:
   `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`
3. Open a failed `MoonMind.Run` Task Details page with an original input snapshot and confirm **Edit task** and **Rerun** are both shown.
