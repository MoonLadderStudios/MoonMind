# Quickstart: Resume from Last Failed Step

## Prerequisites

- Use the active feature directory `specs/310-resume-from-last-failed-step`.
- Managed-agent local test mode should use `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Do not require raw Jira or provider credentials for this feature's unit and hermetic integration tests.

## Red-First Unit Tests

Add failing tests before implementation:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py
./tools/test_unit.sh tests/unit/workflows/temporal/workflows
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Expected initial failures:
- `canResumeFromFailedStep` is missing from execution actions.
- `POST /api/executions/{workflow_id}/resume-from-failed-step` is not registered.
- Resume request validation does not reject edited task payloads.
- Resume checkpoint model/validation is absent.
- Preserved-step provenance is absent from step rows.
- Task detail UI has only lifecycle Resume.

## Implementation Validation

After implementation, run focused tests again:

```bash
./tools/test_unit.sh tests/unit/api/routers/test_executions.py
./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py
./tools/test_unit.sh tests/unit/workflows/temporal/workflows
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx
```

Then run the full unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Tests

Add or update integration/contract coverage for the API and workflow boundary. Run:

```bash
./tools/test_integration.sh
```

If a targeted integration file is added for the resume command, run it directly during iteration, then finish with the full integration runner.

## End-to-End Story Validation

1. Seed or construct a failed `MoonMind.Run` with an authoritative original task input snapshot.
2. Provide a valid resume checkpoint that names source workflow ID, source run ID, failed step identity/attempt, preserved prior steps, prepared refs, output refs, and workspace/branch state.
3. Fetch task details and verify `actions.canResumeFromFailedStep === true` while lifecycle `canResume` remains separate.
4. Submit `POST /api/executions/{workflow_id}/resume-from-failed-step` with a bounded idempotency key.
5. Verify the response creates a new linked execution and leaves the source failed execution unchanged.
6. Fetch resumed steps and verify prior steps are preserved from the source run, while the first newly executed step is the failed source step.
7. Fetch source and resumed details and verify the `Resumed from failed step` relationship appears in related runs.
8. Repeat with missing checkpoint, plan mismatch, unauthorized checkpoint, and edited task payload values; each must fail before new step execution and must not become a full rerun.

## Traceability

Final verification must mention Jira issue `MM-602`, the preserved Jira preset brief in `spec.md`, and coverage for FR-001 through FR-012, SC-001 through SC-008, and DESIGN-REQ-001 through DESIGN-REQ-013.
