# Quickstart: Exact Full Rerun Workflow

## Purpose

Validate MM-645 against the preserved Jira preset brief in `spec.md`: Rerun on a failed execution starts a new execution from the beginning using the original task input snapshot unchanged, records `exact_full_rerun` provenance, and imports no completed progress.

## Test-First Plan

1. Add failing frontend tests in `frontend/src/entrypoints/task-detail.test.tsx` proving the Rerun action submits directly and does not link to `/tasks/new`.
2. Add failing frontend tests or update existing `frontend/src/entrypoints/task-create.test.tsx` to confirm exact Rerun no longer depends on the authoring form path.
3. Add failing unit tests in `tests/unit/workflows/temporal/test_temporal_service.py` proving exact no-mutation rerun creates `task.recovery.kind = exact_full_rerun`, pins source workflow/run IDs, and strips resume/progress fields.
4. Add failing unit tests in `tests/unit/api/routers/test_executions.py` proving the update route persists/returns exact rerun snapshot lineage and rejects missing source identity or missing snapshot.
5. Add or update hermetic integration tests under `tests/integration/temporal/` proving the full API/service path creates a fresh exact rerun execution without completed progress import.
6. Implement the smallest changes needed to pass those tests.

## Focused Commands

Frontend iteration after dependencies are prepared:

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx frontend/src/entrypoints/task-create.test.tsx
```

Python unit iteration:

```bash
pytest tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py -q
```

Hermetic integration iteration:

```bash
pytest tests/integration/temporal -m 'integration_ci' -q --tb=short
```

Final required unit verification:

```bash
./tools/test_unit.sh
```

Final required hermetic integration verification:

```bash
./tools/test_integration.sh
```

## End-to-End Validation Scenario

1. Prepare or fixture a failed `MoonMind.Run` execution with:
   - source workflow ID,
   - source run ID,
   - authoritative original task input snapshot,
   - some completed source progress or resume checkpoint evidence.
2. Confirm task detail exposes `Rerun` when `canRerun = true`.
3. Trigger `Rerun` directly from task detail.
4. Confirm no task authoring page opens.
5. Confirm the request uses exact rerun shape with no mutation fields.
6. Confirm the created execution has `exact_full_rerun` provenance pinned to the source workflow/run IDs.
7. Confirm the original task input snapshot is reused unchanged.
8. Confirm no completed progress, preserved outputs, resume source, or resume checkpoint refs are present in the new execution.
9. Confirm `MM-645` remains present in spec, plan, tasks, verification, commit, and PR metadata.

## Expected Blocked Scenario

1. Prepare or fixture a failed execution without an authoritative task input snapshot.
2. Confirm `Rerun` is unavailable or blocked with `original_task_input_snapshot_missing` or an equivalent explicit degraded reason.
3. Confirm no new execution is created.
