# Data Model: OAuth Terminal PTY Proxy

## ManagedAgentOAuthSession

Existing session row for OAuth credential enrollment.

Relevant fields:
- `session_id`: stable OAuth session identifier.
- `requested_by_user_id`: owner used for attach authorization.
- `status`: attachable only in bridge-ready, awaiting-user, or verifying states.
- `expires_at`: TTL boundary for terminal attachment.
- `container_name`: auth-runner container containing the provider login PTY.
- `terminal_session_id`: browser-visible terminal session reference.
- `terminal_bridge_id`: MoonMind terminal bridge reference.
- `session_transport`: expected to be `moonmind_pty_ws` when PTY bridge is enabled.
- `connected_at`: last terminal attachment time.
- `disconnected_at`: last terminal disconnect time.
- `metadata_json`: safe bridge metadata only.

Metadata keys used by this story:
- `terminal_attach_token_sha256`: one-time attach token digest.
- `terminal_attach_token_used`: boolean token-use guard.
- `terminal_attach_issued_at`: ISO timestamp for token issue.
- `terminal_connected_at`: ISO timestamp for accepted WebSocket connection.
- `terminal_disconnected_at`: ISO timestamp for WebSocket disconnect.
- `terminal_close_reason`: safe close reason string.
- `terminal_last_cols`: last accepted terminal column count.
- `terminal_last_rows`: last accepted terminal row count.
- `terminal_heartbeat_count`: accepted heartbeat count.
- `terminal_input_event_count`: accepted input frame count; raw input is not stored.
- `terminal_output_event_count`: streamed output chunk count; raw output is not stored.

Validation rules:
- Token digests are stored; raw attach tokens are returned only in the attach response and never persisted.
- Raw terminal input and output are not persisted in `metadata_json`.
- Expired or non-attachable sessions cannot open a PTY bridge.
- Unsupported frames close the WebSocket with a safe reason.

## OAuth Terminal Bridge

Runtime-only connection state for one attached browser terminal.

Fields:
- `session_id`
- `terminal_bridge_id`
- `owner_user_id`
- `resize_events`
- `input_event_count`
- `output_event_count`
- `heartbeat_count`
- `close_reason`

State transitions:
- `issued`: attach token created for bridge-ready session.
- `connected`: token accepted and marked used.
- `forwarding`: input/output/resize/heartbeat frames are handled.
- `closing`: close frame, disconnect, invalid frame, session expiry, or PTY failure occurs.
- `closed`: safe metadata is persisted and PTY/socket resources are closed.

## Auth Runner PTY Adapter

Runtime-only adapter for the auth-runner terminal process.

Responsibilities:
- Connect to the session's auth-runner container.
- Start or attach to the provider login process through a PTY.
- Forward accepted input bytes.
- Stream PTY output to the browser.
- Resize the PTY.
- Close socket/container resources without leaking terminal data to logs.
