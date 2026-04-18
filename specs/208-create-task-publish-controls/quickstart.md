# Quickstart: Create Task Publish Controls

## Focused Test Command

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

## Final Unit Command

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Test-First Validation Scenarios

1. Render the Create page and confirm GitHub Repo, Branch, and Publish Mode are in the Steps card control group.
2. Confirm Execution context has no standalone `Enable merge automation` checkbox.
3. Confirm ordinary PR-publishing tasks expose a Publish Mode option for PR with Merge Automation.
4. Select None, Branch, PR, and PR with Merge Automation and submit each draft; inspect request payload mapping.
5. Select PR with Merge Automation, then select a direct resolver skill; confirm the combined merge choice is unavailable or cleared and submission omits merge automation.
6. Hydrate edit/rerun snapshots with None, Branch, PR, and PR plus merge automation; confirm the visible Publish Mode selection matches stored state.
7. Confirm Branch and Publish Mode controls expose accessible names.
8. Confirm `docs/UI/CreatePage.md` describes merge automation as a Publish Mode choice rather than a standalone Execution context checkbox.

## Expected End State

- No backend publish enum changes.
- No worker contract changes.
- Existing payload shape is preserved.
- MM-412 remains traceable through spec, plan, tasks, verification, commits, and PR metadata.
