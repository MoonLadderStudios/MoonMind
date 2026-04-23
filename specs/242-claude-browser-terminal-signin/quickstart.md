# Quickstart: Claude Browser Terminal Sign-In Ceremony

## Focused Verification

Run the focused backend and frontend tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/routers/test_oauth_sessions.py tests/unit/services/temporal/runtime/test_terminal_bridge.py --ui-args frontend/src/entrypoints/mission-control.test.tsx
```

Expected:

- Claude awaiting-user OAuth session attach returns a WebSocket URL with a one-time token.
- Server metadata stores only the attach token hash and used flag.
- Claude authorization-code-like input is forwarded to the PTY.
- Safe terminal metadata excludes raw pasted input.
- The OAuth terminal UI waits for a Claude session to become attachable and then connects.

## Full Unit Verification

Before final completion:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Traceability Check

```bash
rg -n "MM-479|DESIGN-REQ-006|DESIGN-REQ-017|Claude Browser Terminal Sign-In Ceremony" specs/242-claude-browser-terminal-signin docs/tmp/jira-orchestration-inputs/MM-479-moonspec-orchestration-input.md
```
