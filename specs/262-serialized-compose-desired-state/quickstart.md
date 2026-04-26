# Quickstart: Serialized Compose Desired-State Execution

## Focused Unit Validation

```bash
pytest tests/unit/workflows/skills/test_deployment_update_execution.py -q
pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py -q
```

Expected coverage:
- same-stack lock rejection before side effects
- before-state capture before desired-state persistence
- desired-state persistence before Compose up
- changed-services and force-recreate command construction
- remove-orphans and wait flag construction
- verification failure returns non-success result
- runner mode is closed and deployment-controlled

## Hermetic Integration Validation

```bash
pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q
```

Expected coverage:
- `deployment.update_compose_stack` dispatches through the existing `mm.tool.execute` tool dispatcher
- the handler returns schema-compatible structured output
- lock contention is surfaced as non-retryable `DEPLOYMENT_LOCKED`

## Final Suite

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
./tools/test_integration.sh
```

If Docker is unavailable in the managed runtime, record `./tools/test_integration.sh` as blocked by missing Docker socket and keep the hermetic focused integration pytest result as local evidence.
