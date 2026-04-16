# Quickstart: Merge Automation Visibility

Run focused workflow and UI checks:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py \
  tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py \
  --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Expected result:

- Merge automation summary projection tests pass.
- Merge automation artifact write tests pass.
- Task detail renders merge automation state from run summary payloads.

Run full unit verification before final MoonSpec verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Then run final MoonSpec verification:

```bash
/moonspec-verify
```
