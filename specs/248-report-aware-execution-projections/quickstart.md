# Quickstart: Report-Aware Execution Projections

## Goal

Implement and verify MM-496 by exposing a bounded report projection on execution detail responses while explicitly deferring the dedicated report endpoint.

## Focused Unit Verification

Run the focused report helper and execution router tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_report_workflow_rollout.py tests/unit/api/routers/test_executions.py
```

What this proves:
- bounded report projection helper behavior remains valid
- execution detail materialization exposes the projection shape correctly
- no-report behavior degrades safely
- unsupported projection metadata is not widened by the API layer

## Execution API Contract Verification

Run the execution-detail contract regression:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/contract/test_temporal_execution_api.py
```

What this proves:
- `/api/executions/{workflowId}` returns the new report projection contract
- projection data stays bounded to compact refs and counts only
- execution detail remains the only new surface in this story

## Full Unit Verification

Before claiming MM-496 is complete, rerun the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

Use the hermetic integration suite only if implementation changes execution persistence, artifact-link query wiring, or API serialization behavior beyond the existing unit and contract boundary coverage:

```bash
./tools/test_integration.sh
```

## End-to-End Story Validation

1. Confirm `spec.md` preserves MM-496 and the original Jira preset brief.
2. Run the focused unit verification command.
3. Run the execution API contract verification command.
4. If verification exposes drift, implement the smallest contract-preserving fix in `moonmind/schemas/temporal_models.py` and `api_service/api/routers/executions.py` while reusing `moonmind/workflows/temporal/report_artifacts.py`.
5. Rerun focused verification.
6. Run the full unit suite.
7. Preserve MM-496 and DESIGN-REQ-013/022/024 in downstream tasks and verification artifacts.
