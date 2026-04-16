# Quickstart: Merge Outcome Propagation

## Focused Unit Validation

Run parent and merge automation focused tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/workflows/temporal/test_run_parent_owned_merge_automation.py \
  tests/unit/workflows/temporal/workflows/test_run_parent_owned_merge_automation_boundary.py \
  tests/unit/workflows/temporal/workflows/test_merge_automation_temporal.py \
  tests/unit/workflows/temporal/workflows/test_run_dependency_signals.py
```

Expected coverage:
- Parent success for `merged` and `already_merged`.
- Parent failure for `blocked`, `failed`, `expired`, missing status, and unsupported status.
- Parent cancellation for `canceled`.
- Parent cancellation requests active merge automation child cancellation.
- Merge automation cancellation requests active resolver child cancellation.
- Downstream dependency signals are satisfied only by parent terminal success.

## Full Unit Validation

Before final verification, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Validation

When Docker compose is available, run:

```bash
./tools/test_integration.sh
```

The integration suite is not expected to call external providers or require Jira/GitHub credentials.

## Final MoonSpec Verification

After implementation and tests pass, run the `/moonspec-verify` equivalent for:

```text
specs/188-merge-outcome-propagation/spec.md
```

The verification report must preserve MM-353 and classify every in-scope FR, acceptance scenario, SC, and DESIGN-REQ.
