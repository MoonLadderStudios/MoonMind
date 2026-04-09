# Quickstart: Step Ledger Phase 6

## 1. Verify latest-run reconciliation in the execution router

```bash
pytest tests/unit/api/routers/test_executions.py -q
```

Focus:

- execution detail adopts the queried latest `runId`
- `progress` remains bounded and public-shape compatible

## 2. Verify public contract behavior

```bash
pytest tests/contract/test_temporal_execution_api.py -q
```

Focus:

- latest-run-only detail and steps semantics remain intact across mocked query lag
- degraded/read-repair flows still pass

## 3. Verify Mission Control latest-run artifact alignment

```bash
npm run ui:test -- frontend/src/entrypoints/task-detail.test.tsx
```

Focus:

- generic Artifacts reads switch to the latest run once the step ledger resolves

## 4. Run final unit verification

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx tests/unit/api/routers/test_executions.py tests/contract/test_temporal_execution_api.py
```
