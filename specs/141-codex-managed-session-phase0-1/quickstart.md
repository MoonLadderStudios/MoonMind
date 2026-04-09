# Quickstart: codex-managed-session-phase0-1

1. Run the focused Phase 0 and Phase 1 verification:

```bash
./.venv/bin/pytest -q \
  tests/unit/workflows/temporal/workflows/test_agent_session.py \
  tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py \
  tests/unit/workflows/adapters/test_codex_session_adapter.py \
  tests/unit/api/routers/test_task_runs.py
```

2. Run the full repo unit suite:

```bash
./tools/test_unit.sh
```

3. Inspect the canonical doc and the workflow surface:

- `docs/ManagedAgents/CodexManagedSessionPlane.md`
- `moonmind/workflows/temporal/workflows/agent_session.py`

Expected outcome: the doc distinguishes truth surfaces explicitly, and workflow mutations go through typed updates with validators rather than the generic `control_action` signal.
