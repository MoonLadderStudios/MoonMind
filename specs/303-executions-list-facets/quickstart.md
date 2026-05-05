# Quickstart: Executions List and Facet API Support for Column Filters

## Targeted Unit Tests

```bash
./tools/test_unit.sh tests/unit/api/test_executions_temporal.py
```

Focused frontend iteration after dependencies are prepared:

```bash
./tools/test_unit.sh --ui-args frontend/src/entrypoints/tasks-list.test.tsx
```

## Contract / Integration Evidence

```bash
./tools/test_unit.sh tests/contract/test_temporal_execution_api.py
```

Full final unit suite:

```bash
./tools/test_unit.sh
```

Hermetic integration suite when Docker is available:

```bash
./tools/test_integration.sh
```

## Scenario Checks

1. Request `/api/executions?source=temporal&scope=tasks&stateIn=executing,completed&targetRuntimeIn=codex_cli&sort=createdAt&sortDir=desc` and verify the generated query remains task-run and owner scoped.
2. Request `/api/executions?source=temporal&stateIn=executing&stateNotIn=failed` and verify HTTP 422 with `invalid_execution_query`.
3. Request `/api/executions/facets?source=temporal&facet=targetRuntime&stateIn=executing&targetRuntimeIn=codex_cli` and verify the target runtime filter is excluded from the facet universe while state and authorization constraints remain.
4. Simulate facet API failure in the Tasks List test harness and verify the filter control shows a current-page-values fallback notice while the table remains usable.
5. Confirm `MM-590` and DESIGN-REQ-006, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-025 are preserved in verification evidence.
