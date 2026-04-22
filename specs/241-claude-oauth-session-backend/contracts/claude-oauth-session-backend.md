# Contract: Claude OAuth Session Backend

## Provider Registry Contract

For `runtime_id = "claude_code"`, provider lookup returns:

```json
{
  "runtime_id": "claude_code",
  "auth_mode": "oauth",
  "session_transport": "moonmind_pty_ws",
  "default_volume_name": "claude_auth_volume",
  "default_mount_path": "/home/app/.claude",
  "provider_id": "anthropic",
  "provider_label": "Anthropic",
  "bootstrap_command": ["claude", "login"],
  "success_check": "claude_config_exists"
}
```

Unsupported runtime IDs continue to fail through existing registry validation.

## OAuth Session Creation Contract

`POST /api/v1/oauth-sessions`

Minimum Claude request:

```json
{
  "runtime_id": "claude_code",
  "profile_id": "claude_anthropic",
  "account_label": "Claude Anthropic OAuth"
}
```

Expected persisted/session response behavior:

- `runtime_id = "claude_code"`
- `profile_id = "claude_anthropic"`
- `volume_ref = "claude_auth_volume"` when not explicitly supplied
- `volume_mount_path = "/home/app/.claude"` when not explicitly supplied
- `session_transport = "moonmind_pty_ws"`
- Provider metadata defaults to `provider_id = "anthropic"` and `provider_label = "Anthropic"`

## Auth Runner Contract

When `oauth_session.start_auth_runner` receives a Claude request, it must call the PTY auth runner with:

```json
{
  "runtime_id": "claude_code",
  "volume_ref": "claude_auth_volume",
  "volume_mount_path": "/home/app/.claude",
  "bootstrap_command": ["claude", "login"]
}
```

The Docker runner arguments must include:

- `-v claude_auth_volume:/home/app/.claude`
- `-e HOME=/home/app`
- `-e CLAUDE_HOME=/home/app/.claude`
- `-e CLAUDE_VOLUME_PATH=/home/app/.claude`
- `-e ANTHROPIC_API_KEY=`
- `-e CLAUDE_API_KEY=`

The Docker runner arguments must not include raw ambient values for `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY`.
