# Quickstart: Docker-Out-of-Docker Phase 0 Contract Lock

## Verify the canonical docs

1. Read `docs/ManagedAgents/DockerOutOfDocker.md`.
2. Read `docs/ManagedAgents/CodexCliManagedSessions.md`.
3. Read `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`.
4. Confirm the three docs share the same glossary and preserve the tool-path boundary for Docker-backed workloads.

## Run automated validation

```bash
pytest -q tests/unit/docs/test_dood_phase0_contract.py
./tools/test_unit.sh
```

## Expected result

- The focused test passes.
- The full unit suite passes.
- The canonical DooD doc links to `docs/ManagedAgents/DockerOutOfDocker.md`.
- The session-plane and execution-model docs state that workload containers remain outside session identity unless they are true managed runtimes.
