# Quickstart: Canonical Remediation Submissions

## Focused Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/temporal/test_temporal_service.py tests/unit/api/routers/test_executions.py
```

Expected result:

- valid remediation creation preserves `initialParameters.task.remediation`
- omitted target run IDs are resolved and persisted
- remediation links are queryable in outbound and inbound directions
- malformed remediation submissions are rejected before workflow start
- remediation creation does not create dependency prerequisites
- the remediation convenience route expands into the canonical task-shaped create contract

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full unit suite passes.

## Integration Verification

No new compose-backed integration service is required for MM-451 because the story is an already implemented create-time API/service persistence slice. Existing FastAPI router tests and Temporal execution service tests cover the boundary where the runtime behavior is accepted, normalized, validated, and persisted.
