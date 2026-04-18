# Contract: Settings Codex OAuth Terminal Flow

## UI Contract

### Auth action visibility

A provider profile row is OAuth-authable when:
- `runtime_id` is `codex_cli`.
- The profile is enabled or editable enough to start enrollment.
- The credential source is `oauth_volume`, or the row has enough OAuth volume defaults to create an OAuth session.

The row exposes an `Auth` action. Non-authable rows do not show the action.

### Auth action behavior

When the operator clicks `Auth`:
1. Settings calls `POST /api/v1/oauth-sessions` with selected profile metadata.
2. Settings stores the returned `session_id` and status for the row.
3. Settings opens the OAuth terminal page for the session, or exposes an operator action that opens it immediately.
4. Settings polls `GET /api/v1/oauth-sessions/{session_id}` while the session is active.
5. Settings exposes Cancel while the session is cancellable.
6. Settings exposes Retry for failed, cancelled, or expired sessions.
7. Settings exposes Finalize when the session is ready for verification/finalization.
8. On successful finalization, Settings invalidates Provider Profile query data and shows a success notice.

## API Contract

### Create OAuth Session

`POST /api/v1/oauth-sessions`

Required request fields for this story:
- `runtime_id`: `codex_cli`.
- `profile_id`: selected profile ID.
- `account_label`: safe display label.

Optional/defaulted fields:
- `volume_ref`.
- `volume_mount_path`.
- `provider_id`.
- `provider_label`.
- policy fields.

Response:
- `session_id`.
- `runtime_id`.
- `profile_id`.
- `status`.
- `session_transport`: expected `moonmind_pty_ws` for Codex interactive enrollment.
- terminal refs when available.
- sanitized `failure_reason` when failed.
- safe `profile_summary` when available.

### Terminal Attach

`POST /api/v1/oauth-sessions/{session_id}/terminal/attach`

Returns one-time attach metadata only when the session is active, attachable, owned by the requester, unexpired, and has terminal bridge refs.

### Finalize OAuth Session

`POST /api/v1/oauth-sessions/{session_id}/finalize`

Valid only from awaiting-user or verifying states. Runs verification, updates Provider Profile on success, stops runner, and signals workflow completion. On failure, stores sanitized failure state and stops runner.

### Cancel OAuth Session

`POST /api/v1/oauth-sessions/{session_id}/cancel`

Valid for active states. Transitions to cancelled and signals workflow cancellation.

### Reconnect/Retry OAuth Session

`POST /api/v1/oauth-sessions/{session_id}/reconnect`

Valid for failed, cancelled, or expired sessions. Creates a new pending session with safe metadata copied from the old session.

## Runtime Contract

- Codex OAuth bootstrap command is `codex login --device-auth`.
- Interactive Codex OAuth sessions use `session_transport = moonmind_pty_ws`.
- Auth runner mounts durable auth volume at the configured enrollment path.
- Verification result is compact and secret-free.
- OAuth terminal transport is not ordinary managed task execution transport.
