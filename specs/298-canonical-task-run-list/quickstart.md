# Quickstart: Canonical Task Run List Route

## Prerequisites

- Python 3.12 environment with repository dependencies installed.
- Node/npm dependencies prepared by `./tools/test_unit.sh` or `npm ci --no-fund --no-audit`.
- No external provider credentials are required for the planned unit/UI verification.

## Test-First Flow

1. Add failing router tests for task-list boot payload identity:

```bash
pytest tests/unit/api/routers/test_task_dashboard.py -q
```

Expected initial result: tests for exact `tasks-list` page key, wide layout, initial path, and dashboard config fail until implementation is adjusted or verified.

2. Add failing Tasks List UI tests for ordinary list safety:

```bash
./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

Required scenarios:
- Default list fetch uses task-run scope.
- `scope=system`, `scope=all`, system `workflowType`, and `entry=manifest` do not produce ordinary broad workflow fetches.
- Scope, Workflow Type, and Entry broad-workflow controls are absent from the ordinary page.
- Browser list loading calls `/api/executions` only.
- Table/card output exposes no ordinary `Kind`, `Workflow Type`, or `Entry` browsing affordance.

3. Add failing API boundary tests for ordinary task visibility:

```bash
pytest tests/unit/api/routers/test_executions.py -q
```

Required scenarios:
- Task-scope list query remains bounded to `MoonMind.Run` task-run entries.
- Broad params from ordinary compatibility handling cannot widen visibility.
- Non-admin owner scoping remains enforced.

4. Add failing hermetic integration coverage for mixed task/system/manifest data and broad compatibility URLs:

```bash
pytest tests/integration/api/test_tasks_list_visibility.py -q
```

Required scenarios:
- Mixed ordinary task, system workflow, and manifest workflow data yields zero system or manifest rows in the ordinary task table.
- At least four broad compatibility URL cases fail safe without ordinary broad workflow exposure.

5. Implement the smallest code changes needed to pass the targeted tests.

6. Run the full unit suite before finalizing:

```bash
./tools/test_unit.sh
```

7. Run the required hermetic integration suite when the new integration test is marked `integration_ci` or when implementation changes the API/Temporal list boundary:

```bash
./tools/test_integration.sh
```

## 2026-05-05 Verification Notes

- Targeted router/API unit coverage passed: `pytest tests/unit/api/routers/test_task_dashboard.py tests/unit/api/routers/test_executions.py -q`.
- Targeted UI coverage passed through the repo runner: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --dashboard-only --ui-args frontend/src/entrypoints/tasks-list.test.tsx`.
- Targeted hermetic integration coverage passed: `pytest tests/integration/api/test_tasks_list_visibility.py -q`.
- Full `./tools/test_integration.sh` was attempted but blocked in the managed agent container because `/var/run/docker.sock` is unavailable.
- Full `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` was attempted but blocked by the active `.agents/skills` projection missing unrelated PR resolver skill files required by existing tests.

## End-to-End Validation

Manual or browser-smoke validation after tests pass:

1. Open `/tasks/list`.
2. Confirm the page is the task-oriented Tasks List.
3. Open `/tasks` and `/tasks/tasks-list`; confirm both land on `/tasks/list`.
4. Open broad compatibility URLs such as `/tasks/list?scope=system`, `/tasks/list?scope=all`, `/tasks/list?workflowType=MoonMind.ProviderProfileManager`, and `/tasks/list?entry=manifest`.
5. Confirm ordinary users do not see system or manifest rows in the task table.
6. Confirm broad workflow diagnostics, when available, are separate and permission-gated.

## Final Evidence

Final verification should report:
- THOR-370 preserved in artifacts.
- Unit and UI command results.
- Integration command result, including targeted `tests/integration/api/test_tasks_list_visibility.py` and `./tools/test_integration.sh` when applicable.
- Compatibility URL cases covered.
- Count of system workflow rows visible in ordinary `/tasks/list`: `0`.
