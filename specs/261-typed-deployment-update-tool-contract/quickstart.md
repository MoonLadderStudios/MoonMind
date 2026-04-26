# Quickstart: Typed Deployment Update Tool Contract

## Targeted Unit Validation

```bash
pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py tests/unit/api/routers/test_deployment_operations.py -q
```

Expected result: deployment tool contract tests pass and existing deployment API queued-run tests continue to pass using the shared canonical tool name/version.

## Full Unit Validation

```bash
./tools/test_unit.sh
```

Expected result: required unit suite passes.

## Integration Validation

The story's integration boundary is in-process plan validation against a pinned registry snapshot. Run:

```bash
pytest tests/unit/workflows/skills/test_deployment_tool_contracts.py -q
```

Expected result: valid representative deployment update plan payload validates, while shell/path/runner override payloads fail before execution.

## Traceability Check

```bash
rg -n "MM-519|DESIGN-REQ-001|DESIGN-REQ-009|deployment.update_compose_stack" specs/261-typed-deployment-update-tool-contract moonmind/workflows/skills api_service/services tests/unit/workflows/skills tests/unit/api/routers
```

Expected result: MM-519 and source design coverage remain visible in artifacts, code, and tests.
