# Quickstart: DooD Workload Observability

## 1. Review generated artifacts

```bash
sed -n '1,220p' specs/155-dood-workload-observability/spec.md
sed -n '1,260p' specs/155-dood-workload-observability/plan.md
sed -n '1,220p' specs/155-dood-workload-observability/contracts/workload-observability-contract.md
```

Confirm the feature remains runtime-scoped and requires production code plus validation tests.

## 2. Implement test-first workload artifact publication

Add or update focused workload tests for:

- successful workload publishes `runtime.stdout`, `runtime.stderr`, and `runtime.diagnostics`
- failed workload publishes the same evidence
- timeout/cancel diagnostics include the final reason when available
- large stdout/stderr stays artifact-backed and result metadata remains bounded
- artifact publication failure is operator-visible

Run:

```bash
pytest tests/unit/workloads -q --tb=short
```

## 3. Validate declared output handling

Add tests for:

- existing declared outputs appear in `outputRefs`
- missing declared outputs are recorded in diagnostics
- declared output paths cannot escape the artifacts directory
- declared output classes cannot use session continuity classes

Run:

```bash
pytest tests/unit/workloads/test_workload_contract.py tests/unit/workloads/test_docker_workload_launcher.py -q --tb=short
```

## 4. Validate tool and workflow-boundary metadata

Add tests proving the normal executable tool result includes:

- `stdoutRef`
- `stderrRef`
- `diagnosticsRef`
- `outputRefs`
- compact workload metadata
- optional session association context

Also add workflow-boundary or step-ledger coverage proving the producing step owns the workload refs.

Run:

```bash
pytest tests/unit/workloads/test_workload_tool_bridge.py tests/unit/workflows/temporal -q --tb=short
```

## 5. Validate task/detail API and UI consumption

When API or UI presentation changes are made, add focused tests proving workload metadata and artifact refs are exposed through task/execution detail without presenting the workload as a managed session.

Suggested focused commands:

```bash
pytest tests/unit/api/routers/test_task_runs.py -q --tb=short
npm run ui:test -- <focused-task-detail-test-path>
```

Use the frontend command only when dashboard components are changed.

## 6. Final verification

Before completing runtime implementation, run the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result:

- Python unit suite passes.
- Frontend unit suite passes when enabled by the test runner.
- Workload artifacts remain the durable truth for success, failure, timeout, and cancel diagnostics.
