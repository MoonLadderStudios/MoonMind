# Quickstart: Remediation Evidence Bundles

## Focused Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

Expected result:
- Python remediation context/evidence tests pass.
- Frontend unit phase invoked by the unit runner passes.

## Traceability Check

```bash
rg -n "MM-452|DESIGN-REQ-006|DESIGN-REQ-007|DESIGN-REQ-008|DESIGN-REQ-009|DESIGN-REQ-022|DESIGN-REQ-023|prepare_action_request" specs/227-remediation-evidence-bundles docs/tmp/jira-orchestration-inputs/MM-452-moonspec-orchestration-input.md moonmind/workflows/temporal/remediation_tools.py tests/unit/workflows/temporal/test_remediation_context.py
```

Expected result:
- MM-452 appears in source artifacts.
- All source design IDs are preserved.
- The pre-action target health guard is present in code and tests.

## Integration Verification

```bash
./tools/test_integration.sh
```

Expected result when Docker is available:
- Compose-backed `integration_ci` suite passes.
