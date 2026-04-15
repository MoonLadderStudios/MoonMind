# Data Model

## ManagedAgentOAuthSession

Existing OAuth session persistence row.

- `session_id`: stable OAuth session ID.
- `requested_by_user_id`: owner used for API and WebSocket attach authorization.
- `status`: transport-neutral state, one of `pending`, `starting`, `bridge_ready`, `awaiting_user`, `verifying`, `registering_profile`, `succeeded`, `failed`, `cancelled`, `expired`.
- `session_transport`: `moonmind_pty_ws` when the first-party bridge is active.
- `terminal_session_id`: browser-facing terminal session ref.
- `terminal_bridge_id`: MoonMind bridge ref.
- `container_name`: auth runner container owned by this session.
- `expires_at`: TTL boundary for attach and workflow expiry decisions.
- `connected_at` / `disconnected_at`: terminal attach metadata.
- `failure_reason`: redacted terminal outcome reason when failed.

## No Raw Credentials

OAuth session rows and API responses store compact refs only. They must not store credential file contents, token values, environment dumps, or raw auth-volume listings.
