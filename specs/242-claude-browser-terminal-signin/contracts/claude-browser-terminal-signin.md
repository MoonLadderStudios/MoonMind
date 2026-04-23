# Contract: Claude Browser Terminal Sign-In Ceremony

## Session Readiness

`GET /api/v1/oauth-sessions/{session_id}` returns the OAuth session response used by the browser terminal page.

Required response fields for terminal attach:

```json
{
  "session_id": "oas_example",
  "runtime_id": "claude_code",
  "profile_id": "claude_anthropic",
  "status": "awaiting_user",
  "terminal_session_id": "term_oas_example",
  "terminal_bridge_id": "br_oas_example",
  "session_transport": "moonmind_pty_ws"
}
```

The browser terminal must poll this endpoint until the status is `bridge_ready`, `awaiting_user`, or `verifying` and both terminal identifiers are present.

## Attach

`POST /api/v1/oauth-sessions/{session_id}/terminal/attach`

Required behavior:

- Requires the session to belong to the current user.
- Requires status `bridge_ready`, `awaiting_user`, or `verifying`.
- Requires terminal bridge identifiers.
- Rejects expired sessions.
- Returns a one-time token and WebSocket URL.
- Stores only the token hash and used flag in session metadata.

Response shape:

```json
{
  "session_id": "oas_example",
  "terminal_session_id": "term_oas_example",
  "terminal_bridge_id": "br_oas_example",
  "websocket_url": "/api/v1/oauth-sessions/oas_example/terminal/ws?token=opaque",
  "attach_token": "opaque",
  "expires_at": "2026-04-23T00:00:00Z"
}
```

## WebSocket Frames

`/api/v1/oauth-sessions/{session_id}/terminal/ws?token={attach_token}`

Allowed browser-to-server frames:

```json
{"type":"input","data":"returned-claude-code\n"}
{"type":"resize","cols":120,"rows":36}
{"type":"heartbeat"}
{"type":"close"}
```

Required behavior:

- Input frames forward exact bytes to the Claude auth-runner PTY.
- Resize frames resize the PTY.
- Heartbeat and close frames update only bounded metadata.
- `docker_exec`, `task_terminal`, and other generic execution frames are rejected.
- Raw input content is not returned through API responses or persisted in safe metadata.
