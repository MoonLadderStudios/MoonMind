# Quickstart: Claude OAuth Authorization and Redaction Guardrails

## Focused TDD Flow

1. Add failing MM-482 guardrail tests first:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/api_service/api/routers/test_oauth_sessions.py \
  tests/unit/api_service/api/routers/test_provider_profiles.py \
  tests/unit/services/temporal/runtime/test_terminal_bridge.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/services/temporal/runtime/test_launcher.py
```

Expected red cases before implementation:
- Claude reconnect-as-repair rejects unauthorized access before starting a successor session.
- Claude attach-token replay is denied after the first successful WebSocket attach.
- Claude OAuth session, provider-profile, verification, and launch-visible failures remain secret-free even when token-like data is present.
- Claude auth-volume metadata is treated as credential-store-only and not as a workspace or artifact root.

2. Implement the smallest production changes needed in existing boundaries:
- `api_service/api/routers/oauth_sessions.py`
- `api_service/api/routers/provider_profiles.py`
- `moonmind/utils/logging.py`
- `moonmind/workflows/temporal/runtime/terminal_bridge.py`
- `moonmind/workflows/temporal/activity_runtime.py`
- `moonmind/workflows/temporal/runtime/launcher.py`
- other shared helpers only if the new tests expose a real guardrail gap

3. Re-run focused validation until all MM-482 guardrail tests pass:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/api_service/api/routers/test_oauth_sessions.py \
  tests/unit/api_service/api/routers/test_provider_profiles.py \
  tests/unit/services/temporal/runtime/test_terminal_bridge.py \
  tests/unit/workflows/temporal/test_agent_runtime_activities.py \
  tests/unit/services/temporal/runtime/test_launcher.py
```

4. Run the full unit suite before closing implementation:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Integration Strategy

Run hermetic integration coverage only if the implementation changes compose-backed API, artifact, or worker-topology seams that the focused unit tests cannot prove safely:

```bash
./tools/test_integration.sh
```

Potential integration targets if MM-482 crosses compose-backed seams:
- OAuth session route + WebSocket lifecycle integration
- artifact/diagnostic publication around OAuth failures
- managed runtime launch paths that surface auth diagnostics

## End-to-End Verification Goal

The story is ready for `/moonspec.verify` when the focused and full validation evidence proves:
- unauthorized Claude OAuth lifecycle actions fail closed,
- attach tokens are short-lived and single-use,
- operator-visible surfaces remain secret-free,
- and Claude auth volumes are treated only as credential stores.
