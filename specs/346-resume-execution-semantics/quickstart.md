# Quickstart: Resume Execution Semantics

## Prerequisites

- Run from the repository root.
- Use managed-agent local test mode for unit tests:

```bash
export MOONMIND_FORCE_LOCAL_TESTS=1
```

## Unit Test Strategy

Add or update red-first unit tests for:

- Resume source validation and no-fallback behavior in `tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py`.
- Service/task contract protection in `tests/unit/workflows/temporal/test_temporal_service.py` and `tests/unit/workflows/tasks/test_task_contract.py` only if touched.
- Route-level no-edit behavior in `tests/unit/api/routers/test_executions.py` only if touched.
- Step ledger preserved provenance, preserved output refs, and fresh resumed-run evidence in `tests/unit/workflows/temporal/test_step_ledger.py` or the existing workflow test file.

Focused command:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py
```

Expected red-first checks before implementation:
- Invalid direct `resumeSource` fails before step execution.
- Workspace restoration is required before the failed step starts.
- Preserved output refs are injected into failed and downstream step inputs.
- Retried and later steps produce fresh resumed-run evidence.

## Integration Test Strategy

Add hermetic integration coverage under `tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py` and reuse `tests/integration/temporal/test_backend_resume_eligibility.py` where route/service boundaries are involved.

Focused command:

```bash
./tools/test_unit.sh tests/integration/workflows/temporal/workflows/test_run_resume_from_failed_step.py tests/integration/temporal/test_backend_resume_eligibility.py
```

Full required integration command before final verification:

```bash
./tools/test_integration.sh
```

Expected integration scenarios:
1. Valid failed-step Resume validates checkpoint identity before executing the failed step.
2. Workspace or branch checkpoint state is restored before the failed step starts.
3. Prior completed steps are preserved with source provenance and are not re-executed.
4. Preserved outputs are available to failed and downstream steps with continuous-run semantics.
5. The failed step is the first newly executed step.
6. Downstream steps continue normally after failed-step success.
7. Retried and later steps produce fresh resumed-run ledger rows, artifacts, and checkpoints.
8. Invalid restoration fails explicitly and never falls back to full rerun.

## End-to-End Story Check

After implementation and focused tests pass:

```bash
./tools/test_unit.sh
./tools/test_integration.sh
```

Then run final MoonSpec verification against:
- `specs/346-resume-execution-semantics/spec.md`
- `plan.md`
- `tasks.md`
- this quickstart
- test evidence
- preserved Jira issue key `MM-647`
