# Quickstart: Backend-Computed Resume Eligibility

Traceability: MM-643, `spec.md` FR-001 through FR-010.

## Prerequisites

- Run commands from the repository root.
- Use managed-agent local test mode for unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Required tests must not depend on external provider credentials.

## Test-First Validation Flow

1. Add failing API unit tests in `tests/unit/api/routers/test_executions.py`:
   - Edit task, Rerun, and Resume are exposed independently from backend capability fields.
   - Missing snapshot, checkpoint, failed-step identity, completed refs, workspace checkpoint, plan identity, source workflow/run, stale evidence, and unauthorized checkpoint each produce bounded reasons.
   - Generic rerun with Resume-shaped metadata is not interpreted as Resume.
   - Resume rejects all task mutation field categories before creating work.

2. Add failing task contract and Temporal service unit tests:
   - accepted Resume produces or maps to recovery provenance with `kind=resume_from_failed_step`, `sourceWorkflowId`, and `sourceRunId`
   - accepted Resume produces or maps to the failed-step resume reference fields
   - exact rerun and edited full retry omit Resume reference fields
   - invalid evidence creates no resumed execution and does not trigger full rerun

3. Add or update Task Detail UI tests in `frontend/src/entrypoints/task-detail.test.tsx`:
   - Resume button visibility follows `actions.canResumeFromFailedStep`
   - unavailable reason copy comes from backend disabled reasons
   - local status/rerun labels do not cause Resume to appear

4. Add hermetic integration coverage:
   - execution detail returns the expected recovery action matrix for failed `MoonMind.Run` executions
   - accepted Resume carries source/run/failed-step/checkpoint/snapshot/plan evidence
   - invalid Resume evidence returns a rejection and creates no linked execution

## Commands

Focused Python unit iteration:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/workflows/temporal/test_temporal_service.py
```

Focused UI iteration:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Full unit verification before handoff:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Hermetic integration verification:

```bash
./tools/test_integration.sh
```

## End-to-End Story Check

A failed `MoonMind.Run` execution should expose Edit task, Rerun, and Resume only from backend capability fields. Resume is available only when the backend has the original task input snapshot, pinned source workflow/run IDs, ledger-identified failed step, completed-step refs, workspace checkpoint, and plan identity or digest. Any missing, stale, unauthorized, corrupted, or inconsistent evidence must produce an explicit reason and must not create a full rerun fallback.
