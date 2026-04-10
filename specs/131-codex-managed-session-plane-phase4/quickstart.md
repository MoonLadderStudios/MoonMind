# Quickstart: codex-managed-session-plane-phase4

## Focused test loop

Run the new launcher/runtime suites while iterating:

```bash
./tools/test_unit.sh \
  tests/unit/services/temporal/runtime/test_managed_session_controller.py \
  tests/unit/services/temporal/runtime/test_codex_session_runtime.py \
  tests/unit/workflows/temporal/test_temporal_worker_runtime.py
```

## Final verification

```bash
./tools/test_unit.sh
```

## Optional manual smoke check

In a Docker-enabled worker environment:

1. Call `agent_runtime.launch_session` with a `LaunchCodexManagedSessionRequest` that points at the current MoonMind image.
2. Confirm the returned handle has `status="ready"` and a distinct `containerId`.
3. Call `agent_runtime.send_turn` with a trivial prompt like `Reply with exactly the word OK`.
4. Confirm the result is a typed `CodexManagedSessionTurnResponse`.
5. Call `agent_runtime.clear_session`, then `agent_runtime.terminate_session`.
