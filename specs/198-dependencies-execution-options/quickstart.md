# Quickstart: Dependencies and Execution Options

## Focused UI Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Expected:
- Create page tests pass.
- Dependency fetch failure preserves draft state and does not block valid manual task creation.
- Duplicate dependencies are rejected.
- No more than 10 direct dependencies can be selected.
- Runtime-specific provider-profile options update when runtime changes.
- Merge automation submits only for ordinary PR-publishing tasks.
- Resolver-style direct tasks do not submit enabled merge automation.
- Jira import and image upload flows do not weaken repository, publish, runtime, or dependency validation.

## Full Unit Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected:
- Python unit tests and frontend unit tests pass through the repository runner.

## Manual Story Check

1. Open `/tasks/new`.
2. Confirm dependency options can be loaded from recent `MoonMind.Run` executions.
3. Add one dependency, attempt to add it again, and confirm it is rejected.
4. Add dependencies until the direct dependency cap is reached and confirm an eleventh entry is rejected.
5. Change runtime and confirm provider-profile options update to the selected runtime.
6. Select `Publish Mode` `pr`, enable merge automation, and submit a valid ordinary task.
7. Confirm the create payload preserves `publishMode=pr`, `task.publish.mode=pr`, and `mergeAutomation.enabled=true`.
8. Switch publish mode to `branch` or `none`, and confirm merge automation is unavailable and omitted.
9. Select `pr-resolver` or `batch-pr-resolver`, and confirm direct resolver task creation omits enabled merge automation.
10. Import Jira content or upload an image and confirm repository, publish, runtime, and dependency validation still run.
