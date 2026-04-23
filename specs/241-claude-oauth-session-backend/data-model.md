# Data Model: Claude OAuth Session Backend

## OAuth Provider Spec

- `runtime_id`: `claude_code`.
- `auth_mode`: `oauth`.
- `session_transport`: `moonmind_pty_ws`.
- `default_volume_name`: `claude_auth_volume`.
- `default_mount_path`: `/home/app/.claude`.
- `provider_id`: `anthropic`.
- `provider_label`: `Anthropic`.
- `bootstrap_command`: ordered command parts `["claude", "login"]`.
- `success_check`: `claude_config_exists`.

Validation rules:
- Bootstrap command must be non-empty and contain no blank parts.
- Unsupported runtime IDs fail through existing registry validation.

## Managed Agent OAuth Session

- `session_id`: generated OAuth session identifier.
- `runtime_id`: `claude_code` for this story.
- `profile_id`: `claude_anthropic`.
- `volume_ref`: defaults to `claude_auth_volume`.
- `volume_mount_path`: defaults to `/home/app/.claude`.
- `session_transport`: defaults to `moonmind_pty_ws`.
- `status`: existing OAuth session lifecycle status.
- `terminal_session_id`, `terminal_bridge_id`, `container_name`: populated when the PTY-backed runner starts.

Validation rules:
- `volume_ref` and `volume_mount_path` are required for OAuth sessions.
- Active session exclusivity per profile is preserved.

## Managed Agent Provider Profile

- `profile_id`: `claude_anthropic`.
- `runtime_id`: `claude_code`.
- `provider_id`: `anthropic`.
- `provider_label`: `Anthropic`.
- `credential_source`: `oauth_volume`.
- `runtime_materialization_mode`: `oauth_home`.
- `volume_ref`: `claude_auth_volume`.
- `volume_mount_path`: `/home/app/.claude`.
- `clear_env_keys`: includes `ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `OPENAI_API_KEY`.

Validation rules:
- Profile stores refs and metadata only, never raw OAuth credential file contents.

## OAuth Auth Runner

- `container_name`: `moonmind_auth_<session_id>`.
- `runtime_id`: `claude_code`.
- `volume_ref`: `claude_auth_volume`.
- `volume_mount_path`: `/home/app/.claude`.
- `bootstrap_command`: `claude login`.
- Environment:
  - `HOME=/home/app`
  - `CLAUDE_HOME=/home/app/.claude`
  - `CLAUDE_VOLUME_PATH=/home/app/.claude`
  - `ANTHROPIC_API_KEY=`
  - `CLAUDE_API_KEY=`

Validation rules:
- Runner startup must not pass raw API-key values to the child environment.
- OAuth runner remains scoped to enrollment and repair.
