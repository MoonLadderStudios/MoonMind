# Quickstart: Step Ledger Phase 3

## 1. Run targeted API tests

```bash
pytest tests/unit/api/routers/test_executions.py \
  tests/contract/test_temporal_execution_api.py \
  tests/unit/api/routers/test_task_dashboard_view_model.py -q
```

## 2. Regenerate the frontend OpenAPI types

```bash
npm run generate
```

## 3. Run the full unit suite

```bash
./tools/test_unit.sh
```

## Expected verification

- `GET /api/executions/{workflowId}` returns bounded `progress` for `MoonMind.Run`.
- `GET /api/executions/{workflowId}/steps` returns the latest/current run step ledger in the frozen contract shape.
- Temporal-backed task detail compatibility payloads expose `stepsHref`.
- The generated TypeScript OpenAPI client includes `ExecutionModel.progress`, `ExecutionModel.stepsHref`, and the `/api/executions/{workflow_id}/steps` path.
