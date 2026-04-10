# Quickstart: Step Ledger Phase 2

## Verify the feature

1. Run the focused TDD coverage:

```bash
pytest \
  tests/unit/workflows/temporal/test_step_ledger.py \
  tests/unit/workflows/temporal/workflows/test_run_step_ledger.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/workflows/temporal/workflows/test_agent_run_jules_execution.py \
  tests/unit/workflows/temporal/workflows/test_agent_run_codex_session_execution.py \
  -q
```

2. Run the full required unit suite:

```bash
./tools/test_unit.sh
```

## Manual inspection checklist

1. Start from a plan with an `agent_runtime` node.
2. Confirm the parent step row exposes `refs.childWorkflowId`, `refs.childRunId`, and `refs.taskRunId`.
3. Confirm the same row exposes grouped artifact slots for summary/output/logs/diagnostics using refs only.
4. Confirm no raw log or diagnostics body appears in the step row, Memo, or Search Attributes.
