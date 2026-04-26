# Quickstart: Deployment Verification, Artifacts, and Progress

## Focused Unit Validation

```bash
pytest tests/unit/workflows/skills/test_deployment_update_execution.py -q
```

Expected evidence:
- successful verification returns `SUCCEEDED` with required artifact refs
- partial verification returns `PARTIALLY_VERIFIED` and non-success tool status
- evidence payloads are recursively redacted
- audit metadata includes MM-521-relevant deployment context
- progress events contain documented lifecycle states and short messages only

## Focused Integration Validation

```bash
pytest tests/integration/temporal/test_deployment_update_execution_contract.py -q
```

Expected evidence:
- `deployment.update_compose_stack` dispatch returns structured result fields through the tool activity boundary
- artifact refs and progress metadata survive the dispatcher boundary

## Final Validation

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run `./tools/test_integration.sh` when Docker is available; otherwise record the Docker socket blocker.
