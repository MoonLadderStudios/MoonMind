# Contract: OAuth Terminal PTY Proxy

## POST `/api/v1/oauth-sessions/{session_id}/terminal/attach`

Purpose: issue a one-time terminal attachment token for an OAuth session that is ready for browser terminal attachment.

Preconditions:
- Caller owns the OAuth session.
- Session status is `bridge_ready`, `awaiting_user`, or `verifying`.
- Session is not expired.
- `terminal_session_id` and `terminal_bridge_id` are present.

Response:

```json
{
  "session_id": "oas_...",
  "terminal_session_id": "term_oas_...",
  "terminal_bridge_id": "br_oas_...",
  "websocket_url": "/api/v1/oauth-sessions/oas_.../terminal/ws?token=...",
  "attach_token": "...",
  "expires_at": "2026-04-16T12:00:00Z"
}
```

Persistence:
- Store only `terminal_attach_token_sha256`, not the raw token.
- Mark `terminal_attach_token_used` as `false` until WebSocket acceptance.

Failure behavior:
- `404` when the session does not exist or is not owned by the caller.
- `400` when the session status is not attachable.
- `410` when the session is expired.
- `409` when bridge identifiers are missing.

## WebSocket `/api/v1/oauth-sessions/{session_id}/terminal/ws?token=...`

Purpose: proxy Mission Control terminal I/O to the real auth-runner PTY for the OAuth session.

Acceptance rules:
- Token digest must match the stored one-time token digest.
- Token must not have been used.
- Session must still be attachable and unexpired.
- Session must have an auth-runner container reference and terminal bridge identifiers.

Server initial message:

```json
{
  "type": "ready",
  "session_id": "oas_...",
  "transport": "moonmind_pty_ws"
}
```

Client JSON frames:

```json
{ "type": "input", "data": "codex login\n" }
{ "type": "resize", "cols": 120, "rows": 36 }
{ "type": "heartbeat" }
{ "type": "close" }
```

Client binary messages:
- Treated as terminal input bytes for the attached auth-runner PTY.

Server JSON acknowledgements:

```json
{ "type": "input_ack", "bytes": 12 }
{ "type": "resize_ack", "cols": 120, "rows": 36 }
{ "type": "heartbeat_ack" }
{ "type": "close_ack" }
```

Server terminal output:
- PTY output is streamed to the browser as terminal data.
- Raw output must not be stored in session metadata, workflow history, logs, or artifacts.

Rejected frames:
- `exec`
- `docker_exec`
- `task_terminal`
- unknown JSON frame types
- malformed resize frames

Close metadata:
- Persist `terminal_close_reason`.
- Persist `terminal_disconnected_at`.
- Persist safe counters/dimensions only.
- Do not persist raw terminal input or output.

Security boundaries:
- The WebSocket is for OAuth terminal enrollment only.
- It must not expose generic Docker exec access.
- It must not expose ordinary task-run terminal attachment.
- It must not alter managed task execution transport.
