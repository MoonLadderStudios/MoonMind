# Quickstart: OAuth Terminal PTY Proxy

## Focused Unit Verification

Run bridge and OAuth router tests while iterating:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/services/temporal/runtime/test_terminal_bridge.py \
  tests/unit/api_service/api/routers/test_oauth_sessions.py \
  tests/unit/api_service/api/test_oauth_terminal_websocket.py
```

Expected evidence:
- One-time attach token is accepted once and reuse is rejected.
- OAuth terminal input frames are forwarded to the auth-runner PTY adapter.
- Auth-runner PTY output is streamed to Mission Control without persisting raw scrollback.
- Resize frames resize the PTY and persist safe dimensions.
- Heartbeat frames update liveness metadata.
- Generic Docker exec and task-terminal frames are rejected with safe close reasons.

## Full Unit Verification

Run before final verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Verification

Run when Docker is available:

```bash
./tools/test_integration.sh
```

Expected evidence:
- OAuth session workflow reaches bridge readiness.
- Mission Control attach WebSocket connects through the OAuth attach path.
- Auth-runner PTY input/output, resize, heartbeat, and cleanup behavior are covered at the API/runtime/container boundary.

If `/var/run/docker.sock` is unavailable in a managed-agent container, record the Docker blocker and include focused unit evidence in the verification report.
