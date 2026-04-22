# Quickstart: Remediation Context Artifacts

## Focused Unit Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py
```

## Final Unit Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_remediation_context.py tests/unit/workflows/temporal/test_temporal_service.py
```

## End-to-End Scenario

1. Create a target `MoonMind.Run` execution.
2. Create a remediation `MoonMind.Run` with `task.remediation.target.workflowId` pointing to the target.
3. Run the remediation context builder for the remediation workflow ID.
4. Verify the generated artifact is complete, linked to the remediation execution, recorded on the remediation link, and contains only bounded metadata and refs.
