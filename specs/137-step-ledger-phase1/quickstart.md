# Quickstart: Step Ledger Phase 1

## Goal

Validate the phase-0 contract freeze and the phase-1 workflow-owned ledger implementation before any API or UI work ships.

## Commands

1. Run targeted ledger unit tests during TDD:

```bash
pytest tests/unit/workflows/temporal/test_step_ledger.py -q
pytest tests/unit/workflows/temporal/workflows/test_run_step_ledger.py -q
```

2. Run the full required unit suite for final verification:

```bash
./tools/test_unit.sh
```

## Expected Behaviors

- Contract fixture tests pass for progress and representative step rows.
- Workflow-boundary tests cover plan resolved, ready, started, waiting, reviewing, succeeded, failed, skipped, and canceled transitions.
- Query tests return latest-run ledger/progress during execution and after completion.
- No test depends on full logs or diagnostics being stored in workflow state.
