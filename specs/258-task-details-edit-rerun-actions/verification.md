# Verification: Task Details Edit and Rerun Actions

**Verdict**: FULLY_IMPLEMENTED

## Coverage

- REQ-001: PASS - `ExecutionActionCapabilityModel` and generated OpenAPI types include `canEditForRerun`.
- REQ-002: PASS - terminal `MoonMind.Run` states expose `canEditForRerun` and `canRerun` when task editing is enabled and the original snapshot exists.
- REQ-003: PASS - executing tasks continue to expose `canUpdateInputs` without `canEditForRerun`.
- REQ-004: PASS - Task Details renders **Edit task** from either `canUpdateInputs` or `canEditForRerun`.
- REQ-005: PASS - edit-for-rerun links target `/tasks/new?rerunExecutionId=:taskExecutionId&mode=edit`.
- REQ-006: PASS - Task Details renders **Rerun** when `canRerun=true`.
- REQ-007: PASS - failed task tests assert **Edit task** and **Rerun** are both visible.

## Evidence

- `./tools/test_unit.sh tests/unit/api/routers/test_executions.py`: PASS, 122 passed.
- `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`: PASS, 3995 Python tests passed, 1 xpassed, 16 subtests passed; 2 Vitest files passed, 270 tests passed.
- `./node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`: PASS.
- `./node_modules/.bin/vitest run --config frontend/vite.config.ts frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx`: PASS, 2 files and 270 tests passed.
- `npm run api:types`: PASS and regenerated `frontend/src/generated/openapi.ts`.
- `git diff --check`: PASS.

## Notes

- `npm run ui:typecheck` and `npm run ui:test -- ...` fail in this environment with shell lookup errors for `tsc`/`vitest`, but the same local binaries succeed when invoked directly from `./node_modules/.bin`.
- `npm run api:types:check` reports the intended generated OpenAPI diff for the new `canEditForRerun` field.
- Implementation-stage reruns on 2026-04-25 confirmed the current code passes the required unit, component integration, type, OpenAPI generation, and diff checks. Red-first failure confirmation could not be replayed without reverting already-committed production code, so T003 and T006 remain unchecked in `tasks.md` rather than fabricating TDD evidence.
