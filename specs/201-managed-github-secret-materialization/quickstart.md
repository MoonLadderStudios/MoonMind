# Quickstart: Managed GitHub Secret Materialization

## Focused Validation

Run schema and boundary tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/schemas/test_managed_session_models.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py
```

Run the full unit suite before finalizing:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run hermetic integration tests when Docker is available:

```bash
./tools/test_integration.sh
```

## Expected Evidence

- Serialized launch requests contain `githubCredential` descriptors, not raw token values.
- `agent_runtime.launch_session` passes a descriptor through the real activity invocation shape.
- Host git clone/fetch commands receive a scoped credential helper when a token resolves.
- Docker run arguments and container launch payload omit `GITHUB_TOKEN`.
- Git and launch failures redact token-like values.
- `MM-320` remains present in spec, plan, tasks, and verification output.
