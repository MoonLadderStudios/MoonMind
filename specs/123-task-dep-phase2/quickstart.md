# Quickstart: Task Dependencies Phase 2 - MoonMind.Run Dependency Gate

## Goal

Validate that `MoonMind.Run` enforces dependency waiting before planning, preserves replay compatibility, and fails clearly when prerequisites do not complete successfully.

## Prerequisites

- Repo checked out on branch `123-task-dep-phase2`
- Unit-test environment available through `./tools/test_unit.sh`

## Validation Steps

1. Run targeted workflow tests while iterating:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_scheduling.py
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_signals_updates.py
```

2. Run the full required unit-test command before completion:

```bash
./tools/test_unit.sh
```

3. Confirm the new workflow-boundary tests cover:
   - dependency-aware runs entering `waiting_on_dependencies`
   - empty/unpatched paths skipping the dependency gate
   - failed or canceled prerequisites producing dependency-specific failure
   - dependent-run cancellation during dependency wait

## Expected Results

- Dependency-aware workflow tests pass.
- Legacy/unpatched compatibility tests pass.
- Existing run workflow tests remain green.
- No direct `pytest` invocation is required outside the project test wrapper.
