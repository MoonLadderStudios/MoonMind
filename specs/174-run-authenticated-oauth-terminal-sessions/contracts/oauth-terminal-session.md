# OAuth Terminal Session Contract

## Create Session Response

`POST /api/v1/oauth-sessions` returns:

- `session_id`
- `runtime_id`
- `profile_id`
- `status`
- `expires_at`
- `terminal_session_id`
- `terminal_bridge_id`
- `session_transport`
- `failure_reason`
- `created_at`

Credential contents are never returned.

## WebSocket

`/ws/v1/terminal/{session_id}?token=...`

Requirements:

- token resolves to an active owner of the OAuth session
- session status is active
- session has not expired
- session has a runner container
- PTY command is resolved from provider registry bootstrap command

Supported client frames:

- binary or plain text terminal input
- JSON text frame `{"type":"input","data":"..."}`
- JSON text frame `{"type":"resize","cols":120,"rows":40}`
- JSON text frame `{"type":"heartbeat"}`

Server close reasons are metadata only and must not include secrets.
