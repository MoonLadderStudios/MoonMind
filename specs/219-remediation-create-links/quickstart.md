# Quickstart: Remediation Create Links

## Focused Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py
```

Expected result: remediation create tests pass, existing dependency tests pass, and task-shaped API normalization preserves `task.remediation`.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full unit suite passes.

## Integration Verification

No new compose-backed integration service is required for this slice. The persistence and API normalization behavior is covered by async SQLite service tests and FastAPI router unit tests.
