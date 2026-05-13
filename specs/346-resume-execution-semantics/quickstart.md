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
- Invalid direct `resumeSource` fails before step execution (FR-001, FR-002, FR-003, FR-012, SC-001, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006).
- Workspace restoration is required before the failed step starts (FR-006, SC-002, DESIGN-REQ-001, DESIGN-REQ-002).
- Preserved output refs are injected into failed and downstream step inputs (FR-008, SC-004, DESIGN-REQ-001, DESIGN-REQ-002).
- Retried and later steps produce fresh resumed-run evidence (FR-011, SC-005, DESIGN-REQ-002, DESIGN-REQ-003).

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
1. Valid failed-step Resume validates checkpoint identity before executing the failed step (FR-001, FR-002, FR-003, SCN-001, SC-001, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-004, DESIGN-REQ-006).
2. Workspace or branch checkpoint state is restored before the failed step starts (FR-006, SCN-002, SC-002, DESIGN-REQ-001, DESIGN-REQ-002).
3. Prior completed steps are preserved with source provenance and are not re-executed (FR-004, FR-005, FR-007, SCN-003, SC-003, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-005).
4. Preserved outputs are available to failed and downstream steps with continuous-run semantics (FR-008, SCN-004, SC-004, DESIGN-REQ-001, DESIGN-REQ-002).
5. The failed step is the first newly executed step (FR-009, SCN-005, SC-005, DESIGN-REQ-001, DESIGN-REQ-002).
6. Downstream steps continue normally after failed-step success (FR-010, SCN-005, SC-005, DESIGN-REQ-001, DESIGN-REQ-002).
7. Retried and later steps produce fresh resumed-run ledger rows, artifacts, and checkpoints (FR-011, SCN-006, SC-005, DESIGN-REQ-002, DESIGN-REQ-003).
8. Invalid restoration fails explicitly and never falls back to full rerun (FR-012, SCN-007, SC-006, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003).
9. Edited-input Resume attempts remain rejected while MM-647 traceability is preserved (FR-013, FR-014, SCN-008, SC-007, SC-008, DESIGN-REQ-004, DESIGN-REQ-018, DESIGN-REQ-024).

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
