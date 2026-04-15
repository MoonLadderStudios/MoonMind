# Quickstart: Typed Temporal Activity Calls

## Targeted Validation

```bash
pytest tests/unit/workflows/temporal/test_temporal_client.py \
  tests/unit/workflows/temporal/test_typed_activity_boundaries.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/workflows/temporal/workflows/test_agent_run_jules_execution.py -q
```

## Full Unit Suite

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Suite

```bash
./tools/test_integration.sh
```

Run this when Docker is available. If `/var/run/docker.sock` is unavailable in a managed agent workspace, record that exact blocker and rely on the targeted Temporal test-worker unit coverage for this story's typed serialization path.

## Expected Evidence

- Temporal client tests prove the shared data converter contract is used.
- Typed boundary tests prove strict request validation and real Temporal round-trip serialization.
- AgentRun workflow tests prove migrated call sites pass typed request model instances.
- Agent runtime activity tests prove legacy dict payloads validate at the public edge into canonical models.
