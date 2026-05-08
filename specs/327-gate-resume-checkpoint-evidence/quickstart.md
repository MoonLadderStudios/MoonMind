# Quickstart: Gate Resume on Durable Checkpoint Evidence

Traceability: MM-633, `spec.md` FR-001 through FR-013.

## Prerequisites

- Run from the repository root.
- Use managed-agent local test mode for unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Do not require external provider credentials for the required tests.

## Test-First Validation Flow

1. Add failing unit tests for backend availability gating in `tests/unit/api/routers/test_executions.py`:
   - complete evidence enables `canResumeFromFailedStep`
   - missing snapshot disables Resume
   - missing checkpoint disables Resume
   - missing failed-step identity disables Resume
   - missing completed-step refs disables Resume
   - missing workspace checkpoint disables Resume
   - missing or mismatched plan identity disables Resume

2. Add failing service/model tests in `tests/unit/workflows/temporal/test_temporal_service.py`:
   - checkpoint source workflow/run mismatch fails before `create_execution`
   - task snapshot mismatch fails before `create_execution`
   - missing workspace checkpoint fails
   - missing plan identity fails
   - corrupted or inconsistent checkpoint payload fails with a bounded reason
   - repeated checkpoint writes are idempotent once checkpoint writing is implemented

3. Add checkpoint payload policy tests in `tests/schemas/test_temporal_payload_policy.py` or an adjacent schema test:
   - compact artifact refs pass
   - inline large or binary checkpoint payload content fails or is rejected by the chosen boundary

4. Add or update UI tests only if display behavior changes in `frontend/src/entrypoints/task-detail.test.tsx`:
   - Resume button visibility follows backend `actions.canResumeFromFailedStep`
   - unavailable reasons are displayed from backend disabled reasons

5. Add hermetic integration coverage in `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` or an adjacent `integration_ci` file:
   - valid checkpoint evidence materializes preserved prior steps and unblocks the failed step
   - invalid evidence blocks before a resumed execution is created
   - no invalid Resume path silently becomes a full rerun

## Commands

Focused Python unit iteration:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/routers/test_executions.py tests/unit/workflows/temporal/test_temporal_service.py tests/schemas/test_temporal_payload_policy.py
```

Focused UI iteration if Task Detail display changes:

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

A failed `MoonMind.Run` execution should expose Resume only after backend evidence proves:
- original task input snapshot
- pinned source workflow/run IDs
- failed-step ledger identity
- completed-step refs and state evidence
- workspace/branch/commit checkpoint
- plan identity or digest

Invalid, stale, unauthorized, corrupted, or inconsistent evidence must block Resume before execution and must not create a full rerun fallback.
