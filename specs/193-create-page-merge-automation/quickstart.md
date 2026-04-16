# Quickstart: Create Page Merge Automation

## Focused Test Loop

Prepare frontend dependencies through the standard unit runner if needed, then run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-create.test.tsx
```

Run existing backend merge automation request parsing coverage:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_run_merge_gate_start.py
```

## Manual Browser Check

1. Open `/tasks/new`.
2. Select `Publish Mode` `pr`.
3. Confirm the merge automation option is visible for an ordinary task.
4. Select the option and submit a task draft in a test environment.
5. Confirm the submitted request payload includes `mergeAutomation.enabled=true`, `publishMode=pr`, and `task.publish.mode=pr`.
6. Change `Publish Mode` to `branch` and `none`; confirm the option is hidden or disabled and no merge automation payload is submitted.
7. Select `pr-resolver` or `batch-pr-resolver`; confirm publish mode becomes `none` and merge automation is unavailable.

## Final Verification

Run the full unit suite before completing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Then run `/moonspec-verify` and record the result in `specs/193-create-page-merge-automation/verification.md`.
