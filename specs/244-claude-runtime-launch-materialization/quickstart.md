# Quickstart: Claude OAuth Runtime Launch Materialization

## Focused TDD Flow

1. Add failing Claude OAuth launch/session tests first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/services/temporal/runtime/test_launcher.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/agents/codex_worker/test_cli.py
```

Expected red cases before implementation:

- Claude OAuth-backed launch resolves `claude_anthropic` before runtime startup.
- The launch/session environment removes ambient `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`.
- The launch/session path sets `CLAUDE_HOME` and `CLAUDE_VOLUME_PATH` to `/home/app/.claude`.
- Safe diagnostics expose `volumeRef` and `authMountTarget` without leaking auth paths or secrets.
- Auth-volume paths are not treated as workspace or artifact publication roots.

2. Implement the smallest production changes needed in shared launch shaping:

- `moonmind/workflows/adapters/materializer.py`
- `moonmind/workflows/adapters/managed_agent_adapter.py`
- `moonmind/workflows/temporal/activity_runtime.py`
- `moonmind/workflows/temporal/runtime/launcher.py`
- Claude-specific helpers only if the new tests expose a real boundary gap

3. Re-run focused unit validation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/services/temporal/runtime/test_launcher.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/agents/codex_worker/test_cli.py
```

4. Run the full unit suite before closing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

Run hermetic integration tests only if launch, artifact, or worker-topology behavior changes in a way unit tests cannot fully prove:

```bash
./tools/test_integration.sh
```

Preferred integration file when MM-481 needs new compose-backed coverage: `tests/integration/temporal/test_claude_runtime_launch_materialization.py`.

Use integration coverage to confirm end-to-end managed runtime behavior only when the implementation changes compose-backed seams or artifact publication behavior.
